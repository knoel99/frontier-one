"""Race dashboard: live view of every mission under a root (default ~/.frontier/missions).

    python3 -m frontier dashboard [--root DIR] [--port 8800]      # serve http://127.0.0.1:8800, refreshes itself
    python3 -m frontier dashboard --once race.html                 # a static snapshot

With FRONTIER_DASHBOARD_TOKEN set, the token itself never travels: it signs login links (`login_link`, made by
`tools/race_vm.py link`), valid LINK_MINUTES and as often as needed within them (a reload of the link, a browser of an app
that keeps no cookie). Opening one sets a signed session cookie (SESSION_DAYS) and redirects to the page; a request that
carries a valid session passes whatever link it repeats, so a link that leaked opens nothing once it expired.
A dashboard reachable from the Internet (tools/race_vm.py) is served this way, behind HTTPS.

    python3 -m frontier dashboard --race race.toml                 # also edits, starts and stops that race (Setup tab)

With --race the dashboard can spend money, so it is never open: without FRONTIER_DASHBOARD_TOKEN it makes a token of its
own and prints a login link at start. --root then defaults to the race's root (to ~/.frontier/races/<file stem> while
the file is missing or refused, and to the new root once a save sets one). It then also writes that file and starts
and stops its launchers.

Read-only: it reads the framework's ledgers (llm.jsonl, resources.json, events.jsonl, budgets.json, delivery.json)
and the harness session logs; it writes nothing in the missions. The scientific quality of each snapshot comes from
<root>/jury/snapshots.json, {team: {snapshot commit: {"scientific_quality": x, "artifacts": y}}} (or a bare number for
the scientific quality alone), written by whoever grades the snapshots; a snapshot without a grade is shown with its time
and spend only. From the latest graded snapshot of each team, the live standings apply frontier/scoring.py: efficiency
from the spend and time so far, and the final score. A race replayed faster (tools/fake_race.py --replay) has its own
clock in <root>/clock.json: every time is then read on it (race_now), so the snapshots, durations and axes are the race's.
"""

from __future__ import annotations

import argparse
import datetime as dt
import copy
import gzip
import hashlib
import hmac
import json
import math
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import archive as archive_mod
from . import credentials, params, scoring
from . import llm as llm_mod
from . import race as race_mod
from .budget import Budget, llm_cost, now, parse_time, read_json, read_jsonl, write_json
from .config import ROOT, load_config
from .mission import STATE_ROOT as ROOT_DEFAULT
from .mission import MissionFileError, report_files

FEED = 300  # actions kept in the global feed
LINK_MINUTES = 15
SESSION_DAYS = 30


def _sign(token: str, text: str) -> str:
    return hmac.new(token.encode(), text.encode(), hashlib.sha256).hexdigest()[:32]


def login_link(base_url: str, token: str, minutes: float = LINK_MINUTES) -> str:
    """A link that opens the dashboard within `minutes`: <expiry>.<nonce>.<signature> under ?k=."""
    body = f"{int(time.time() + minutes * 60)}.{secrets.token_urlsafe(9)}"
    return f"{base_url.rstrip('/')}/?k={body}.{_sign(token, 'link:' + body)}"


def _session(token: str) -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_sign(token, 'session:' + issued)}"


def _valid_session(token: str, value: str) -> bool:
    issued, _, sig = value.partition(".")
    return (issued.isdigit() and time.time() - int(issued) < SESSION_DAYS * 86400
            and hmac.compare_digest(sig, _sign(token, "session:" + issued)))


def _short(x, n=140) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _event_text(e: dict) -> tuple[str, str] | None:
    """(kind, text) of a framework event worth showing, else None."""
    k = e.get("event")
    if k == "mcp_call":
        args = e.get("arguments", "")
        return "tool", f"{e.get('tool')} {_short(args, 120)}"
    if k == "tool_call":
        return "sub-agent tool", f"{e.get('agent', '')[:14]} {e.get('tool')} {_short(e.get('arguments', ''), 100)}"
    if k == "agent_start":
        model = f"{e.get('provider')}/{e.get('model')}@{e.get('reasoning')}"
        return "sub-agent", f"{e.get('agent')} on {model}: {_short(e.get('task', ''), 90)}"
    if k == "agent_end":
        return "sub-agent", f"{e.get('agent')} done, {e.get('usage', {}).get('usd', 0):.4f} USD"
    if k in ("session_start", "session_end"):
        extra = f", exit {e.get('code')}, {e.get('seconds')} s" if k == "session_end" else ""
        return "session", f"{k.replace('_', ' ')} {e.get('n')}{extra}"
    if k and k.startswith("resource_") or k == "watchdog_delete":
        return "cloud", f"{k.replace('_', ' ')} {_short({x: e[x] for x in e if x not in ('t', 'event', 'prev', 'hash')}, 120)}"
    if k == "snapshot":
        return "snapshot", (f"{', '.join(e.get('labels', []))}: {e.get('llm_usd', 0):.2f} USD model calls, "
                            f"{e.get('simulation_usd', 0):.2f} USD simulations")
    if k in ("delivered", "hf_publish", "mission_prepared", "harness_setup", "session_unmetered"):
        return k.replace("_", " "), _short({x: e[x] for x in e if x not in ("t", "event", "prev", "hash", "settings")}, 140)
    return None


def _harness_actions(log: pathlib.Path) -> list[dict]:
    """Actions of the harness itself, from its live JSON output (Codex --json, Claude stream-json, …)."""
    out = []
    try:
        lines = log.read_text(errors="replace").splitlines()[-2000:]
    except OSError:
        return out
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == "command_execution":
            out.append({"kind": "shell", "text": _short(it.get("command", ""), 160)})
        elif e.get("type") == "item.completed" and it.get("type") == "mcp_tool_call":
            out.append({"kind": "tool", "text": f"{it.get('server')}.{it.get('tool')}"})
        elif e.get("type") == "item.completed" and it.get("type") == "file_change":
            out.append({"kind": "edit", "text": _short([c.get("path") for c in it.get("changes", [])], 160)})
        elif e.get("type") == "item.completed" and it.get("type") == "agent_message":
            out.append({"kind": "message", "text": _short(it.get("text", ""), 160)})
        elif e.get("type") == "assistant":  # Claude Code stream-json
            for c in (e.get("message") or {}).get("content", []):
                if c.get("type") == "tool_use":
                    out.append({"kind": "tool", "text": f"{c.get('name')} {_short(c.get('input', {}), 130)}"})
                elif c.get("type") == "text" and c.get("text", "").strip():
                    out.append({"kind": "message", "text": _short(c["text"], 160)})
    return out[-60:]  # the last actions of the session


