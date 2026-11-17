"""Race dashboard: live view of every mission under a root (default ~/.frontier/missions).

    python3 -m frontier dashboard [--root DIR] [--port 8800]      # serve http://127.0.0.1:8800, refreshes itself
    python3 -m frontier dashboard --once race.html                 # a static snapshot

With FRONTIER_DASHBOARD_TOKEN set, the token itself never travels: it signs single-use login links (`login_link`, made by
`tools/race_vm.py link`), valid LINK_MINUTES. Opening one sets a signed session cookie (SESSION_DAYS) and redirects to the
page, so a link that leaked after use, or was never used in time, opens nothing. A dashboard reachable from the Internet
(tools/race_vm.py) is served this way, behind HTTPS.

Read-only: it reads the framework's ledgers (llm.jsonl, resources.json, events.jsonl, budgets.json, delivery.json)
and the harness session logs; it writes nothing in the missions. The scientific quality of each snapshot comes from
<root>/jury/snapshots.json, {team: {snapshot commit: {"scientific_quality": x, "artifacts": y}}} (or a bare number for
the scientific quality alone), written by whoever grades the snapshots; a snapshot without a grade is shown with its time
and spend only. From the latest graded snapshot of each team, the live standings apply frontier/scoring.py: efficiency
from the spend and time so far, and the final score.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sys
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import llm as llm_mod
from . import scoring
from .budget import Budget, now, parse_time, read_json, read_jsonl
from .config import load_config
from .mission import STATE_ROOT as ROOT_DEFAULT

FEED = 300  # actions kept in the global feed
LINK_MINUTES = 15
SESSION_DAYS = 30


def _sign(token: str, text: str) -> str:
    return hmac.new(token.encode(), text.encode(), hashlib.sha256).hexdigest()[:32]


def login_link(base_url: str, token: str, minutes: float = LINK_MINUTES) -> str:
    """A link that opens the dashboard once, within `minutes`: <expiry>.<nonce>.<signature> under ?k=."""
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


def subagents(events: list[dict], llm: list[dict], logs: list[pathlib.Path]) -> list[dict]:
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
        r["seconds"] = round(((f or now()) - s).total_seconds()) if s else None
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


def team_state(d: pathlib.Path) -> dict:
    settings = json.loads((d / "mission.json").read_text())
    b = Budget(d)
    st = b.status()
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
    at = now()
    alive = []
    for r in b.resources():
        if r.get("state") == "deleted":
            continue
        created = parse_time(r.get("created"))
        hours = (at - created).total_seconds() / 3600 if created else 0
        alive.append({"provider": r["provider"], "id": r["id"], "type": r.get("type"), "name": r.get("name"),
                      "price_h": r.get("eur_per_hour") or r.get("usd_per_hour"), "hours": round(hours, 2),
                      "max_hours": r.get("max_hours")})
    subs = subagents(events, llm, sessions)
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
    teams = []
    for p in sorted(root.glob("*/mission.json")):
        try:
            teams.append(team_state(p.parent))
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
    return {"time": now().isoformat(timespec="seconds"), "root": str(root), "teams": teams, "jury": jury_state(root / "jury"),
            "feed": feed[::-1],
            "subagents": history, "activities": [a for t in teams for a in t["activities"]],
            "model_calls": [{**c, "team": t["name"]} for t in teams for c in t.get("model_calls", [])]}


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


PAGE = pathlib.Path(__file__).with_name("dashboard.html").read_text().replace(
    "<!--LOGOS-->", pathlib.Path(__file__).with_name("logos.svg").read_text())
# The page runs its own inline script and style only, talks to its own origin only, and is never framed.
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class _Handler(BaseHTTPRequestHandler):
    root: pathlib.Path = ROOT_DEFAULT
    token: str = ""
    server_version, sys_version = "frontier-dashboard", ""

    def log_message(self, *a):
        pass

    used: set[str] = set()  # nonces of the login links opened by this process
    lock = threading.Lock()

    def _deny(self, why: str) -> None:
        body = f"forbidden: {why}\n".encode()
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _login(self, link: str, https: bool) -> None:
        """A single-use link: check its signature and expiry, burn its nonce, set the session cookie, redirect."""
        expiry, _, rest = link.partition(".")
        nonce, _, sig = rest.rpartition(".")
        ok = expiry.isdigit() and int(expiry) > time.time() and hmac.compare_digest(sig, _sign(self.token, f"link:{expiry}.{nonce}"))
        with self.lock:
            fresh = ok and nonce not in self.used
            self.used.add(nonce)
        if not fresh:
            return self._deny("this login link has expired or was already used; ask for a new one (tools/race_vm.py link)")
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Set-Cookie", f"frontier_dashboard={_session(self.token)}; Path=/; Max-Age={SESSION_DAYS * 86400}; "
                         "HttpOnly; SameSite=Strict" + ("; Secure" if https else ""))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        https = self.headers.get("X-Forwarded-Proto") == "https"  # behind Caddy (tools/race_vm.py)
        if self.token:
            link = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("k", [""])[0]
            if link:
                return self._login(link, https)
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get("frontier_dashboard")
            if not (cookie and _valid_session(self.token, cookie.value)):
                return self._deny("open the dashboard with a login link (tools/race_vm.py link)")
        if self.path.startswith("/api/state"):
            body, ctype = json.dumps(snapshot(self.root), separators=(",", ":")).encode(), "application/json"
        else:
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        zipped = "gzip" in self.headers.get("Accept-Encoding", "")
        if zipped:  # the state of a long race is megabytes of JSON, fetched every few seconds
            body = gzip.compress(body, 5)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
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
    ap.add_argument("--root", default=str(ROOT_DEFAULT), help="directory holding the missions' state directories")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--once", help="write a static HTML snapshot to this file and exit")
    a = ap.parse_args(argv)
    root = pathlib.Path(a.root).expanduser()
    if a.once:
        state = json.dumps(snapshot(root)).replace("</", "<\\/")
        pathlib.Path(a.once).write_text(PAGE.replace("<script>", f"<script>window.__STATE__={state};", 1))
        print(f"wrote {a.once}")
        return 0
    _Handler.root = root
    _Handler.token = os.environ.get("FRONTIER_DASHBOARD_TOKEN", "")
    srv = ThreadingHTTPServer((a.host, a.port), _Handler)
    print(f"dashboard on http://{a.host}:{a.port} for {root}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
