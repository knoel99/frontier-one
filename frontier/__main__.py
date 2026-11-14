"""Command line: python -m frontier {run,plan,ping,docs,tools}."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import apidocs, llm
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
    answer = agent.run_task(task)
    (run.log_dir / "answer.md").write_text(answer + "\n")
    summary = {"run_id": run.run_id, "tokens": run.tokens, "sub_agents": run.agents, "main": agent.usage}
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
        print(apidocs.list_docs())
    elif a.action == "search":
        print(apidocs.search(" ".join(a.args), a.doc))
    else:
        print(apidocs.show(a.args[0]))
    return 0


def cmd_tools(a, cfg) -> int:
    for n in DEFAULT_TOOLS:
        print(f"{n:13s} {REGISTRY[n].description}")
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
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    return {"run": cmd_run, "plan": cmd_plan, "ping": cmd_ping, "docs": cmd_docs, "tools": cmd_tools}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
