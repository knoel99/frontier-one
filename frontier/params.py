"""Every parameter a mission file may hold, as one table of plain data, and the check of a mission against it.

`scope` says whether a parameter is the same for every runner of a race ("race") or differs per team ("runner").
"""

from __future__ import annotations

import difflib
import math
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import llm, scoring, snapshots
from .budget import parse_time
from .config import ROOT, load_config
from .credentials import SOURCE_KINDS
from .harnesses import HARNESSES

SANDBOX_KINDS = ("none", "openshell")
# kind: (what a value must be, how to write one)
KINDS = {"str": ("a string", 'put it in quotes, e.g. "x"'), "int": ("a whole number", "write e.g. 20"),
         "float": ("a number", "write e.g. 30.0"), "bool": ("true or false", "write true or false"),
         "strs": ("a list of strings", 'write e.g. ["a", "b"]'), "numbers": ("a list of numbers", "write e.g. [1, 2]")}


@dataclass(frozen=True)
class Param:
    key: str
    kind: str
    default: Any
    scope: str
    doc: str
    choices: Callable[[], list[str]] | None = None
    minimum: float | None = None
    required: bool = False

    def __post_init__(self):
        if self.kind not in KINDS or self.scope not in ("race", "runner"):
            raise ValueError(f"{self.key}: kind must be one of {list(KINDS)} and scope race or runner")


def _harnesses() -> list[str]:
    # the names with a launch recipe: frontier/harnesses.py also writes files for harnesses nobody can launch yet
    recipes = tomllib.loads((ROOT / "config" / "harnesses.toml").read_text())
    return [*(h for h in HARNESSES if h in recipes), "auto"]


def _providers() -> list[str]:
    return list(load_config().get("providers", {}))


def _key_sources(section: str) -> tuple[Param, ...]:
    return tuple(Param(f"{section}.{k}", "str", None, "runner", f"older form of a key source: the variable holding the {section} key "
                       "(prefer [secrets])") for k in ("key_env", "token_env"))


