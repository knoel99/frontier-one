#!/usr/bin/env python3
"""A fake race for the dashboard: plausible state directories for one team per provider, with no model call.

    python3 tools/fake_race.py DIR [--hours 24] [--seed 7] [--live]
    python3 -m frontier dashboard --root DIR

It writes what the launcher, the gateway and the MCP server would have written over the first --hours of a race that
started --hours ago: mission.json, budgets.json, the hash-chained llm.jsonl and events.jsonl (through budget.py, so
the dashboard finds them intact), resources.json, runners/<id>.log and the harness session logs. Costs come from
config/prices.toml. With --live it then keeps appending calls and actions in real time until stopped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from frontier import budget
from frontier.config import load_config
from frontier.mission import NATIVE_HARNESS

TOOLS = ["budget_status", "scw_offers", "rp_offers", "api_docs", "sympy", "frontier_plan", "infra_resources", "mission_status"]
COMMANDS = ["ls subject/", "pdftotext subject/subject.pdf - | head -120", "pip install torch==2.9.1 datasets==4.2.0",
            "python src/prepare_data.py --out data/", "python src/train.py --epochs 3 --batch 64", "pytest -q",
            "python src/eval.py --split test --seeds 0 1 2", "git add -A && git commit -m 'baseline'",
            "python src/ablation.py --no-attention", "sed -n 1,80p report/report.md", "nvidia-smi", "du -sh data/",
            "python src/plot.py --out report/figures/", "rg -n 'TODO' src/", "git log --oneline | head"]
EDITS = ["src/train.py", "src/model.py", "src/eval.py", "report/report.md", "DECISIONS.md", "COMPLIANCE.md", "README.md"]
MESSAGES = ["Baseline reproduced within 0.4 points of the paper.", "The ablation lowers the score: keeping attention.",
            "Moving the 3-seed sweep to a rented GPU.", "Report section 4 updated with the new table.",
            "Budget check: about half of the LLM budget left, keeping the frontier tier for writing only."]
TASKS = ["Survey the literature on the subject and list the 8 most relevant papers with one line each.",
         "Review src/train.py for bugs and data leakage; answer with a list of findings.",
         "Try to refute the result of table 2: rerun the evaluation logic by hand on 20 samples.",
         "Write the related-work section of the report from notes/related.md, 600 words.",
         "Derive the gradient of the loss in section 3 and check it with sympy."]
GPUS = {"scaleway": [("L4-1-24G", 0.7875), ("L40S-1-48G", 1.4699), ("H100-1-80G", 2.73)],
        "runpod": [("1x NVIDIA RTX 4090", 0.59), ("1x NVIDIA A100 80GB PCIe", 1.39), ("1x NVIDIA H100 80GB HBM3", 2.69)]}


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


class Team:
    def __init__(self, root: pathlib.Path, provider: str, cfg: dict, rng: random.Random, start: dt.datetime, hours: float):
        self.p, self.cfg, self.rng, self.start = provider, cfg["providers"][provider], rng, start
        self.harness = NATIVE_HARNESS.get(provider, "opencode")
        self.name = f"{provider}-{self.harness}"
        self.d = root / self.name
        self.d.mkdir(parents=True, exist_ok=True)
        tiers = self.cfg["tiers"]
        self.main = tiers["frontier"]
        self.sub = tiers.get("light") or tiers.get("standard") or self.main
        self.modes = self.cfg.get("modes", {})
        self.fast = next((m for m in ("fast", "priority", "highspeed", "flashx") if m in self.modes), "")
        self.pace = rng.uniform(0.5, 1.6)  # calls per minute relative to the others
        self.cloud = rng.choice(["scaleway", "runpod", "both", "both", "none"])
        self.share = rng.uniform(0.2, 0.6) if provider != "openai" else 0.95  # of the LLM cap spent by the end; one team burns
        self.hours, self.refused = hours, False
        self.events: list[dict] = []
        self.llm: list[dict] = []
        self.resources: list[dict] = []
        self.runners: dict[str, list[str]] = {}
        self.sessions: dict[int, list[str]] = {}
        self.caps = {"start": iso(start), "deadline": iso(start + dt.timedelta(hours=72)), "llm_usd": 50.0,
                     "scaleway_eur": 30.0, "runpod_usd": 30.0}
        self.spent = 0.0
        self.delivered_at = start + dt.timedelta(hours=hours * rng.uniform(0.85, 0.95)) if provider == "deepseek" else None

    def ev(self, t: dt.datetime, event: str, **kw) -> None:
        self.events.append({"t": iso(t), "event": event, **kw})

    def call(self, t: dt.datetime, source: str, agent: str, model: str, ctx: int, mode: str = "") -> float:
        usage = {"input": ctx, "cached": int(ctx * self.rng.uniform(0.6, 0.95)), "output": self.rng.randint(150, 3500)}
        usage["reasoning"] = int(usage["output"] * self.rng.uniform(0.2, 0.7))
        price_x = self.modes.get(mode, {}).get("price_x", 1.0) if mode else 1.0
        usd, exact = budget.llm_cost(self.p, model, usage, price_x, at=t)
        if self.spent + usd > self.caps["llm_usd"]:
            if not self.refused:
                self.ev(t, "llm_refused", agent=agent, why="LLM budget: spent + worst case > cap")
            self.refused = True
            return 0.0
        self.spent += usd
        self.llm.append({"t": iso(t), "frontier.source": source, "gen_ai.agent.name": agent, "gen_ai.provider.name": self.p,
                         "gen_ai.request.model": model, "frontier.mode": mode, "gen_ai.usage.input_tokens": usage["input"],
                         "gen_ai.usage.output_tokens": usage["output"], "gen_ai.usage.cache_read.input_tokens": usage["cached"],
                         "gen_ai.usage.cache_write.input_tokens": 0, "gen_ai.usage.reasoning.output_tokens": usage["reasoning"],
                         "frontier.cost.usd": round(usd, 6), "frontier.price_exact": exact})
        return usd

    def action(self, n: int) -> str:
        r, k = self.rng, self.rng.random()
        if self.harness == "claude":
            if k < 0.6:
                c = {"type": "tool_use", "name": "Bash", "input": {"command": r.choice(COMMANDS)}}
            elif k < 0.8:
                c = {"type": "tool_use", "name": "Edit", "input": {"file_path": r.choice(EDITS)}}
            elif k < 0.85:
                c = {"type": "tool_use", "name": "Agent", "input": {"subagent_type": r.choice(["reviewer", "infra"]),
                                                                   "description": r.choice(TASKS)[:80]}}
            else:
                c = {"type": "text", "text": r.choice(MESSAGES)}
            return json.dumps({"type": "assistant", "message": {"content": [c]}})
        if self.harness == "codex":
            if k < 0.6:
                item = {"type": "command_execution", "command": r.choice(COMMANDS)}
            elif k < 0.8:
                item = {"type": "file_change", "changes": [{"path": r.choice(EDITS)}]}
            elif k < 0.9:
                item = {"type": "mcp_tool_call", "server": "frontier", "tool": r.choice(TOOLS)}
            else:
                item = {"type": "agent_message", "text": r.choice(MESSAGES)}
            return json.dumps({"type": "item.completed", "item": item})
        return f"[{self.harness}] {r.choice(COMMANDS)}"  # other harnesses: plain text, not parsed by the dashboard

    def machine(self, t: dt.datetime, end: dt.datetime) -> None:
        r = self.rng
        prov = r.choice(["scaleway", "runpod"]) if self.cloud == "both" else self.cloud
        typ, price = r.choice(GPUS[prov])
        rid = f"{r.getrandbits(64):016x}"[:12] if prov == "runpod" else f"{r.getrandbits(128):032x}"
        hours = r.uniform(0.5, 5)
        tool = "rp_create_pod" if prov == "runpod" else "scw_create_instance"
        self.ev(t, "mcp_call", tool=tool, arguments=json.dumps({"type": typ, "max_hours": 8}))
        rec = {"provider": prov, "kind": "pod" if prov == "runpod" else "instance", "id": rid, "name": f"{self.name}-gpu",
               "type": typ, ("usd_per_hour" if prov == "runpod" else "eur_per_hour"): price, "max_hours": 8}
        self.ev(t, "resource_created", **rec)
        log = [f"=== {iso(t)} created: {typ}, {price}/h, max 8 h"]
        stop = min(t + dt.timedelta(hours=hours), end)
        u = t + dt.timedelta(minutes=4)
        while u < stop and prov == "runpod":  # a Scaleway Instance runs its cloud-init script: no exec tool
            cmd = r.choice(["python src/train.py --epochs 3", "python src/eval.py --seeds 0 1 2", "nvidia-smi", "tail -5 train.log"])
            log += [f"=== {iso(u)} exec (exit 0, {r.randint(300, 24000)} ms): {cmd}",
                    f"epoch {r.randint(1, 3)} loss {r.uniform(0.2, 1.4):.3f} acc {r.uniform(0.6, 0.92):.3f}"]
            self.ev(u, "mcp_call", tool="rp_exec", arguments=json.dumps({"pod_id": rid, "command": cmd}))
            u += dt.timedelta(minutes=r.uniform(5, 40))
        alive = stop >= end
        rec.update(state="alive" if alive else "deleted", created=iso(t))
        if not alive:
            rec["deleted_at"] = iso(stop)
            self.ev(stop, "mcp_call", tool=tool.replace("create", "delete"), arguments=json.dumps({"id": rid}))
            self.ev(stop, "resource_deleted", id=rid)
            log.append(f"=== {iso(stop)} deleted")
        self.resources.append(rec)
        self.runners[rid] = log

    def subagent(self, t: dt.datetime, end: dt.datetime) -> None:
        r = self.rng
        a = f"sub-{r.getrandbits(24):06x}"
        reasoning = r.choice(["low", "medium", "high"])
        mode = self.fast if self.fast and r.random() < 0.25 else ""
        self.ev(t, "agent_start", agent=a, parent="main", depth=1, provider=self.p, model=self.sub, reasoning=reasoning,
                effort_value=reasoning, mode=mode, task=r.choice(TASKS))
        u, usd = t, 0.0
        for _ in range(r.randint(2, 12)):
            u += dt.timedelta(seconds=r.uniform(8, 90))
            if u >= end:
                return  # still running at the end of the generated period
            usd += self.call(u, "spawn_agent", a, self.sub, r.randint(3000, 40000), mode)
            tc = r.choice([[], [{"name": "sympy"}], [{"name": "api_docs"}]])
            self.ev(u, "model_call", agent=a, tool_calls=tc)
            if tc:
                self.ev(u, "tool_call", agent=a, tool=tc[0]["name"], arguments="{}")
        self.ev(u, "agent_end", agent=a, answer="Done: findings listed above.", usage={"usd": round(usd, 6)})

    def simulate(self, end: dt.datetime) -> None:
        r, t = self.rng, self.start
        self.call(t, "launcher", "key-check", self.sub, 20, "")
        self.ev(t, "mission_prepared", harness=self.harness, provider=self.p)
        t += dt.timedelta(seconds=40)
        self.ev(t, "harness_setup", code=0)
        n = 0
        while t < end:
            n += 1
            self.ev(t, "session_start", n=n, command=self.harness)
            log = self.sessions.setdefault(n, [])
            if self.harness == "vibe" and t < self.start + dt.timedelta(hours=3):  # its account answered 429 at first
                t += dt.timedelta(seconds=r.uniform(5, 12))
                log.append("Error: 429 Too Many Requests")
                self.ev(t, "session_end", n=n, code=1, seconds=8)
                t += dt.timedelta(minutes=r.uniform(10, 25))
                continue
            stop = min(t + dt.timedelta(hours=r.uniform(0.5, 4)), self.delivered_at or end, end)
            ctx = r.randint(12000, 20000)
            next_sub = t + dt.timedelta(minutes=r.uniform(15, 60))
            next_box = t + dt.timedelta(hours=r.uniform(1, 6))
            while t < stop:
                t += dt.timedelta(seconds=r.expovariate(self.pace / 45))
                if t >= stop:
                    break
                plan = self.caps["llm_usd"] * self.share * (t - self.start).total_seconds() / 3600 / self.hours
                if self.spent > plan + 0.5:  # the team works on its machines or its files between calls
                    continue
                mode = self.fast if self.fast and r.random() < 0.1 else ("flex" if "flex" in self.modes and r.random() < 0.05 else "")
                self.call(t, "gateway", f"harness:{self.harness}", self.main, ctx, mode)
                ctx = min(ctx + r.randint(500, 4000), 240000)
                if r.random() < 0.5:
                    log.append(self.action(n))
                if r.random() < 0.08:
                    self.ev(t, "mcp_call", tool=r.choice(TOOLS), arguments="{}")
                if t >= next_sub:
                    self.ev(t, "mcp_call", tool="spawn_agent", arguments=json.dumps({"tier": "light"}))
                    self.subagent(t, end)
                    next_sub = t + dt.timedelta(minutes=r.uniform(20, 90))
                if t >= next_box and self.cloud != "none":
                    self.machine(t, end)
                    next_box = t + dt.timedelta(hours=r.uniform(3, 8))
            if stop >= end:
                break  # the session is still running
            t = stop
            self.ev(t, "session_end", n=n, code=0, seconds=0)
            if self.delivered_at and t >= self.delivered_at:
                self.deliver(t)
                return
            t += dt.timedelta(seconds=r.uniform(5, 30))

    def deliver(self, t: dt.datetime) -> None:
        self.ev(t, "mcp_call", tool="deliver", arguments=json.dumps({"summary": "report and code delivered"}))
        st = {"llm_usd": round(self.spent, 6), "scaleway_eur": 0, "runpod_usd": 0}
        rec = {"delivered_at": iso(t), "elapsed_hours": round((t - self.start).total_seconds() / 3600, 3),
               "deadline": self.caps["deadline"], **st, "llm_cap_usd": self.caps["llm_usd"], "missing": [],
               "report_paths": {}, "publications": [], "cleanup": [], "summary": "report and code delivered"}
        self.ev(t, "delivered", **{k: v for k, v in rec.items() if k != "cleanup"})
        self.caps.update(frozen=True, frozen_llm_usd=round(self.spent, 6))
        self.delivery = rec

    def write(self) -> None:
        s = {"name": self.name, "harness": self.harness, "provider": self.p, "providers": [self.p], "tier": "frontier",
             "reasoning": "high", "workspace": str(self.d / "ws"), "state": str(self.d), "fake": True}
        budget.write_json(self.d / "mission.json", s)
        budget.write_json(self.d / "budgets.json", self.caps)
        for name, rows in (("llm.jsonl", self.llm), ("events.jsonl", self.events)):
            (self.d / name).unlink(missing_ok=True)
            for e in sorted(rows, key=lambda e: e["t"]):
                budget.append_chained(self.d / name, e)
        budget.write_json(self.d / "resources.json", self.resources)
        (self.d / "runners").mkdir(exist_ok=True)
        for rid, lines in self.runners.items():
            (self.d / "runners" / f"{rid}.log").write_text("\n".join(lines) + "\n")
        (self.d / "sessions").mkdir(exist_ok=True)
        for n, lines in self.sessions.items():
            (self.d / "sessions" / f"{n:02d}.log").write_text("\n".join(lines) + "\n")
        if getattr(self, "delivery", None):
            budget.write_json(self.d / "delivery.json", self.delivery)


def live(teams: list[Team], rng: random.Random) -> None:
    """Keep the race moving: a model call, now and then a tool call and a harness action, for a running team."""
    while True:
        tm = rng.choice([x for x in teams if not getattr(x, "delivery", None)])
        t = budget.now()
        n = max(tm.sessions)
        tm.llm, tm.events = [], []
        tm.spent = budget.Budget(tm.d).llm_spent()
        tm.call(t, "gateway", f"harness:{tm.harness}", tm.main, rng.randint(40000, 200000))
        if rng.random() < 0.3:
            tm.ev(t, "mcp_call", tool=rng.choice(TOOLS), arguments="{}")
        for name, rows in (("llm.jsonl", tm.llm), ("events.jsonl", tm.events)):
            for e in rows:
                budget.append_chained(tm.d / name, e)
        with open(tm.d / "sessions" / f"{n:02d}.log", "a") as f:
            f.write(tm.action(n) + "\n")
        time.sleep(rng.uniform(1, 4))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(a.root)
    cfg = load_config()
    end = budget.now()
    start = end - dt.timedelta(hours=a.hours)
    rng = random.Random(a.seed)
    teams = [Team(root, p, cfg, random.Random(rng.random()), start, a.hours) for p in NATIVE_HARNESS if p in cfg["providers"]]
    for tm in teams:
        tm.simulate(end)
        tm.write()
        st = budget.Budget(tm.d).status()
        print(f"{tm.name:22s} {len(tm.llm):5d} calls  {st['llm']['spent_usd']:7.2f} USD  {len(tm.resources)} machine(s)  "
              f"{max(tm.sessions)} session(s)  ledgers {st['ledgers_intact']}")
    if a.live:
        print("live: appending calls and actions; Ctrl-C to stop", flush=True)
        live(teams, rng)
    return 0


if __name__ == "__main__":
    sys.exit(main())
