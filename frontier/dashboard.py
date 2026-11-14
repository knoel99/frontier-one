"""Race dashboard: live view of every mission under a root (default ~/.frontier/missions).

    python3 -m frontier dashboard [--root DIR] [--port 8800]      # serve http://127.0.0.1:8800, refreshes itself
    python3 -m frontier dashboard --once race.html                 # a static snapshot

Read-only: it reads the framework's ledgers (llm.jsonl, resources.json, events.jsonl, budgets.json, delivery.json)
and the harness session logs; it writes nothing in the missions.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .budget import Budget, now, parse_time, read_jsonl

ROOT_DEFAULT = pathlib.Path.home() / ".frontier" / "missions"
FEED = 300  # actions kept in the global feed


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
    if k in ("delivered", "hf_publish", "mission_prepared", "harness_setup", "session_unmetered"):
        return k.replace("_", " "), _short({x: e[x] for x in e if x not in ("t", "event", "prev", "hash", "settings")}, 140)
    return None


def _harness_actions(log: pathlib.Path, limit: int = 60) -> list[dict]:
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
    return out[-limit:]


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
            rows[a] = {"kind": kind, "agent": a, "parent": e.get("parent"), "depth": e.get("depth"),
                       "model": f"{e.get('provider')}/{e.get('model')}", "effort": e.get("reasoning"),
                       "effort_value": e.get("effort_value"), "start": e.get("t"), "end": None, "status": "running",
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
            if e.get("frontier.mode"):
                r["model"] = r["model"].split("+")[0] + "+" + e["frontier.mode"]
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
            for c in (e.get("message") or {}).get("content", []) if e.get("type") == "assistant" else []:
                if c.get("type") == "tool_use" and c.get("name") in ("Agent", "Task"):
                    inp = c.get("input") or {}
                    out.append({"kind": "native", "agent": inp.get("subagent_type") or "agent", "parent": "harness",
                                "model": inp.get("model") or "harness model", "effort": "", "start": None, "end": None,
                                "status": "native", "task": _short(inp.get("description") or inp.get("prompt", ""), 300),
                                "calls": None, "tool_calls": None, "input": None, "cached": None, "output": None,
                                "reasoning": None, "usd": None, "seconds": None, "answer": "", "session": log.stem})
    return out


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
    sessions = sorted((d / "sessions").glob("*.log")) if (d / "sessions").exists() else []
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
    jobs = [a for a in actions if a["kind"] == "tool" and a["text"].startswith(("rp_exec", "scw_create", "rp_create"))]
    delivered = json.loads((d / "delivery.json").read_text()) if (d / "delivery.json").exists() else None
    return {"name": settings.get("name", d.name), "harness": settings.get("harness"), "provider": settings.get("provider"),
            "tier": settings.get("tier"), "reasoning": settings.get("reasoning"), "status": st, "tokens": tokens,
            "series": series, "alive": alive, "jobs": jobs[-10:], "running": running, "delivered": delivered,
            "actions": actions[-120:], "subagents": subagents(events, llm, sessions)}


def snapshot(root: pathlib.Path) -> dict:
    teams = [team_state(p.parent) for p in sorted(root.glob("*/mission.json"))]
    feed = sorted(({**a, "team": t["name"]} for t in teams for a in t["actions"] if a["t"]),
                  key=lambda a: a["t"])[-FEED:]
    history = sorted(({**r, "team": t["name"]} for t in teams for r in t["subagents"]),
                     key=lambda r: r.get("start") or "", reverse=True)
    return {"time": now().isoformat(timespec="seconds"), "root": str(root), "teams": teams, "feed": feed[::-1],
            "subagents": history}


PAGE = (pathlib.Path(__file__).with_name("dashboard.html")).read_text()


class _Handler(BaseHTTPRequestHandler):
    root: pathlib.Path = ROOT_DEFAULT

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/state"):
            body, ctype = json.dumps(snapshot(self.root)).encode(), "application/json"
        else:
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
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
    srv = ThreadingHTTPServer((a.host, a.port), _Handler)
    print(f"dashboard on http://{a.host}:{a.port} for {root}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