_SNAP = snapshots.DEFAULTS
PARAMS: tuple[Param, ...] = (
    Param("mission.name", "str", "mission", "runner", "name of the mission (and of its state folder)"),
    Param("mission.subject", "str", None, "race", "file (copied into the workspace) or URL (downloaded) of the subject",
          required=True),
    Param("mission.workspace", "str", "workspace", "runner", "workspace folder, created as a new git repository if missing"),
    Param("mission.state", "str", None, "runner", "state folder; default ~/.frontier/missions/<name>"),
    Param("mission.duration_hours", "float", None, "race", "hours from the start to the deadline", minimum=0),
    Param("mission.deadline", "str", None, "race", 'deadline as an ISO time, e.g. "2026-11-21T18:00:00+01:00"'),
    Param("harness.name", "str", "auto", "runner", "harness: auto = the LLM provider's own harness, else opencode",
          choices=_harnesses),
    Param("harness.tier", "str", "frontier", "runner", "model tier of the main thread", choices=lambda: list(llm.TIERS)),
    Param("harness.reasoning", "str", "high", "runner", "reasoning level of the main thread",
          choices=lambda: list(llm.LEVELS)),
    Param("harness.max_sessions", "int", 20, "race", "the launcher restarts the harness until delivery, the deadline or "
          "a budget ends, at most this many times", minimum=1),
    Param("llm.provider", "str", None, "runner", "section of config/providers.toml", choices=_providers, required=True),
    Param("llm.budget_usd", "float", None, "race", "all LLM calls of the mission: the harness and every sub-agent",
          minimum=0),
    Param("llm.other_providers", "strs", [], "runner", "more providers usable by spawn_agent", choices=_providers),
    *_key_sources("llm"),
    Param("scaleway.budget_eur", "float", None, "race", "Scaleway budget in EUR (the section turns the service on)",
          minimum=0),
    *_key_sources("scaleway"),
    Param("runpod.budget_usd", "float", None, "race", "Runpod budget in USD (the section turns the service on)",
          minimum=0),
    *_key_sources("runpod"),
    Param("huggingface.namespace", "str", "", "race", "user or organisation; default: the token's user"),
    Param("huggingface.visibility", "str", "private", "race", "who can see what the AI publishes",
          choices=lambda: ["private", "public"]),
    Param("huggingface.prefix", "str", "", "race", "race prefix: repositories are named <prefix>-<team>-<name>"),
    Param("huggingface.collection", "str", "", "race", "title of the race's private collection; default: the prefix"),
    *_key_sources("huggingface"),
    Param("sandbox.kind", "str", "none", "race", "none, or openshell: the harness runs in an NVIDIA OpenShell sandbox",
          choices=lambda: list(SANDBOX_KINDS)),
    Param("sandbox.base_image", "str", "node:22-bookworm", "race", "base image of the sandbox"),
    Param("sandbox.build_ca_bundle", "str", None, "race", "CA of a TLS-intercepting proxy, trusted while the image builds"),
    Param("sandbox.cpu", "float", None, "race", "CPUs of the sandbox", minimum=0),
    Param("sandbox.memory", "str", None, "race", 'memory of the sandbox, e.g. "8Gi"'),
    Param("sandbox.engine", "str", "docker", "race", "container engine of the OpenShell gateway's driver"),
    Param("network.allow", "strs", [], "race", "with a sandbox: hosts the AI may read from during its research"),
    Param("network.deny", "strs", [], "race", "hosts (or host/path) removed from what the AI may reach"),
    Param("grading.criteria", "strs", ["scientific quality", "code and data",
                                       "efficiency: total spend and time, absolute and against the other teams"],
          "race", "shown to the AI as the evaluation"),
    Param("grading.weights", "numbers", None, "race", "optional weights of the criteria; default: "
          + ", ".join(f"{v:g}" for v in scoring.WEIGHTS.values()) + " for the default criteria", minimum=0),
    Param("snapshots.enabled", "bool", _SNAP["enabled"], "race", "snapshots of the workspace at time and spend milestones"),
    Param("snapshots.grace_minutes", "float", _SNAP["grace_minutes"], "race", "minutes from the notice to the snapshot",
          minimum=0),
    Param("snapshots.notice_from_minutes", "float", _SNAP["notice_from_minutes"], "race",
          "milestones before this are snapshotted silently", minimum=0),
    Param("snapshots.notice_calls", "int", _SNAP["notice_calls"], "race", "model calls that carry each notice", minimum=1),
    # at least 1: the lists go on doubling past their last value, which 0 or less never would
    Param("snapshots.time_minutes", "numbers", _SNAP["time_minutes"], "race", "time milestones in minutes", minimum=1),
    Param("snapshots.spend_usd", "numbers", _SNAP["spend_usd"], "race", "total spend milestones in USD", minimum=1),
)
BY_KEY = {p.key: p for p in PARAMS}
DEFAULTS = {p.key: p.default for p in PARAMS}
SKILL_KEYS = ("repo", "rev", "path")
SECTIONS = sorted({p.key.split(".")[0] for p in PARAMS} | {"secrets", "skills"})


def _is(kind: str, v: Any) -> bool:
    num = isinstance(v, (int, float)) and not isinstance(v, bool)
    return {"str": isinstance(v, str), "int": isinstance(v, int) and not isinstance(v, bool), "float": num,
            "bool": isinstance(v, bool), "strs": isinstance(v, list) and all(isinstance(x, str) for x in v),
            "numbers": isinstance(v, list) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)}[kind]


def _unknown(name: str, known: list[str]) -> str:
    near = difflib.get_close_matches(name, known, n=1)
    return f"did you mean {near[0]}?" if near else f"remove it (known: {', '.join(known)})"