def race_clock(c: dict, real: dt.datetime) -> dt.datetime:
    """The race's time at the real time `real`, for a replayed race: SPEED race minutes per real minute at its start,
    the speed doubling every DOUBLING minutes (0: constant), until the race's end (race_end); then the real clock's pace,
    so that a finished race stays at its end instead of running away."""
    end = parse_time(c.get("race_end"))
    if end and real >= real_time(c, end):
        return end + (real - real_time(c, end))
    r = max(0.0, (real - parse_time(c["real_start"])).total_seconds() / 60)
    s0, d = float(c["speed"]), float(c.get("doubling_minutes") or 0)
    race = s0 * d / math.log(2) * (2 ** min(r / d, 20) - 1) if d else s0 * r  # capped: a clock without race_end
    return parse_time(c["race_start"]) + dt.timedelta(minutes=race)


def real_time(c: dict, race: dt.datetime) -> dt.datetime:
    """The inverse of race_clock: when the real clock reaches the race time `race`."""
    m = max(0.0, (race - parse_time(c["race_start"])).total_seconds() / 60)
    s0, d = float(c["speed"]), float(c.get("doubling_minutes") or 0)
    r = d * math.log2(1 + m * math.log(2) / (s0 * d)) if d else m / s0
    return parse_time(c["real_start"]) + dt.timedelta(minutes=r)


def race_now(root: pathlib.Path) -> dt.datetime:
    c = read_json(root / "clock.json")
    return race_clock(c, now()) if c else now()


def subagents(events: list[dict], llm: list[dict], logs: list[pathlib.Path], at: dt.datetime | None = None) -> list[dict]:
    """History of the sub-agents of a mission, like a session tracker: model, effort, duration, calls, tokens, cost.

    Frontier sub-agents (spawn_agent) come from agent_start / model_call / agent_end events and the LLM ledger;
    the harness's native sub-agents (Claude Code's Agent/Task tool) from its stream-json output, their cost being
    part of the harness's own traffic."""
    rows: dict[str, dict] = {}
    for e in events:
        k, a = e.get("event"), e.get("agent")
        if k == "agent_start" and a and not a.startswith("harness"):
            kind = "spawn_agent" if e.get("depth") else "main (API agent)"
            role = e.get("role") or a.rsplit("-", 1)[0]  # older events: the role is the id's prefix
            rows[a] = {"kind": kind, "agent": a, "role": "own instructions" if role == "sub" else role,
                       "parent": e.get("parent"), "depth": e.get("depth"),
                       "model": f"{e.get('provider')}/{e.get('model')}", "effort": e.get("reasoning"),
                       "effort_value": e.get("effort_value"), "mode": e.get("mode") or "standard",
                       "start": e.get("t"), "end": None, "status": "running",
                       "task": _short(e.get("task", ""), 300), "calls": 0, "tool_calls": 0, "input": 0, "cached": 0,
                       "output": 0, "reasoning": 0, "usd": 0.0, "answer": ""}
        elif k == "model_call" and a in rows:
            rows[a]["calls"] += 1
            rows[a]["tool_calls"] += len(e.get("tool_calls") or [])
        elif k == "agent_end" and a in rows:
            rows[a].update(end=e.get("t"), status="done", answer=_short(e.get("answer", ""), 300))
    for e in llm:
        r = rows.get(e.get("gen_ai.agent.name"))
        if r:
            r["input"] += e["gen_ai.usage.input_tokens"]
            r["cached"] += e["gen_ai.usage.cache_read.input_tokens"]
            r["output"] += e["gen_ai.usage.output_tokens"]
            r["reasoning"] += e["gen_ai.usage.reasoning.output_tokens"]
            r["usd"] = round(r["usd"] + e["frontier.cost.usd"], 6)
            if e.get("frontier.mode"):  # the mode the response says was served
                r["mode"] = e["frontier.mode"]
    for r in rows.values():
        s, f = parse_time(r["start"]), parse_time(r["end"])
        r["seconds"] = round(((f or at or now()) - s).total_seconds()) if s else None
    out = list(rows.values())
    for log in logs:  # native sub-agents of the harness
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"tool_use"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict) or not isinstance(e.get("message"), (dict, type(None))):
                continue  # a harness may print other JSON (a list, a bare string) on its own lines
            for c in (e.get("message") or {}).get("content", []) if e.get("type") == "assistant" else []:
                if c.get("type") == "tool_use" and c.get("name") in ("Agent", "Task"):
                    inp = c.get("input") or {}
                    out.append({"kind": "native", "agent": inp.get("subagent_type") or "agent",
                                "role": inp.get("subagent_type") or "agent", "parent": "harness",
                                "model": inp.get("model") or "harness model", "effort": "", "mode": "harness",
                                "start": None, "end": None,
                                "status": "native", "task": _short(inp.get("description") or inp.get("prompt", ""), 300),
                                "calls": None, "tool_calls": None, "input": None, "cached": None, "output": None,
                                "reasoning": None, "usd": None, "seconds": None, "answer": "", "session": log.stem})
    return out


