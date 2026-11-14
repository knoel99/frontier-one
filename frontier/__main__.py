"""Command line: python -m frontier {run,plan,ping,docs,tools}."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import catalog, infra, llm
from .agent import Agent, Run, load_role
from .config import load_config
from .tools import DEFAULT_TOOLS, REGISTRY


def cmd_run(a, cfg) -> int:
    task = pathlib.Path(a.task_file).read_text() if a.task_file else a.task
    if not task:
        print("give a task (argument) or --task-file", file=sys.stderr)
        return 2
    run = Run(cfg, workspace=a.workspace, allow_spend=a.allow_spend)
    role = load_role(a.role) if a.role else {}
    agent = Agent(run, a.provider or role.get("provider") or cfg.get("default_provider", "openai"),
                  a.tier or role.get("tier") or "frontier", a.reasoning or role.get("reasoning") or "high",
                  tools=a.tools.split(",") if a.tools else role.get("tools") or None,
                  instructions=role.get("instructions", ""), max_turns=a.max_turns or None, name=a.role or "main")
    print(f"run {run.run_id}: {agent.spec.label()} (tier {agent.spec.tier}); log {run.log_dir}/events.jsonl", file=sys.stderr)
    try:
        answer = agent.run_task(task)
    finally:
        if run.alive() and not a.keep_resources:
            print(f"cleanup: deleting {len(run.alive())} resource(s) left by the run", file=sys.stderr)
            print(json.dumps(infra.cleanup(run)), file=sys.stderr)
    (run.log_dir / "answer.md").write_text(answer + "\n")
    summary = {"run_id": run.run_id, "tokens": run.tokens, "sub_agents": run.agents, "main": agent.usage,
               "resources": run.resources}
    (run.log_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(answer)
    print(json.dumps(summary), file=sys.stderr)
    return 0


def cmd_plan(a, cfg) -> int:
    print(f"{'provider':10s} {'style':12s} " + "  ".join(f"{t:22s}" for t in llm.TIERS) + "  reasoning (none..max)")
    for name, p in cfg["providers"].items():
        models = [llm.resolve(cfg, name, t, "medium").model + ("" if t in p["tiers"] else " (*)") for t in llm.TIERS]
        lv = [str(llm.resolve(cfg, name, "frontier", lvl).effort_value) for lvl in llm.LEVELS]
        print(f"{name:10s} {p['style']:12s} " + "  ".join(f"{m:22s}" for m in models) + "  " + "/".join(lv))
    print("(*) tier missing: nearest tier used. Limits:", json.dumps(cfg["limits"]))
    return 0


def cmd_ping(a, cfg) -> int:
    """One tiny tool-calling request per provider and tier: checks model id, reasoning value and tool calls."""
    tool = {"name": "echo", "description": "Echo a word.", "parameters": {
        "type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]}}
    names = [a.provider] if a.provider else list(cfg["providers"])
    tiers = [a.tier] if a.tier else llm.TIERS
    bad = 0
    for name in names:
        seen = set()
        for t in tiers:
            spec = llm.resolve(cfg, name, t, a.reasoning or "low")
            if spec.model in seen:
                continue
            seen.add(spec.model)
            msgs = [{"role": "user", "content": "Call the echo tool with the word 'pong'."}]
            try:
                r = llm.chat(spec, msgs, [tool], max_tokens=2000)
                ok = any(c["name"] == "echo" for c in r.tool_calls)
                msgs += [r.raw_message] + [{"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": "pong"}
                                           for c in r.tool_calls]
                r2 = llm.chat(spec, msgs, [tool], max_tokens=2000) if ok else r
                status = ("OK  " if ok else "NO TOOL CALL") + f" tokens {r.usage} -> {r2.content[:40]!r}"
                bad += not ok
            except llm.LLMError as e:
                status, bad = f"FAIL {str(e)[:300]}", bad + 1
            print(f"{spec.label():45s} {status}")
    return 1 if bad else 0


def cmd_docs(a, cfg) -> int:
    if a.action == "list":
        print(catalog.overview())
    elif a.action == "search":
        print(catalog.search(" ".join(a.args), a.doc))
    else:
        print(catalog.show(a.args[0]))
    return 0


def cmd_call(a, cfg) -> int:
    """One tool call from a shell, for harnesses without MCP. Same tools and session ledger as the MCP server."""
    import os

    from .mcp import Server

    if a.session:
        os.environ["FRONTIER_SESSION"] = a.session
    if a.allow_spend:
        os.environ["FRONTIER_ALLOW_SPEND"] = "1"
    server = Server(cfg, state=a.state or os.environ.get("FRONTIER_STATE"))
    if a.tool == "list":
        for t in server.tool_specs():
            print(f"{t['name']:20s} {json.dumps(t['inputSchema'].get('properties', {}))[:150]}")
        return 0
    text, err = server.call(a.tool, json.loads(a.arguments or "{}"))
    print(text)
    return 1 if err else 0


def cmd_mission(a, cfg) -> int:
    from . import launcher

    if a.action == "start":
        return launcher.start(a.target, prepare_only=a.prepare_only, keep_resources=a.keep_resources,
                              max_sessions=a.max_sessions, ping=not a.no_ping)
    return launcher.status(a.target)


def cmd_harnesses(a, cfg) -> int:
    from .harnesses import HARNESSES, McpCommand, artifacts

    for name, h in HARNESSES.items():
        files = ", ".join(sorted(artifacts(McpCommand(), [name])))
        extra = f" | user-level MCP: {h.user_config}" if h.user_config else ""
        print(f"{name:12s} {h.title} [{h.kind}{', ' + h.provider if h.provider else ''}]: {files}{extra}")
    return 0


def cmd_reap(a, cfg) -> int:
    """Dead-man sweep of a mission or run state directory, e.g. from cron: */10 * * * * frontier reap <state>."""
    from . import infra

    d = pathlib.Path(a.state)
    settings = json.loads((d / "mission.json").read_text()) if (d / "mission.json").exists() else {}
    run = Run(cfg, workspace=settings.get("workspace", "."), log_dir=d, run_id=d.name, settings=settings)
    print(json.dumps(infra.reap(run), indent=1))
    return 0


def cmd_cleanup(a, cfg) -> int:
    """Delete what a finished or crashed run left alive, from its runs/<id>/resources.json."""
    d = pathlib.Path(a.run_dir)
    run = Run(cfg, workspace=".", log_dir=d, run_id=d.name)
    run.resources = json.loads((d / "resources.json").read_text())
    print(json.dumps(infra.cleanup(run), indent=1))
    return 0


def cmd_tools(a, cfg) -> int:
    for n in REGISTRY:
        tag = "all " if n in DEFAULT_TOOLS else "infra"
        print(f"[{tag}] {n:20s} {REGISTRY[n].description[:110]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m frontier")
    ap.add_argument("--config", help="providers.toml (default config/providers.toml or $FRONTIER_CONFIG)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run an agent on a task")
    r.add_argument("task", nargs="?")
    r.add_argument("--task-file")
    r.add_argument("--provider")
    r.add_argument("--tier", choices=llm.TIERS)
    r.add_argument("--reasoning", choices=llm.LEVELS)
    r.add_argument("--role", help="preset from roles/")
    r.add_argument("--tools", help="comma-separated subset (default: all)")
    r.add_argument("--workspace", default=".")
    r.add_argument("--max-turns", type=int)
    r.add_argument("--allow-spend", action="store_true", help="allow creating/changing/deleting cloud resources")
    r.add_argument("--keep-resources", action="store_true", help="do not delete what the run left alive")
    sub.add_parser("plan", help="models and reasoning values of every provider and tier")
    p = sub.add_parser("ping", help="check model ids, reasoning values and tool calls with tiny requests")
    p.add_argument("--provider")
    p.add_argument("--tier", choices=llm.TIERS)
    p.add_argument("--reasoning", choices=llm.LEVELS)
    d = sub.add_parser("docs", help="search the bundled API docs")
    d.add_argument("action", choices=["list", "search", "show"])
    d.add_argument("args", nargs="*")
    d.add_argument("--doc", default="")
    sub.add_parser("tools", help="list the tools")
    k = sub.add_parser("call", help="call one framework tool from a shell (harnesses without MCP); 'list' lists them")
    k.add_argument("tool")
    k.add_argument("arguments", nargs="?", help="JSON object")
    k.add_argument("--session", default="cli", help="session under runs/ whose resource ledger is shared (default: cli)")
    k.add_argument("--allow-spend", action="store_true")
    k.add_argument("--state", help="a mission's state directory (.frontier/); default $FRONTIER_STATE")
    ms = sub.add_parser("mission", help="prepare and run a mission (see config/mission.example.toml)")
    ms.add_argument("action", choices=["start", "status"])
    ms.add_argument("target", help="the mission .toml file")
    ms.add_argument("--prepare-only", action="store_true", help="write the workspace, do not launch the harness")
    ms.add_argument("--keep-resources", action="store_true")
    ms.add_argument("--max-sessions", type=int)
    ms.add_argument("--no-ping", action="store_true", help="do not check the LLM keys with a tiny call")
    sub.add_parser("harnesses", help="supported harnesses and the files generated for each")
    sub.add_parser("dashboard", help="live race dashboard (see frontier/dashboard.py --help)", add_help=False)
    rp = sub.add_parser("reap", help="dead-man sweep: delete what overran its max_hours or budget (for cron)")
    rp.add_argument("state")
    c = sub.add_parser("cleanup", help="delete the resources a run left alive")
    c.add_argument("run_dir")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["dashboard"]:
        from .dashboard import main as dashboard_main

        return dashboard_main(argv[1:])
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    return {"run": cmd_run, "plan": cmd_plan, "ping": cmd_ping, "docs": cmd_docs, "tools": cmd_tools,
            "cleanup": cmd_cleanup, "call": cmd_call, "mission": cmd_mission, "harnesses": cmd_harnesses,
            "reap": cmd_reap}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