def _check(p: Param, v: Any) -> list[str]:
    what, how = KINDS[p.kind]
    if not _is(p.kind, v):
        return [f"{p.key} = {v!r} is not {what}: {how}."]
    out = []
    items = v if isinstance(v, list) else [v]
    if bad := [x for x in items if isinstance(x, float) and not math.isfinite(x)]:
        return [f"{p.key} = {v!r} holds {bad[0]!r}, which is not a finite number: give a real value, or remove the key."]
    if p.minimum is not None and (low := [x for x in items if x < p.minimum]):
        out.append(f"{p.key} = {v!r} is below its minimum {p.minimum:g} ({low[0]!r}): give a value of at least {p.minimum:g}.")
    if p.choices and (bad := [x for x in items if x not in p.choices()]):
        out.append(f"{p.key} = {bad[0]!r} is not allowed: use one of {', '.join(p.choices())}.")
    if p.key == "mission.deadline":
        try:
            parse_time(v)
        except ValueError:
            out.append(f'{p.key} = {v!r} is not an ISO time: write e.g. "2026-11-21T18:00:00+01:00".')
    return out


def problems(m: dict) -> list[str]:
    """Every problem of a parsed mission file, each one a sentence naming the key, what is wrong and the fix."""
    out = []
    for section, body in m.items():
        if section in _providers() and section not in SECTIONS:  # a provider of other_providers: its key source only
            out += [f"{section}.{k} is not a key of a provider's section: use key_env or token_env, or [secrets]."
                    for k in (body if isinstance(body, dict) else [""]) if k not in ("key_env", "token_env")]
            out += [f"{section}.{k} = {v!r} is not a string: name the variable in quotes." for k, v in
                    (body.items() if isinstance(body, dict) else []) if k in ("key_env", "token_env") and not isinstance(v, str)]
        elif section not in SECTIONS:
            out.append(f"[{section}] is not a section of a mission file: {_unknown(section, SECTIONS)}")
        elif section == "skills":
            if not isinstance(body, list) or not all(isinstance(s, dict) for s in body):
                out.append('skills must be an array of tables: write each one as [[skills]] with repo, rev and path.')
                continue
            for i, sk in enumerate(body, 1):
                out += [f"[[skills]] #{i}: {k} = {sk[k]!r} is not a string: put it in quotes." for k in SKILL_KEYS
                        if k in sk and not isinstance(sk[k], str)]
                out += [f"[[skills]] #{i} has no {k}: add {k} = \"...\"." for k in SKILL_KEYS if k not in sk]
                out += [f"[[skills]] #{i}: {k} is not a key of a skill: {_unknown(k, list(SKILL_KEYS))}"
                        for k in sk if k not in SKILL_KEYS]
        elif not isinstance(body, dict):
            out.append(f"{section} must be a section: write it as [{section}] followed by its keys.")
        elif section == "secrets":
            for var, src in body.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
                    out.append(f"secrets.{var} is not a variable name: use letters, digits and _, e.g. OPENAI_API_KEY.")
                kind, colon, arg = src.partition(":") if isinstance(src, str) else ("", "", "")
                if not (src == "proxy" or (kind in SOURCE_KINDS and kind != "proxy" and colon and arg)):
                    out.append(f"secrets.{var} = {src!r} is not a source: write env:NAME, file:PATH, cmd:COMMAND or proxy.")
        else:
            for k, v in body.items():
                p = BY_KEY.get(f"{section}.{k}")
                out += _check(p, v) if p else [f"{section}.{k} is not a key of a mission file: "
                                               f"{_unknown(f'{section}.{k}', list(BY_KEY))}"]
    for p in PARAMS:
        section, k = p.key.split(".")
        if p.required and k not in (m.get(section) if isinstance(m.get(section), dict) else {}):
            out.append(f"{p.key} is missing: add {k} to [{section}] ({p.doc}).")
    return out