def activities(name: str, settings: dict, events: list[dict], subs: list[dict], resources: list[dict]) -> list[dict]:
    """Bars of the Gantt chart: harness sessions (main thread), spawn_agent runs, cloud machines; end None = running."""
    out = []
    start = {}
    for e in events:
        if e.get("event") == "session_start":
            start[e.get("n")] = e.get("t")
        elif e.get("event") == "session_end" and e.get("n") in start:
            out.append({"team": name, "kind": "session", "provider": settings.get("provider"), "label": f"session {e['n']}",
                        "start": start.pop(e["n"]), "end": e.get("t"), "detail": f"exit {e.get('code')}"})
    for n, t in start.items():
        out.append({"team": name, "kind": "session", "provider": settings.get("provider"), "label": f"session {n}",
                    "start": t, "end": None, "detail": "running"})
    for r in subs:
        if r.get("start") and r["kind"] in ("spawn_agent", "main (API agent)"):
            kind = "sub-agent" if r["kind"] == "spawn_agent" else "session"
            out.append({"team": name, "kind": kind, "provider": r["model"].split("/")[0], "label": r["agent"],
                        "start": r["start"], "end": r["end"], "detail": f"{r['model']}@{r['effort']}, {r['usd']:.4f} USD"})
    for r in resources:
        price = r.get("eur_per_hour") or r.get("usd_per_hour")
        out.append({"team": name, "kind": "machine", "provider": r["provider"], "label": r.get("name") or r["id"],
                    "start": r.get("created"), "end": r.get("deleted_at"), "detail": f"{r.get('type')}, {price}/h"})
    return [a for a in out if a["start"]]


def runner_activity(d: pathlib.Path, resources: list[dict], at) -> list[dict]:
    """Every machine of the mission, alive or deleted, with its cost so far and the end of its activity log
    (state/runners/<id>.log: what was run on it and what it answered)."""
    out = []
    for r in resources:
        created, ended = parse_time(r.get("created")), parse_time(r.get("deleted_at"))
        hours = ((ended or at) - created).total_seconds() / 3600 if created else 0.0
        price = r.get("eur_per_hour") or r.get("usd_per_hour") or 0
        log = d / "runners" / f"{r['id']}.log"
        text = log.read_text(errors="replace") if log.exists() else ""
        out.append({"provider": r["provider"], "id": r["id"], "name": r.get("name"), "type": r.get("type"),
                    "state": r.get("state"), "created": r.get("created"), "deleted": r.get("deleted_at"),
                    "hours": round(hours, 3), "cost": round(price * hours, 4),
                    "currency": "EUR" if r.get("eur_per_hour") is not None else "USD",
                    "commands": sum(1 for ln in text.splitlines() if ln.startswith("=== ") and " exec" in ln),
                    "log_tail": "\n".join(text.splitlines()[-30:])})
    return out


def simulation_series(resources: list[dict], at) -> list[list]:
    """Cumulative spend of the rented machines over time, EUR counted at par with USD as the snapshots do: a point at
    each creation and deletion, where the slope changes, and one now."""
    spans = [(parse_time(r.get("created")), parse_time(r.get("deleted_at")) or at, r.get("eur_per_hour") or r.get("usd_per_hour") or 0)
             for r in resources if r.get("created")]
    times = sorted({t for a, b, _ in spans for t in (a, b) if t <= at} | ({at} if spans else set()))
    return [[t.isoformat(timespec="seconds"), round(sum(max(0.0, (min(b, t) - a).total_seconds()) / 3600 * p
                                                        for a, b, p in spans if a <= t), 4)] for t in times]


def team_state(d: pathlib.Path, at: dt.datetime | None = None) -> dict:
    settings = json.loads((d / "mission.json").read_text())
    b = Budget(d)
    at = at or now()
    st = b.status(at)
    llm = b.llm_entries()
    cum, series = 0.0, []
    for e in llm:
        cum += e["frontier.cost.usd"]
        series.append([e["t"], round(cum, 6)])
    tokens = {"input": sum(e["gen_ai.usage.input_tokens"] for e in llm),
              "cached": sum(e["gen_ai.usage.cache_read.input_tokens"] for e in llm),
              "output": sum(e["gen_ai.usage.output_tokens"] for e in llm),
              "reasoning": sum(e["gen_ai.usage.reasoning.output_tokens"] for e in llm)}
    events = read_jsonl(d / "events.jsonl")
    actions = []
    for e in events:
        r = _event_text(e)
        if r:
            actions.append({"t": e.get("t", ""), "kind": r[0], "text": r[1]})
    sessions = sorted((d / "sessions").glob("[0-9]*.log"), key=lambda p: int(p.stem)) if (d / "sessions").exists() else []
    running = None
    if sessions:
        starts = [e for e in events if e.get("event") == "session_start"]
        ends = {e.get("n") for e in events if e.get("event") == "session_end"}
        if starts and starts[-1].get("n") not in ends:
            running = {"n": starts[-1].get("n"), "since": starts[-1].get("t")}
        t0 = starts[-1].get("t", "") if starts else ""
        for i, a in enumerate(_harness_actions(sessions[-1])):  # harness output has no clock: stamped from its session
            actions.append({"t": f"{t0}.{i:04d}" if t0 else "", "kind": "harness " + a["kind"], "text": a["text"]})
    alive = []
    for r in b.resources():
        if r.get("state") == "deleted":
            continue
        created = parse_time(r.get("created"))
        hours = (at - created).total_seconds() / 3600 if created else 0
        alive.append({"provider": r["provider"], "id": r["id"], "type": r.get("type"), "name": r.get("name"),
                      "price_h": r.get("eur_per_hour") or r.get("usd_per_hour"), "hours": round(hours, 2),
                      "max_hours": r.get("max_hours")})
    subs = subagents(events, llm, sessions, at)
    runners = runner_activity(d, b.resources(), at)
    jobs = [a for a in actions if a["kind"] == "tool" and a["text"].startswith(("rp_exec", "scw_create", "rp_create"))]
    delivered = read_json(d / "delivery.json")
    snaps = [{"t": e["t"], "labels": e.get("labels", []), "commit": e.get("commit", ""), "minutes": e.get("minutes"),
              "llm_usd": e.get("llm_usd", 0), "simulation_usd": e.get("simulation_usd", 0)}
             for e in events if e.get("event") == "snapshot"]
    cfg = load_config()
    prov = cfg["providers"].get(settings.get("provider"), {})
    try:
        model = llm_mod.resolve(cfg, settings.get("provider"), settings.get("tier") or "frontier",
                                settings.get("reasoning") or "high").model
    except (KeyError, llm_mod.LLMError):
        model = ""
    brand = {"logos": prov.get("logos", []), "color": prov.get("color"), "dash": prov.get("dash", "")}
    from .harnesses import HARNESSES  # late: harnesses imports the agent and tool registry
    h = HARNESSES.get(settings.get("harness") or "")
    return {"name": settings.get("name", d.name), "harness": settings.get("harness"), "provider": settings.get("provider"),
            "model": model, "brand": brand, "harness_title": h.title if h else settings.get("harness"),
            "logos": brand["logos"], "sim_series": simulation_series(b.resources(), at),
            "tier": settings.get("tier"), "reasoning": settings.get("reasoning"), "status": st, "tokens": tokens,
            "series": series, "alive": alive, "jobs": jobs[-10:], "running": running, "delivered": delivered,
            "actions": actions[-120:], "subagents": subs, "runners": runners, "snapshots": snaps,
            "model_calls": [{"t": e["t"], "model": f"{e['gen_ai.provider.name']}/{e['gen_ai.request.model']}",
                             "mode": e.get("frontier.mode") or "standard", "source": e["frontier.source"],
                             "usd": e["frontier.cost.usd"]} for e in llm],
            "activities": activities(settings.get("name", d.name), settings, events, subs, b.resources())}


