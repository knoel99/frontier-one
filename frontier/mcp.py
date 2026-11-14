"""MCP server (stdio, newline-delimited JSON-RPC 2.0) exposing the framework's tools to any harness.

    python3 -m frontier.mcp

Every coding harness that speaks MCP (Claude Code, Codex, Gemini/Antigravity, Qwen Code, Kimi Code, Mistral Vibe,
Cursor, Copilot, OpenCode, Hermes, Goose, Crush, Cline, …) then gets:
  spawn_agent   an API sub-agent with the provider, model tier and reasoning level chosen per call
  the infra tools (offers, create, exec, delete, ledger) and the API catalog, plus frontier_plan / frontier_roles

Settings (environment of the server, set in the harness's MCP config):
  FRONTIER_WORKSPACE      workspace of the sub-agents (default: current directory)
  FRONTIER_PROVIDER       default provider of sub-agents (default: config default_provider)
  FRONTIER_ALLOW_SPEND=1  allow creating cloud resources (default: read-only)
  FRONTIER_TOOLS          comma-separated groups or tool names to expose (default: "agents,infra,budget,hf,mission")
  FRONTIER_KEEP_RESOURCES=1  do not delete the session's cloud resources when the server stops
  FRONTIER_SESSION        name of the session directory under runs/ (default: mcp-<date>-<pid>); the ledger
                          (resources.json) of an existing session is reloaded, so a restarted server keeps it
  --state DIR / FRONTIER_STATE   a mission's state directory (.frontier/): its settings, budgets and ledgers
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import threading
import traceback

from . import infra, llm
from .agent import Agent, Ctx, Run, roles_summary
from .config import load_config
from .tools import GROUPS as TOOL_GROUPS
from .tools import REGISTRY

PROTOCOL = "2025-06-18"
WATCHDOG_S = 60
# The harness has its own shell and file tools: the server exposes the rest.
GROUPS = {"agents": ["spawn_agent", "frontier_plan", "frontier_roles"], **{k: v for k, v in TOOL_GROUPS.items() if k != "base"}}
DEFAULT_GROUPS = "agents,infra,budget,hf,mission"


def _plan(cfg) -> str:
    rows = {name: {t: llm.resolve(cfg, name, t, "medium").model for t in llm.TIERS} for name in cfg["providers"]}
    return json.dumps({"tiers": llm.TIERS, "reasoning_levels": llm.LEVELS, "providers": rows,
                       "default_provider": cfg.get("default_provider")}, indent=1)


class Server:
    def __init__(self, cfg: dict | None = None, state: str | os.PathLike | None = None):
        self.cfg = cfg or load_config()
        state = state or os.environ.get("FRONTIER_STATE")
        mission = pathlib.Path(state) / "mission.json" if state else None
        if mission and mission.exists():  # a mission workspace: its settings and shared state
            settings = json.loads(mission.read_text())
            self.run = Run(self.cfg, workspace=settings["workspace"], allow_spend=settings.get("allow_spend", False),
                           run_id=pathlib.Path(state).name, log_dir=pathlib.Path(state), settings=settings)
            provider = settings.get("provider") or self.cfg.get("default_provider", "openai")
        else:
            session = os.environ.get("FRONTIER_SESSION") or f"mcp-{dt.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
            self.run = Run(self.cfg, workspace=os.environ.get("FRONTIER_WORKSPACE", os.getcwd()),
                           allow_spend=os.environ.get("FRONTIER_ALLOW_SPEND") == "1", run_id=session,
                           log_dir=pathlib.Path(state) if state else None)
            provider = os.environ.get("FRONTIER_PROVIDER") or self.cfg.get("default_provider", "openai")
        # The harness is the root of the agent tree: a placeholder agent that never calls a model.
        self.root = Agent(self.run, provider, "frontier", "medium", name="harness")
        self.root.tools = {n: REGISTRY[n] for n in REGISTRY}  # the harness may hand any tool to a sub-agent
        names: list[str] = []
        for g in (os.environ.get("FRONTIER_TOOLS") or DEFAULT_GROUPS).split(","):
            for n in GROUPS.get(g.strip(), [g.strip()]):
                if n and n not in names:
                    names.append(n)
        self.names = names

    def tool_specs(self) -> list[dict]:
        out = []
        for n in self.names:
            if n == "frontier_plan":
                out.append({"name": n, "description": "Providers, model per tier and reasoning levels available to spawn_agent.",
                            "inputSchema": {"type": "object", "properties": {}}})
            elif n == "frontier_roles":
                out.append({"name": n, "description": "Role presets for spawn_agent (name, tier, reasoning, tools, purpose).",
                            "inputSchema": {"type": "object", "properties": {}}})
            elif n in REGISTRY:
                t = REGISTRY[n]
                desc = t.description
                if n == "spawn_agent":
                    desc += (" It runs on a model API, outside this harness, and sees only what you pass it. The call "
                             "returns when the sub-agent has finished (minutes for long tasks).")
                out.append({"name": n, "description": desc, "inputSchema": t.parameters})
        return out

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        if name not in self.names:
            return f"unknown tool {name}", True
        if name == "frontier_plan":
            return _plan(self.cfg), False
        if name == "frontier_roles":
            return roles_summary(), False
        try:
            out = str(REGISTRY[name].fn(Ctx(self.run, self.root), **args))
        except Exception as e:  # noqa: BLE001
            return f"error: {type(e).__name__}: {e}", True
        self.run.log(event="mcp_call", tool=name, arguments=json.dumps(args)[:2000], output=out[:1000])
        return out, out.startswith(("error", "refused"))

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notification
            return None
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "frontier", "version": "0.3.0"},
                      "instructions": "API sub-agents with a chosen model and reasoning level (spawn_agent), and "
                                      "cloud compute on Scaleway and Runpod (infra tools, or spawn_agent role='infra')."}
        elif method == "tools/list":
            result = {"tools": self.tool_specs()}
        elif method == "tools/call":
            p = msg.get("params", {})
            text, err = self.call(p.get("name", ""), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": err}
        elif method == "ping":
            result = {}
        elif method in ("resources/list", "prompts/list"):
            result = {method.split("/")[0]: []}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def shutdown(self) -> None:
        """Outside a mission, delete what the session left; a mission keeps its resources across harness sessions
        (its watchdog, `deliver` and the launcher end them)."""
        if self.run.settings.get("workspace"):
            return
        if self.run.alive() and os.environ.get("FRONTIER_KEEP_RESOURCES") != "1":
            self.run.log(event="mcp_cleanup", result=infra.cleanup(self.run))


def _watchdog(server: Server, stop: threading.Event) -> None:
    """Delete resources past their max_hours or beyond a spent budget, even while the harness is idle."""
    while not stop.wait(WATCHDOG_S):
        try:
            infra.enforce(server.run)
        except Exception:  # noqa: BLE001 - never kill the server
            traceback.print_exc()


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python3 -m frontier.mcp")
    ap.add_argument("--state", help="state directory of a mission (its .frontier/)")
    a = ap.parse_args(argv)
    server = Server(state=a.state)
    stop = threading.Event()
    threading.Thread(target=_watchdog, args=(server, stop), daemon=True).start()
    out = sys.stdout
    sys.stdout = sys.stderr  # nothing but JSON-RPC may reach stdout
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msgs = msg if isinstance(msg, list) else [msg]
            replies = []
            for m in msgs:
                try:
                    r = server.handle(m)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    r = {"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32603, "message": "internal error"}}
                if r is not None:
                    replies.append(r)
            if replies:
                out.write(json.dumps(replies if isinstance(msg, list) else replies[0]) + "\n")
                out.flush()
    finally:
        stop.set()
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
