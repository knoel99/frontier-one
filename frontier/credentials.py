"""Credentials of a mission, whatever the provider or the place they are kept in.

What each key opens is declared, not coded: the model APIs in `[providers.*]` of config/providers.toml (`key_env`,
`base_url`, and the wire `style` that fixes the header), every other service in `[services.*]` (`env`, `hosts`,
`header`, `scheme`, non-secret `settings`). Adding a provider or a cloud is a config entry.

Where each value comes from is declared by the mission, in `[secrets]` (variable = source):

    env:NAME     the host's environment variable NAME (default: the variable itself)
    file:PATH    the content of a file, e.g. /run/secrets/<name> (Docker, Kubernetes) or a systemd credential
    cmd:COMMAND  the output of a command: any secret manager's CLI (scw, aws, gcloud, vault, op, sops, ...)
    proxy        no value here: an outer proxy injects it into the requests to the service's hosts (a Claude Code
                 cloud session, a corporate egress proxy); the code sends a placeholder

The launcher resolves the sources once, on the host, and gives every value its standard variable name, so that every
reader (the harness, sub-agents, cloud calls) finds it there. A key with no value is sent as a placeholder, as with
`proxy`, but a service that needs one declared (publishing) stays closed. Values are never
written to the workspace or to the state directory: only where each one came from.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import urllib.parse
from dataclasses import dataclass, field

PLACEHOLDER = "proxy-injected"  # sent when the value is not here: a proxy replaces it
CMD_TIMEOUT_S = 60
# The header a model API reads its key from, by wire style (config/providers.toml `style`).
STYLE_AUTH = {"anthropic": ("x-api-key", ""), "gemini": ("x-goog-api-key", "")}


@dataclass(frozen=True)
class Service:
    name: str
    env: str                      # standard variable holding the key
    hosts: tuple[str, ...]        # where the key is sent
    header: str = "authorization"
    scheme: str = "Bearer"        # "" for a header that holds the bare key
    settings: tuple[str, ...] = field(default=())  # non-secret variables of the service (project id, region)
    kind: str = "service"         # "llm" for a model API


def services(cfg: dict) -> dict[str, Service]:
    """Every service a key can open: the model APIs of [providers.*] and the services of [services.*]."""
    out: dict[str, Service] = {}
    for name, p in cfg.get("providers", {}).items():
        header, scheme = STYLE_AUTH.get(p.get("style", ""), ("authorization", "Bearer"))
        out[name] = Service(name, p["key_env"], (urllib.parse.urlsplit(p["base_url"]).hostname,),
                            p.get("header", header), p.get("scheme", scheme), kind="llm")
    for name, s in cfg.get("services", {}).items():
        out[name] = Service(name, s["env"], tuple(s["hosts"]), s.get("header", "authorization").lower(),
                            s.get("scheme", "Bearer"), tuple(s.get("settings", [])))
    return out


def used(m: dict, cfg: dict) -> list[Service]:
    """The services a mission uses: its model providers, and every service that has a section in the mission."""
    svc = services(cfg)
    llm = m.get("llm", {})
    names = [p for p in [llm.get("provider"), *llm.get("other_providers", [])] if p]
    names += [n for n, s in svc.items() if s.kind == "service" and n in m]
    return [svc[n] for n in dict.fromkeys(names) if n in svc]


def sources(m: dict, cfg: dict) -> dict[str, str]:
    """{standard variable: source} for every key and setting the mission uses."""
    out: dict[str, str] = {}
    main = m.get("llm", {}).get("provider")
    for s in used(m, cfg):
        section = m.get("llm", {}) if s.name == main else m.get(s.name, {})
        own = section.get("key_env") or section.get("token_env")  # older form: the name of another variable
        out[s.env] = f"env:{own}" if own else f"env:{s.env}"
        out.update({v: f"env:{v}" for v in s.settings})
    out.update(m.get("secrets", {}))
    return out


def resolve(source: str) -> str | None:
    """The value a source gives, or None (unset, empty, or `proxy`). A failing command or file raises ValueError."""
    kind, _, arg = source.partition(":")
    if kind == "proxy":
        return None
    if kind == "env":
        return os.environ.get(arg) or None
    if kind == "file":
        try:
            return pathlib.Path(arg).expanduser().read_text().strip() or None
        except OSError as e:
            raise ValueError(f"{source}: {e.strerror}") from e
    if kind == "cmd":
        try:  # the owner's own command line, pipes included
            p = subprocess.run(arg, shell=True, capture_output=True, text=True, timeout=CMD_TIMEOUT_S,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"cmd: {type(e).__name__}") from e
        if p.returncode != 0:  # the command's own message only: it never holds the value
            raise ValueError(f"cmd exited {p.returncode}: {p.stderr.strip()[:200]}")
        return p.stdout.strip() or None
    raise ValueError(f"unknown source {source!r}: use env:NAME, file:PATH, cmd:COMMAND or proxy")


def load(m: dict, cfg: dict) -> dict[str, str]:
    """Resolve the mission's sources into the standard variables of this process (and of its children).
    Returns where each one came from, never a value: {variable: "env" | "file" | "cmd" | "proxy" | "absent" | "error: ..."}."""
    origin: dict[str, str] = {}
    for var, src in sources(m, cfg).items():
        try:
            value = resolve(src)
        except ValueError as e:
            origin[var] = f"error: {e}"
            continue
        if value is None:  # sent as a placeholder: `proxy` when the mission says a proxy injects it, else `absent`
            origin[var] = "proxy" if src == "proxy" else "absent"
            continue
        os.environ[var] = value
        origin[var] = src.partition(":")[0]
    return origin


def plain_settings(m: dict, cfg: dict) -> dict[str, str]:
    """The non-secret settings of the services in use, as resolved: recorded in mission.json, so that a session
    inside a sandbox, where no source can be read, gets them too."""
    names = {v for s in used(m, cfg) for v in s.settings}
    return {v: os.environ[v] for v in sorted(names) if os.environ.get(v)}


def auth_header(name: str, cfg: dict | None = None) -> dict[str, str]:
    """The header that carries the key of service `name`: the value, or a placeholder for a proxy to replace."""
    from .config import load_config

    s = services(cfg or load_config())[name]
    value = os.environ.get(s.env) or PLACEHOLDER
    return {s.header: f"{s.scheme} {value}" if s.scheme else value}


def available(origin: dict[str, str], var: str) -> bool:
    """A key this mission can use: it has a value, or the mission declared that a proxy injects it."""
    return origin.get(var) in ("env", "file", "cmd", "proxy")