def snapshot(root: pathlib.Path) -> dict:
    grades = read_json(root / "jury" / "snapshots.json", {}) or {}
    at = race_now(root)
    teams = []
    for p in sorted(root.glob("*/mission.json")):
        try:
            teams.append(team_state(p.parent, at))
        except (OSError, ValueError, KeyError) as e:  # a file being rewritten: skip this team for this refresh
            print(f"dashboard: {p.parent.name}: {e}", file=sys.stderr)
    for t in teams:
        g = grades.get(t["name"], {})
        for sn in t["snapshots"]:
            v = g.get(sn["commit"])
            v = v if isinstance(v, dict) else {"scientific_quality": v}
            sn["quality"], sn["artifacts"] = v.get("scientific_quality"), v.get("artifacts")
    standings(teams)
    feed = sorted(({**a, "team": t["name"]} for t in teams for a in t["actions"] if a["t"]),
                  key=lambda a: a["t"])[-FEED:]
    history = sorted(({**r, "team": t["name"]} for t in teams for r in t["subagents"]),
                     key=lambda r: r.get("start") or "", reverse=True)
    return {"time": at.isoformat(timespec="seconds"), "root": str(root), "teams": teams, "jury": jury_state(root / "jury"),
            "feed": feed[::-1],
            "subagents": history, "activities": [a for t in teams for a in t["activities"]],
            "model_calls": [{**c, "team": t["name"]} for t in teams for c in t.get("model_calls", [])],
            "evaluate": evaluate_state(root), "panel": panel_state(root), "archive": archive_mod.latest(root)}


REPORT_MAX = 400_000  # characters of one report file sent to the page


DEFAULT_JUDGES = ("openai", "anthropic", "google")  # frontier/jury.py's default panel
JUDGE_USAGE = {"input": 30000, "cached": 15000, "output": 4000}  # one verdict's tokens, until the jury has answered once


def panel(root: pathlib.Path) -> dict:
    """The jury's panel (DIR/jury/panel.json): the providers whose frontier model judges, and each change with its race
    time. frontier/jury.py and tools/fake_race.py read it; the dashboard writes it."""
    p = read_json(root / "jury" / "panel.json", {}) or {}
    return {"judges": p.get("judges") or list(DEFAULT_JUDGES), "history": p.get("history", [])}


def set_panel(root: pathlib.Path, judges: object) -> str | None:
    """Change the panel; returns why not, or None."""
    providers = load_config()["providers"]
    if not (isinstance(judges, list) and judges and all(isinstance(j, str) and j in providers for j in judges)
            and len(set(judges)) == len(judges)):
        return "the judges are one or more distinct providers of config/providers.toml"
    p = panel(root)
    if judges == p["judges"]:
        return None
    (root / "jury").mkdir(parents=True, exist_ok=True)
    history = p["history"] or [{"at": None, "judges": p["judges"]}]  # the panel the race started with
    write_json(root / "jury" / "panel.json", {"judges": judges,
                                             "history": [*history, {"at": race_now(root).isoformat(timespec="seconds"), "judges": judges}]})
    return None


def panel_state(root: pathlib.Path) -> dict:
    """The panel, and the providers that could judge with their model and the cost of one verdict: the mean of the
    jury's calls so far, else priced on JUDGE_USAGE. The page prices an evaluation from it."""
    cfg, ledger = load_config(), read_jsonl(root / "jury" / "llm.jsonl")
    seen: dict[str, list[float]] = {}
    for e in ledger:
        seen.setdefault(e["gen_ai.agent.name"].split(":", 1)[-1], []).append(e["frontier.cost.usd"])
    candidates = []
    for prov, c in sorted(cfg["providers"].items()):
        model = c.get("tiers", {}).get("frontier")
        if not model:
            continue
        usd = sum(seen[prov]) / len(seen[prov]) if seen.get(prov) else llm_cost(prov, model, JUDGE_USAGE)[0]
        candidates.append({"provider": prov, "model": model, "logos": c.get("logos", []), "usd": round(usd, 4),
                           "observed": len(seen.get(prov, []))})
    return {**panel(root), "candidates": candidates}


def evaluate_state(root: pathlib.Path) -> dict | None:
    """On-demand grading: DIR/requests exists when something honors the requests (tools/fake_race.py --replay does;
    the framework's jury grades at the milestones only), and holds the requests not yet taken."""
    d = root / "requests"
    if not d.is_dir():
        return None
    return {"pending": sorted(read_json(f, {}).get("requested_at", "") for f in d.glob("evaluate-*.json"))}


