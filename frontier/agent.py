"""The agent loop, the run (budget shared by an agent tree) and the spawn_agent tool."""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import pathlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import llm
from .budget import Budget, append_chained, now, worst_case_usd, write_json
from .config import ROOT
from .tools import GROUPS, INFRA_TOOLS, REGISTRY, Tool, _obj, all_tools

PROMPTS = ROOT / "prompts"
ROLES = ROOT / "roles"


# ---------------------------------------------------------------- run: shared budget and ledger


@dataclass
class Run:
    """An agent tree, or a whole mission: limits, budgets, cloud ledger and logs, kept in `log_dir` on disk so that
    every process of a mission (MCP server, shell calls, metering gateway) shares them."""

    cfg: dict
    workspace: pathlib.Path
    allow_spend: bool = False
    run_id: str = field(default_factory=lambda: dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4])
    log_dir: pathlib.Path | None = None
    tokens: int = 0
    agents: int = 0
    settings: dict = field(default_factory=dict)  # mission settings: allow_publish, publish_visibility, hf_namespace
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.workspace = pathlib.Path(self.workspace).resolve()
        if self.log_dir is None:
            self.log_dir = pathlib.Path(os.environ.get("FRONTIER_RUNS", ROOT / "runs")) / self.run_id
        self.log_dir = pathlib.Path(self.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(self.log_dir)

    @property
    def limits(self) -> dict:
        return self.cfg["limits"]

    def log(self, **event) -> None:
        """Append to events.jsonl, a hash-chained log (budget.verify_chain detects an edited line)."""
        event = {"t": now().isoformat(timespec="seconds"), **event}
        append_chained(self.log_dir / "events.jsonl", json.loads(json.dumps(event, default=str)))

    def charge(self, usage: dict, spec: llm.ModelSpec, agent: str = "") -> float:
        with self._lock:
            self.tokens += usage.get("input", 0) + usage.get("output", 0)
        return self.budget.charge_llm("spawn_agent", agent, spec.provider, spec.model, usage, spec.mode, spec.price_x)

    def over_budget(self) -> str:
        """Why no further model call may be made, or ''."""
        if self.tokens >= self.limits.get("max_tokens_per_run", 10**12):
            return "the token limit of the run is reached (limits.max_tokens_per_run)"
        ok, why = self.budget.llm_allowed()
        return "" if ok else why

    # ledger of the cloud resources created by any agent of the run (file-locked: shared between processes)
    @contextlib.contextmanager
    def _ledger(self):
        f = self.log_dir / "resources.json"
        with open(self.log_dir / ".resources.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            items = json.loads(f.read_text()) if f.exists() else []
            yield items
            write_json(f, items)
            fcntl.flock(lock, fcntl.LOCK_UN)

    @property
    def resources(self) -> list[dict]:
        return self.budget.resources()

    def record(self, resource: dict) -> None:
        with self._ledger() as items:
            items.append({**resource, "state": "alive", "created": now().isoformat(timespec="seconds")})
        self.log(event="resource_created", **resource)

    def mark(self, resource_id: str, state: str) -> None:
        with self._ledger() as items:
            for r in items:
                if r["id"] == resource_id:
                    r["state"] = state
                    if state == "deleted":
                        r.setdefault("deleted_at", now().isoformat(timespec="seconds"))
        self.log(event="resource_" + state, id=resource_id)

    def owns(self, resource_id: str) -> bool:
        return any(r["id"] == resource_id for r in self.resources)

    def alive(self) -> list[dict]:
        return [r for r in self.resources if r.get("state") != "deleted"]

    def take_agent_slot(self) -> bool:
        with self._lock:
            if self.agents >= self.limits.get("max_agents", 12):
                return False
            self.agents += 1
            return True


@dataclass
class Ctx:
    """What a tool sees."""

    run: Run
    agent: Agent

    @property
    def workspace(self) -> pathlib.Path:
        return self.run.workspace

    @property
    def limits(self) -> dict:
        return self.run.limits

    @property
    def allow_spend(self) -> bool:
        return self.run.allow_spend


# ---------------------------------------------------------------- roles


def load_role(name: str) -> dict:
    """roles/<name>.md: a header between '---' lines (key: value), then the role's instructions."""
    p = ROLES / f"{name}.md"
    if not p.exists():
        raise llm.LLMError(f"unknown role {name!r}; known: {', '.join(sorted(x.stem for x in ROLES.glob('*.md')))}")
    text = p.read_text()
    meta: dict = {}
    body = text
    if text.startswith("---"):
        head, body = text[3:].split("\n---", 1)
        for ln in head.strip().splitlines():
            k, _, v = ln.partition(":")
            v = v.strip()
            meta[k.strip()] = [x.strip() for x in v.strip("[]").split(",") if x.strip()] if v.startswith("[") else v
    meta["instructions"] = body.strip()
    return meta


def roles_summary() -> str:
    out = []
    for p in sorted(ROLES.glob("*.md")):
        r = load_role(p.stem)
        out.append(f"- {p.stem} (tier {r.get('tier')}, reasoning {r.get('reasoning')}): {r.get('description', '')}")
    return "\n".join(out)


def expand_tools(names) -> list[str]:
    """Tool names, where a group name (base, infra, budget, hf, mission) stands for its tools."""
    out: list[str] = []
    for n in names:
        for t in GROUPS.get(n, [n]):
            if t not in out:
                out.append(t)
    return out


# ---------------------------------------------------------------- agent


def _fill(template: str, **kw) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n // 2] + f"\n…[{len(s) - n} chars cut]…\n" + s[-n // 2:]


class Agent:
    def __init__(self, run: Run, provider: str, tier: str, reasoning: str, tools: list[str] | None = None,
                 instructions: str = "", depth: int = 0, parent: str | None = None, max_turns: int | None = None,
                 name: str = "main", mode: str = ""):
        self.run = run
        self.spec = llm.resolve(run.cfg, provider, tier, reasoning, mode)
        self.depth = depth
        self.parent = parent
        self.id = f"{name}-{uuid.uuid4().hex[:6]}"
        self.max_turns = min(int(max_turns or 10**9), run.limits.get("max_turns", 40))
        names = expand_tools(all_tools() if tools is None else tools)  # [] is a request for no tools
        if depth >= run.limits.get("max_depth", 2):
            names = [n for n in names if n != "spawn_agent"]
        self.tools: dict[str, Tool] = {n: REGISTRY[n] for n in names if n in REGISTRY}
        self.messages = [{"role": "system", "content": self._system(instructions)}]
        self.usage = {"input": 0, "output": 0, "reasoning": 0, "calls": 0, "usd": 0.0}

    def _system(self, instructions: str) -> str:
        base = (PROMPTS / "system.md").read_text()
        parts = [_fill(base,
            model=self.spec.label(), tier=self.spec.tier, depth=self.depth,
            max_depth=self.run.limits.get("max_depth", 2), workspace=self.run.workspace,
            tools=", ".join(self.tools), date=dt.date.today().isoformat())]
        if any(t in INFRA_TOOLS for t in self.tools):
            parts.append(_fill((PROMPTS / "cloud.md").read_text(),
                               spend="allowed" if self.run.allow_spend else "NOT allowed in this run (read-only calls only)"))
        if "spawn_agent" in self.tools:
            parts.append(_fill((PROMPTS / "subagents.md").read_text(),
                providers=", ".join(self.run.cfg["providers"]), provider=self.spec.provider, roles=roles_summary()))
        if instructions:
            parts.append("# Your role\n\n" + instructions)
        return "\n\n".join(parts)

    def run_task(self, task: str) -> str:
        self.messages.append({"role": "user", "content": task})
        self.run.log(event="agent_start", agent=self.id, parent=self.parent, depth=self.depth, model=self.spec.model,
                     provider=self.spec.provider, tier=self.spec.tier, reasoning=self.spec.reasoning,
                     effort_value=self.spec.effort_value, mode=self.spec.mode or "standard", tools=list(self.tools),
                     task=task[:2000])
        final = ""
        for turn in range(self.max_turns):
            max_out = self.run.limits.get("max_output_tokens", 16000)
            tool_specs = [t.spec() for t in self.tools.values()]
            worst = worst_case_usd(self.spec.provider, self.spec.model,
                                   len(json.dumps(self.messages, default=str)) + len(json.dumps(tool_specs)), max_out,
                                   self.spec.price_x)
            if why := self.run.over_budget():
                final = f"[stopped: {why}]\n" + final
                break
            rid, why = self.run.budget.reserve(worst, f"{self.id} {self.spec.label()}")
            if rid is None:
                final = f"[stopped: {why}]\n" + final
                break
            t0 = time.time()
            try:
                reply = llm.chat(self.spec, self.messages, tool_specs, max_out)
            except llm.LLMError as e:
                if e.maybe_billed:  # charged at the worst case, as the gateway does for a call it could not meter
                    self.run.budget.charge_llm("spawn_agent", self.id, self.spec.provider, self.spec.model, {},
                                               self.spec.mode, self.spec.price_x, usd=worst, exact=False)
                raise
            finally:
                self.run.budget.release(rid)
            usd = self.run.charge(reply.usage, self.spec, self.id)
            self.usage["usd"] = round(self.usage.get("usd", 0) + usd, 6)
            for k in ("input", "output", "reasoning"):
                self.usage[k] += reply.usage.get(k, 0)
            self.usage["calls"] += 1
            self.run.log(event="model_call", agent=self.id, turn=turn, model=self.spec.model, provider=self.spec.provider,
                         reasoning=self.spec.reasoning, effort_value=self.spec.effort_value, usage=reply.usage,
                         seconds=round(time.time() - t0, 2), stop=reply.stop, tool_calls=[c["name"] for c in reply.tool_calls],
                         text=reply.content[:4000])
            self.messages.append(reply.raw_message)
            final = reply.content or final
            if not reply.tool_calls:
                break
            with ThreadPoolExecutor(max_workers=min(8, len(reply.tool_calls))) as pool:
                results = list(pool.map(self._call_tool, reply.tool_calls))
            for call, out in zip(reply.tool_calls, results, strict=True):
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": out})
        else:
            final = f"[stopped after {self.max_turns} turns]\n" + final
        self.run.log(event="agent_end", agent=self.id, usage=self.usage, answer=final[:8000])
        return final

    def _call_tool(self, call: dict) -> str:
        t0 = time.time()
        tool = self.tools.get(call["name"])
        if tool is None:
            out = f"error: no tool {call['name']!r}; available: {', '.join(self.tools)}"
        else:
            try:
                out = str(tool.fn(Ctx(self.run, self), **call["arguments"]))
            except Exception as e:  # noqa: BLE001 - errors go back to the model
                out = f"error: {type(e).__name__}: {e}"
        out = _truncate(out, self.run.limits.get("tool_output_chars", 20000))
        self.run.log(event="tool_call", agent=self.id, tool=call["name"], arguments=json.dumps(call["arguments"])[:2000],
                     seconds=round(time.time() - t0, 2), output=out[:1000])
        return out


# ---------------------------------------------------------------- spawn_agent


def t_spawn_agent(ctx: Ctx, task: str, tier: str = "", reasoning: str = "", provider: str = "", role: str = "",
                  tools: list[str] | None = None, max_turns: int = 0, instructions: str = "", mode: str = "") -> str:
    parent: Agent = ctx.agent
    r = load_role(role) if role else {}
    allowed = ctx.run.settings.get("providers")
    if allowed and (provider or r.get("provider")) and (provider or r.get("provider")) not in allowed:
        return f"refused: provider {provider or r.get('provider')!r} is not available in this mission ({', '.join(allowed)})"
    tier = tier or r.get("tier") or "standard"
    reasoning = reasoning or r.get("reasoning") or "medium"
    provider = provider or r.get("provider") or parent.spec.provider
    # Tools: those named in the call, else the role's, else the parent's; never one the parent does not have.
    # An empty list is a request for no tools, not for the default.
    if tools is None:
        tools = r.get("tools")
    wanted = list(parent.tools) if tools is None else expand_tools(tools)
    tools = [t for t in wanted if t in parent.tools]
    if not ctx.run.take_agent_slot():
        return "refused: the run already started its maximum number of sub-agents (limits.max_agents)"
    text = "\n\n".join(x for x in (r.get("instructions", ""), instructions) if x)
    try:
        child = Agent(ctx.run, provider, tier, reasoning, tools=tools, instructions=text, depth=parent.depth + 1,
                      parent=parent.id, max_turns=max_turns or int(r.get("max_turns", 0) or 0) or None,
                      name=role or "sub", mode=mode)
    except llm.LLMError as e:
        return f"refused: {e}"
    answer = child.run_task(task)
    u = child.usage
    return (f"[sub-agent {child.id}: {child.spec.label()}, tier {child.spec.tier}, {u['calls']} calls, "
            f"{u['input']} in / {u['output']} out tokens, {u['usd']:.4f} USD]\n{answer}")


REGISTRY["spawn_agent"] = Tool(
    "spawn_agent",
    "Start a sub-agent on a model API with its own context, provider, model tier, reasoning level and speed mode; "
    "give it a self-contained task and get its final answer. Several spawn_agent calls in one turn run in parallel. "
    "Its model calls are charged to the run's LLM budget.",
    _obj({
        "task": {"type": "string", "description": "complete, self-contained task: the sub-agent sees nothing else"},
        "tier": {"type": "string", "enum": llm.TIERS},
        "reasoning": {"type": "string", "enum": llm.LEVELS},
        "provider": {"type": "string", "description": "provider section of config/providers.toml; default: yours"},
        "role": {"type": "string", "description": "optional preset from roles/ (sets tier, reasoning, tools, instructions)"},
        "tools": {"type": "array", "items": {"type": "string"},
                  "description": "subset of your tools (group names: infra, budget, hf); default: the role's, else yours"},
        "max_turns": {"type": "integer"},
        "instructions": {"type": "string", "description": "system prompt of the sub-agent (defines your own sub-agent)"},
        "mode": {"type": "string", "description": "speed/price mode of the provider, from frontier_plan (default: standard)"},
    }, ["task"]),
    t_spawn_agent,
)