def request_evaluation(root: pathlib.Path) -> bool:
    """Ask for a graded snapshot of every running team now; False when one is already pending or nobody honors it."""
    st = evaluate_state(root)
    if st is None or st["pending"]:
        return False
    at = race_now(root)
    write_json(root / "requests" / f"evaluate-{at.strftime('%Y%m%dT%H%M%S')}.json", {"requested_at": at.isoformat(timespec="seconds")})
    return True


def team_dir(root: pathlib.Path, name: str) -> pathlib.Path | None:
    """A team's state directory from a name the page sent: only a direct child of DIR with a mission."""
    d = root / name
    return d if re.fullmatch(r"[\w.-]+", name) and not name.startswith(".") and (d / "mission.json").is_file() else None


def reports(root: pathlib.Path, name: str) -> dict | None:
    """A team's report as it stands: its Markdown, Quarto and LaTeX sources (frontier.mission.report_files) with their
    times, and the PDFs next to them, which the page opens by their path."""
    d = team_dir(root, name)
    if d is None:
        return None
    ws = pathlib.Path(read_json(d / "mission.json", {}).get("workspace") or d / "ws")
    if not ws.is_dir():
        return {"files": [], "pdfs": []}
    clock = read_json(root / "clock.json")  # a replayed race: its files were written on its own clock

    def written(f: pathlib.Path) -> str:
        t = dt.datetime.fromtimestamp(f.stat().st_mtime, dt.UTC)
        return (race_clock(clock, t) if clock else t).isoformat(timespec="seconds")

    def compiled(f: pathlib.Path) -> dict | None:
        """When the latest PDF of a LaTeX file was compiled, and whether it is of the file's current version."""
        if f.suffix.lower() != ".tex":
            return None
        cur = pdf_cache(root, name, f.relative_to(ws).as_posix(), f)
        made = sorted(cur.parent.glob(cur.name.rsplit("-", 1)[0] + "-*.pdf"), key=lambda x: x.stat().st_mtime)
        return {"at": written(made[-1]), "current": made[-1] == cur} if made else None
    files = [{"path": f.relative_to(ws).as_posix(), "kind": f.suffix[1:].lower(), "written": written(f), "compiled": compiled(f),
              "text": f.read_text(errors="replace")[:REPORT_MAX]}
             for f in sorted(report_files(ws), key=lambda f: (f.suffix.lower() == ".tex", f.as_posix()))]
    # the team's own PDFs, and the one frontier/snapshots.py made at each milestone from the Markdown report
    pdfs = [f for f in [*ws.glob("report*.pdf"), *(ws / "report").rglob("*.pdf"), *(ws / "snapshots").glob("*.pdf")] if f.is_file()]
    return {"files": files, "pdfs": sorted(f.relative_to(ws).as_posix() for f in pdfs)}


def pdf_cache(root: pathlib.Path, name: str, path: str, src: pathlib.Path) -> pathlib.Path:
    """Where the PDF of this version of a LaTeX file is kept: a new version of the file is a new name."""
    st = src.stat()
    digest = hashlib.sha256(f"{path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:20]
    slug = re.sub(r"[^\w.-]", "_", path)
    return root / ".pdf" / name / f"{slug}-{digest}.pdf"


TEX_FILES = {".tex", ".bib", ".bst", ".sty", ".cls", ".png", ".jpg", ".jpeg", ".pdf", ".eps"}
TEX_MAX = 100_000_000  # bytes copied to compile one report
_compiling = threading.Lock()  # one pdflatex at a time on the race's machine


def tex_pdf(root: pathlib.Path, name: str, path: str) -> pathlib.Path | str:
    """The PDF of one of a team's LaTeX report files, compiled when asked with pdflatex in a scratch copy of its folder
    (no shell escape, no file read or written outside it) and kept in DIR/.pdf until the file changes. Returns the PDF
    or why there is none."""
    r = reports(root, name)
    if r is None or path not in [f["path"] for f in r["files"] if f["kind"] == "tex"]:
        return "no such LaTeX file"
    src = pathlib.Path(read_json(root / name / "mission.json", {}).get("workspace") or root / name / "ws") / path
    out = pdf_cache(root, name, path, src)
    if out.is_file():
        return out
    if not shutil.which("pdflatex"):
        return "pdflatex is not installed on the race's machine"
    with _compiling:  # a second request waits for the first: pdflatex stops at 2 minutes
        with tempfile.TemporaryDirectory() as tmp:
            size = 0
            for f in src.parent.rglob("*"):
                if f.is_file() and f.suffix.lower() in TEX_FILES:
                    size += f.stat().st_size
                    if size > TEX_MAX:
                        return "the report's folder is too large to compile here"
                    (pathlib.Path(tmp) / f.relative_to(src.parent)).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, pathlib.Path(tmp) / f.relative_to(src.parent))
            env = {**os.environ, "openin_any": "p", "openout_any": "p"}
            cmd = ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", src.name]
            for _ in range(2):  # twice: references and the table of contents
                try:
                    p = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, check=False, timeout=120)
                except subprocess.TimeoutExpired:
                    return "pdflatex took more than 2 minutes"
                if p.returncode != 0:
                    errors = [line for line in p.stdout.splitlines() if line.startswith("!")]
                    return "pdflatex failed: " + (errors[0] if errors else f"exit {p.returncode}")[:300]
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pathlib.Path(tmp) / (src.stem + ".pdf"), out)
            return out


def report_pdf(root: pathlib.Path, name: str, path: str) -> pathlib.Path | None:
    r = reports(root, name)
    if r is None or path not in r["pdfs"]:
        return None
    return pathlib.Path(read_json(root / name / "mission.json", {}).get("workspace") or root / name / "ws") / path


def jury_state(d: pathlib.Path) -> dict:
    """The jury's activity from its own directory: the calls in its ledger (llm.jsonl, agent judge:<provider>) and the
    verdicts it records (events.jsonl, event "verdict": judge, model, team, commit, label, scientific_quality,
    artifacts, seconds, usd)."""
    if not d.is_dir():
        return {"calls": 0, "usd": 0.0, "judges": [], "verdicts": []}
    cfg, llm = load_config(), read_jsonl(d / "llm.jsonl")
    judges: dict[str, dict] = {}
    for e in llm:
        prov = e["gen_ai.agent.name"].split(":", 1)[-1]
        j = judges.setdefault(prov, {"provider": prov, "model": e["gen_ai.request.model"], "calls": 0, "usd": 0.0, "last": "",
                                     "logos": cfg["providers"].get(prov, {}).get("logos", []),
                                     "color": cfg["providers"].get(prov, {}).get("color")})
        j["calls"] += 1
        j["usd"] = round(j["usd"] + e["frontier.cost.usd"], 6)
        j["last"] = max(j["last"], e["t"])
    verdicts = [e for e in read_jsonl(d / "events.jsonl") if e.get("event") == "verdict"]
    return {"calls": len(llm), "usd": round(sum(e["frontier.cost.usd"] for e in llm), 6), "cap": Budget(d).caps.get("llm_usd"),
            "judges": sorted(judges.values(), key=lambda j: j["provider"]),
            "verdicts": [{k: v for k, v in e.items() if k not in ("prev", "hash")} for e in verdicts[-400:]]}


def standings(teams: list[dict]) -> None:
    """Live scores of each team from its latest graded snapshot, with frontier/scoring.py: the efficiency on the spend
    (model calls + simulations) and the time so far, against the teams of comparable science, then the final score. Each graded
    snapshot gets the same scores against the other teams' snapshots of its milestone, for the charts over time."""
    live = {}
    for t in teams:
        last = next((x for x in reversed(t["snapshots"]) if x.get("quality") is not None), None)
        st = t["status"]
        hours = (t["delivered"] or {}).get("elapsed_hours", st.get("elapsed_hours")) or 0
        usd = st["llm"]["spent_usd"] + sum(c["spent"] for c in st["cloud"].values())  # EUR at par, as the jury does
        live[t["name"]] = {"quality": last and last["quality"], "artifacts": last and last.get("artifacts"),
                           "usd": usd, "minutes": hours * 60, "classified": last is not None}
    by_label: dict[str, dict] = {}  # every graded snapshot, scored against the other teams at the same milestone
    for t in teams:
        for x in t["snapshots"]:
            if x.get("quality") is not None and x["labels"]:
                by_label.setdefault(x["labels"][0], {})[t["name"]] = x
    for snaps in by_label.values():
        e = scoring.efficiency({n: {"quality": x["quality"], "usd": x["llm_usd"] + x["simulation_usd"],
                                    "minutes": x.get("minutes") or 0, "classified": True} for n, x in snaps.items()})
        for n, x in snaps.items():
            x["efficiency"] = e[n]["efficiency"]
            x["final"] = scoring.final(x["quality"], x.get("artifacts"), x["efficiency"])
    eff = scoring.efficiency(live)
    for t in teams:
        x = live[t["name"]]
        e = eff[t["name"]]["efficiency"] if x["quality"] is not None else None
        t["scores"] = {"science": x["quality"], "artifacts": x["artifacts"], "efficiency": e,
                       "final": None if e is None else scoring.final(x["quality"], x["artifacts"], e)}


SETUP_MAX = 256 * 1024  # bytes of a race posted by the Setup tab
_racing = threading.Lock()  # one save, start or stop at a time: a start never reads a file being replaced


def _checked(raw: dict | None, file: pathlib.Path) -> dict | None:
    try:
        return race_mod.check(copy.deepcopy(raw), file) if isinstance(raw, dict) else None
    except MissionFileError:
        return None


def setup_state(file: pathlib.Path) -> dict:
    """What the Setup tab shows: the parameters, the race file as written (null when missing) and its problems, the
    runners' status, and the framework's providers with their model per tier."""
    cfg = load_config()
    raw, problems, status = None, [], {}
    if file.exists():
        try:
            raw = tomllib.loads(file.read_text())
            status = race_mod.status(race_mod.check(copy.deepcopy(raw), file))
        except tomllib.TOMLDecodeError as e:
            problems = [f"not valid TOML: {e}"]
        except MissionFileError as e:
            problems = str(e).splitlines()[1:]  # the first line names the file
    return {"schema": params.schema(), "services": [n for n, x in credentials.services(cfg).items() if x.kind == "service"],
            "providers": {n: p.get("tiers", {}) for n, p in cfg["providers"].items()},
            "race": raw, "problems": problems, "status": status, "file": str(file)}


def _secrets(r: dict | None) -> dict[tuple[str, str], object]:
    """{(runner name, or "" for the shared [secrets], variable): source} of a parsed race."""
    out: dict[tuple[str, str], object] = {}
    runners = r.get("runners") if isinstance(r, dict) and isinstance(r.get("runners"), list) else []
    for x in [r, *runners] if isinstance(r, dict) else []:
        m = x.get("mission") if isinstance(x, dict) else None
        if isinstance(x, dict) and isinstance(x.get("secrets"), dict):
            where = "" if x is r else str(m.get("name") if isinstance(m, dict) else "")
            out.update({(where, var): src for var, src in x["secrets"].items()})
    return out


def save_setup(file: pathlib.Path, body: object) -> tuple[int, dict]:
    """Check a race posted by the page as race.load checks a file, then write it whole, as posted. From the browser a
    secret comes from env:NAME or a proxy: a file: or cmd: source runs or reads on this host, so it stays only when the
    file already holds it byte for byte in that place."""
    new = body.get("race") if isinstance(body, dict) else None
    if not isinstance(new, dict):
        return 400, {"problems": ['the body is {"race": {...}}']}
    try:
        old = tomllib.loads(file.read_text()) if file.exists() else None
    except tomllib.TOMLDecodeError:
        old = None
    held = _secrets(old)
    out = [f"{where + ': ' if where else ''}secrets.{var}: only env:NAME or proxy can be set here; a file: or cmd: source "
           "is written by hand in the race file." for (where, var), src in _secrets(new).items()
           if not (src == "proxy" or isinstance(src, str) and src.startswith("env:") or held.get((where, var)) == src)]
    try:
        checked = race_mod.check(copy.deepcopy(new), file)
        text = race_mod.dumps(new)
    except MissionFileError as e:
        out += str(e).splitlines()[1:]
    except TypeError as e:  # a value TOML has no form for, which the checks let through
        out.append(str(e))
    if out:
        return 400, {"problems": out}
    running = sorted({n for r in (_checked(old, file), checked) if r for n, x in race_mod.status(r).items() if x["running"]})
    if running:
        return 409, {"problems": [f"{', '.join(running)} running: stop the race before changing its file."]}
    tmp = file.with_name(f".{file.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text)
    os.replace(tmp, file)
    return 200, setup_state(file)


def race_action(file: pathlib.Path, action: str) -> tuple[int, dict]:
    """Start or stop the race of the file on disk; the page polls the status that follows."""
    try:
        r = race_mod.load(file)
    except FileNotFoundError:
        return 409, {"problems": ["no race file yet: save the race first."]}
    except MissionFileError as e:
        return 409 if action == "start" else 400, {"problems": str(e).splitlines()[1:] or [str(e)]}
    result = race_mod.start(r) if action == "start" else race_mod.stop(r, wait_s=0)
    return 200, {"result": result, "status": race_mod.status(r)}


WEB = pathlib.Path(__file__).with_name("web")
# One classic script sharing its globals, in this order: a top-level const or statement runs before its first use.
SCRIPTS = ("core", "charts", "overview", "teams", "analysis", "jury", "markdown", "reports", "setup", "main")


def page() -> str:
    """The dashboard as one page: web/page.html with its style, the logos and the scripts of SCRIPTS inlined."""
    js = "".join((WEB / f"{n}.js").read_text() for n in SCRIPTS)
    return ((WEB / "page.html").read_text().replace("<!--STYLE-->\n", (WEB / "style.css").read_text())
            .replace("<!--LOGOS-->", pathlib.Path(__file__).with_name("logos.svg").read_text()).replace("<!--SCRIPT-->\n", js))


PAGE = page()
# The page runs its own inline script and style only, talks to its own origin only, and is never framed.
DIAGRAMS = ROOT / "docs" / "diagrams"  # the Archify diagrams the How it works tab shows, under /diagrams/<name>
DIAGRAM = re.compile(r"/diagrams/([a-z0-9-]+\.(png|html))")
PDF = re.compile(r"/report/([\w.-]+)/(.+\.pdf)")
TEX = re.compile(r"/texpdf/([\w.-]+)/(.+)\.pdf")  # the PDF of <path>.tex: a URL ending in .pdf saves as one
DIAGRAM_CSP = "default-src 'self' 'unsafe-inline' data: blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
PDF_CSP = "frame-ancestors 'self'"  # the Results tab shows it in a frame; the browser's PDF viewer needs no more


class _Handler(BaseHTTPRequestHandler):
    root: pathlib.Path = ROOT_DEFAULT
    token: str = ""
    race_file: pathlib.Path | None = None  # --race: the Setup tab and its routes
    root_from_race = False  # no --root: the root follows the race file's
    server_version, sys_version = "frontier-dashboard", ""

    def log_message(self, *a):
        pass

    def _deny(self, why: str) -> None:
        body = f"forbidden: {why}\n".encode()
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _login(self, link: str, https: bool, session: bool) -> None:
        """A login link: check its signature and expiry (or the session the browser already has), set a session cookie
        and redirect to the page, which drops the link from the address bar."""
        expiry, _, rest = link.partition(".")
        nonce, _, sig = rest.rpartition(".")
        ok = expiry.isdigit() and int(expiry) > time.time() and hmac.compare_digest(sig, _sign(self.token, f"link:{expiry}.{nonce}"))
        if not (ok or session):
            return self._deny("this login link has expired; ask for a new one (tools/race_vm.py link)")
        # Lax, not Strict: a link opened from another site (a chat, a mail) is a cross-site navigation, and a Strict cookie
        # set on it is not sent on the redirect that follows. Lax is not sent on a cross-site POST either: the one write
        # (do_POST) stays out of other sites' reach.
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Set-Cookie", f"frontier_dashboard={_session(self.token)}; Path=/; Max-Age={SESSION_DAYS * 86400}; "
                         "HttpOnly; SameSite=Lax" + ("; Secure" if https else ""))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _session_ok(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", "")).get("frontier_dashboard")
        return not self.token or bool(cookie and _valid_session(self.token, cookie.value))

    def do_POST(self):
        """The two writes: ask for an on-demand evaluation, change the jury's panel. A session cookie (SameSite=Lax:
        never sent by a cross-site POST) and, when the browser states it, an Origin of this host."""
        origin = urllib.parse.urlsplit(self.headers.get("Origin", "")).netloc
        if not self._session_ok() or (origin and origin != self.headers.get("Host", "")):
            return self._deny("no session")
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/api/setup", "/api/race/start", "/api/race/stop"):
            return self._setup(path)
        if path == "/api/evaluate":
            ok = request_evaluation(self.root)
            return self._reply(json.dumps({"requested": ok}).encode(), "application/json", status=200 if ok else 409)
        if path == "/api/panel":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n)) if 0 < n <= 4096 else {}
            except ValueError:
                body = {}
            why = set_panel(self.root, body.get("judges") if isinstance(body, dict) else None)
            return self._reply(json.dumps({"error": why} if why else panel(self.root)).encode(), "application/json",
                               status=400 if why else 200)
        return self._deny("no such action")

    def do_GET(self):
        https = self.headers.get("X-Forwarded-Proto") == "https"  # behind Caddy (tools/race_vm.py)
        if self.token:
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get("frontier_dashboard")
            session = bool(cookie and _valid_session(self.token, cookie.value))
            link = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("k", [""])[0]
            if link:
                return self._login(link, https, session)
            if not session:
                return self._deny("open the dashboard with a login link (tools/race_vm.py link)")
        csp, cache = CSP, "no-store"
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/api/archive":
            return self._archive()
        if url.path == "/api/setup":
            return self._setup(url.path)
        if m := PDF.fullmatch(url.path):
            f = report_pdf(self.root, urllib.parse.unquote(m.group(1)), urllib.parse.unquote(m.group(2)))
            if f is None:
                return self._deny("no such report")
            # the browser's own PDF viewer draws it: nothing to compile or render on the server
            body, ctype, csp = f.read_bytes(), "application/pdf", PDF_CSP
        elif m := TEX.fullmatch(url.path):
            f = tex_pdf(self.root, urllib.parse.unquote(m.group(1)), urllib.parse.unquote(m.group(2)) + ".tex")
            if isinstance(f, str):
                return self._reply(f"no PDF: {f}\n".encode(), "text/plain; charset=utf-8", status=503)
            body, ctype, csp = f.read_bytes(), "application/pdf", PDF_CSP
        elif url.path == "/api/report":
            r = reports(self.root, urllib.parse.parse_qs(url.query).get("team", [""])[0])
            if r is None:
                return self._deny("no such team")
            body, ctype = json.dumps(r, separators=(",", ":")).encode(), "application/json"
        elif m := DIAGRAM.fullmatch(url.path):
            f = DIAGRAMS / m.group(1)
            if not f.is_file():
                return self._deny("no such diagram")
            body, ctype = f.read_bytes(), "image/png" if m.group(2) == "png" else "text/html; charset=utf-8"
            csp, cache = DIAGRAM_CSP, "private, max-age=3600"  # an Archify page runs its own viewer, fonts inlined
        elif self.path.startswith("/api/state"):
            body, ctype = json.dumps(snapshot(self.root), separators=(",", ":")).encode(), "application/json"
        else:  # with --race, a flag tells the page to ask for its Setup tab
            page_ = PAGE.replace("<script>", "<script>window.__SETUP__=1;", 1) if self.race_file else PAGE
            body, ctype = page_.encode(), "text/html; charset=utf-8"
        self._reply(body, ctype, csp, cache, https)

    def _setup(self, path: str) -> None:
        """The Setup tab's routes, behind a session (main never serves --race without a token)."""
        if not self.race_file:
            return self._reply(b"no race file: start the dashboard with --race FILE\n", "text/plain; charset=utf-8", status=404)
        if not self.token:
            return self._deny("no session")
        if self.command == "GET":
            code, out = 200, setup_state(self.race_file)
        elif path == "/api/setup":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n)) if 0 < n <= SETUP_MAX else None
            except ValueError:
                body = None
            with _racing:
                code, out = (save_setup(self.race_file, body) if body is not None else
                             (400, {"problems": [f"the body is not JSON of at most {SETUP_MAX // 1024} KiB"]}))
            if code == 200 and self.root_from_race:
                type(self).root = pathlib.Path(race_mod.load(self.race_file)["race"]["root"])
        else:
            with _racing:
                code, out = race_action(self.race_file, path.rsplit("/", 1)[1])
        self._reply(json.dumps(out, default=str).encode(), "application/json", status=code)

    def _archive(self) -> None:
        """The latest validated archive (frontier/archive.py), streamed: it can weigh hundreds of megabytes."""
        for _ in range(3):  # a newer archive may delete the one just named: take the newer
            m = archive_mod.latest(self.root)
            if not m:
                break
            try:
                src = open(self.root / ".archive" / m["archive"], "rb")  # once open, it stays readable if deleted
            except FileNotFoundError:
                continue
            with src:
                self.send_response(200)
                for k, v in (("Content-Type", "application/gzip"), ("Content-Length", str(m["bytes"])),
                             ("Content-Disposition", f'attachment; filename="{m["archive"]}"'), ("X-Archive-Sha256", m["sha256"]),
                             ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")):
                    self.send_header(k, v)
                self.end_headers()
                shutil.copyfileobj(src, self.wfile)
            return
        self._reply(b"no archive yet: one is taken at the first evaluation\n", "text/plain; charset=utf-8", status=404)

    def _reply(self, body: bytes, ctype: str, csp: str = CSP, cache: str = "no-store", https: bool = False,
               status: int = 200) -> None:
        zipped = "gzip" in self.headers.get("Accept-Encoding", "") and ctype not in ("image/png", "application/pdf")
        if zipped:  # the state of a long race is megabytes of JSON, fetched every few seconds
            body = gzip.compress(body, 5)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", csp)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if ctype == "application/pdf" else "DENY")
        if https:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Vary", "Accept-Encoding")
        if zipped:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m frontier dashboard")
    ap.add_argument("--root", help=f"directory holding the missions' state directories (default {ROOT_DEFAULT}, or the race's)")
    ap.add_argument("--race", help="the race file this dashboard edits, starts and stops (the Setup tab)")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--once", help="write a static HTML snapshot to this file and exit")
    a = ap.parse_args(argv)
    race_file = pathlib.Path(a.race).expanduser().resolve() if a.race else None
    root = pathlib.Path(a.root).expanduser() if a.root else ROOT_DEFAULT
    if race_file and not a.root:
        try:
            root = pathlib.Path(race_mod.load(race_file)["race"]["root"])
        except (OSError, MissionFileError):
            root = race_mod.RACES / race_file.stem
    if a.once:
        state = json.dumps(snapshot(root)).replace("</", "<\\/")
        pathlib.Path(a.once).write_text(PAGE.replace("<script>", f"<script>window.__STATE__={state};", 1))
        print(f"wrote {a.once}")
        return 0
    _Handler.root, _Handler.race_file, _Handler.root_from_race = root, race_file, bool(race_file and not a.root)
    _Handler.token = os.environ.get("FRONTIER_DASHBOARD_TOKEN", "")
    srv = ThreadingHTTPServer((a.host, a.port), _Handler)
    url = f"http://{a.host}:{srv.server_address[1]}"
    print(f"dashboard on {url} for {root}", file=sys.stderr)
    if race_file and not _Handler.token:  # it can start a race, so it is never open: a token of its own, as Jupyter does
        _Handler.token = secrets.token_urlsafe(24)
        print(f"open it within {LINK_MINUTES} min with {login_link(url, _Handler.token)}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
