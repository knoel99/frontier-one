#!/usr/bin/env python3
"""A fake race for the dashboard: plausible state directories for one team per provider, with no model call.

    python3 tools/fake_race.py DIR [--hours 24] [--seed 7] [--live]
    python3 tools/fake_race.py DIR --replay [--speed 100] [--doubling 1] [--hours 72]   # a race from its start, faster
    python3 -m frontier dashboard --root DIR

It writes what the launcher, the gateway and the MCP server would have written over the first --hours of a race that
started --hours ago: mission.json, budgets.json, the hash-chained llm.jsonl and events.jsonl (through budget.py, so
the dashboard finds them intact), resources.json, runners/<id>.log and the harness session logs, and the snapshots at
the time and spend milestones of frontier/snapshots.py with a made-up scientific quality in DIR/jury/snapshots.json.
Costs come from config/prices.toml. With --live it then keeps appending calls and actions in real time until stopped. With --replay the
race starts now, empty, and its --hours of race are written as they happen on a clock of their own (DIR/clock.json, which
the dashboard reads): SPEED race minutes per real minute at the start, the speed doubling every DOUBLING real minutes, so
that the snapshots' milestones, which double too, come at a steady pace (72 h in about 5 min by default).
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import math
import json
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from frontier import budget
from frontier.config import load_config
from frontier.dashboard import race_clock, real_time
from frontier.mission import NATIVE_HARNESS
from frontier.snapshots import SPEND_USD, TIME_MINUTES, label

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
REPORT = [  # the sections a fake report fills in, in order; $...$ is LaTeX math in both versions
    ("Abstract", "We train neural surrogates of the vorticity form of the 2D Navier-Stokes equations at $Re = 10^3$ "
     "and compare them with a pseudo-spectral solver on accuracy, cost and long-horizon stability."),
    ("Introduction", "Learned solvers promise orders of magnitude of speed-up, but their errors grow over long rollouts. "
     "We measure that growth against the cost of the training data."),
    ("Data", "Trajectories come from a pseudo-spectral solver on a $256 \\times 256$ periodic grid, "
     "$\\partial_t \\omega + u \\cdot \\nabla \\omega = \\nu \\Delta \\omega + f$, with 1000 initial conditions."),
    ("Method", "A Fourier neural operator with 4 layers and 32 modes, trained on one-step targets with a "
     "rollout loss over 8 steps; the code is in `src/train.py`."),
    ("Results", "Relative $L^2$ error of the vorticity after 50 steps, per run, on held-out initial conditions."),
    ("Discussion", "The error halves with each tripling of the data until about 3 GPU hours, then the rollout loss dominates."),
    ("AI contributions", "The lead agent wrote the solver and the training loop; sub-agents surveyed the literature and "
     "reviewed the statistics."),
    ("Reproducibility", "`bash reproduce.sh` regenerates every table and figure from a fresh checkout; see `DELIVERY.md`."),
]
TASKS = ["Survey the literature on the subject and list the 8 most relevant papers with one line each.",
         "Review src/train.py for bugs and data leakage; answer with a list of findings.",
         "Try to refute the result of table 2: rerun the evaluation logic by hand on 20 samples.",
         "Write the related-work section of the report from notes/related.md, 600 words.",
         "Derive the gradient of the loss in section 3 and check it with sympy."]
ROLE_TASKS = {  # role preset (roles/*.md), "sub" for a sub-agent defined by its own instructions
    "infra": ["Rent the cheapest GPU with 24 GB, run src/train.py for 3 seeds, collect the logs and delete the machine.",
              "Price an H100 for 4 hours on Scaleway and Runpod and report the cheapest offer in stock."],
    "reviewer": ["Try to refute the result of table 2: rerun the evaluation logic by hand on 20 samples.",
                 "Review src/train.py for bugs and data leakage; answer with a list of findings."],
    "sub": ["Survey the literature on the subject and list the 8 most relevant papers with one line each.",
            "Write the related-work section of the report from notes/related.md, 600 words.",
            "Derive the gradient of the loss in section 3 and check it with sympy."]}
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
        self.skill, self.tau, self.code = rng.uniform(35, 85), rng.uniform(150, 700), rng.uniform(0.7, 1.25)
        # where its science levels off, how fast, and how its code and data follow it
        self.grades: dict[str, dict] = {}
        self.graded: list[tuple] = []  # (time, commit, label) of each graded snapshot, for the jury
        self.speed = 1.0  # race minutes per real minute: the science grows with the race's time
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
        role = r.choices(list(ROLE_TASKS), weights=[2, 3, 4])[0]
        a = f"{role}-{r.getrandbits(24):06x}"
        reasoning = r.choice(["low", "medium", "high"])
        mode = self.fast if self.fast and r.random() < 0.25 else ""
        self.ev(t, "agent_start", agent=a, role=role, parent="main", depth=1, provider=self.p, model=self.sub,
                reasoning=reasoning, effort_value=reasoning, mode=mode, task=r.choice(ROLE_TASKS[role]))
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

    def spend_at(self, t: dt.datetime) -> tuple[float, float]:
        """(model calls USD, simulations USD) at `t`, EUR counted at par as frontier/snapshots.py does."""
        llm = sum(e["frontier.cost.usd"] for e in self.llm if e["t"] <= iso(t))
        sim = 0.0
        for r in self.resources:
            a, b = budget.parse_time(r["created"]), min(budget.parse_time(r.get("deleted_at")) or t, t)
            sim += max(0.0, (b - a).total_seconds() / 3600) * (r.get("eur_per_hour") or r.get("usd_per_hour") or 0)
        return llm, sim

    def snapshots(self, end: dt.datetime) -> None:
        """A snapshot 5 minutes after each time and spend milestone, as the watchdog takes them, and its grade."""
        due = []
        minutes = [m for m in TIME_MINUTES] + [TIME_MINUTES[-1] * 2 ** k for k in range(1, 8)]
        due += [(self.start + dt.timedelta(minutes=m), label("time", m)) for m in minutes]
        cum, spends = 0.0, list(SPEND_USD)
        for e in sorted(self.llm, key=lambda e: e["t"]):
            cum += e["frontier.cost.usd"]
            while spends and cum >= spends[0]:
                due.append((budget.parse_time(e["t"]), label("usd", spends.pop(0))))
        for t, lab in sorted(due):
            t += dt.timedelta(minutes=5)
            if t >= end or (self.delivered_at and t > self.delivered_at):
                continue
            self.snapshot(t, lab)

    def work(self, m: float) -> float:
        """Minutes of real work after m minutes of race: Vibe's first sessions failed."""
        return m * self.speed - (180 if self.harness == "vibe" else 0)

    def snapshot(self, t: dt.datetime, lab: str) -> dict:
        """The snapshot taken at t and its grade, which follows the science so far without falling much below the
        last one; returns the snapshot event."""
        r = self.rng
        llm, sim = self.spend_at(t)
        m = (t - self.start).total_seconds() / 60
        commit = f"{r.getrandbits(160):040x}"
        self.ev(t, "snapshot", labels=[lab], commit=commit, minutes=round(m, 2), llm_usd=round(llm, 4),
                simulation_usd=round(sim, 4), core_seconds=round(m * 60 * r.uniform(0.2, 0.9), 1), pdf={lab: "ok"})
        if m >= 15:  # before, there is no report to grade
            before = [g for g in sorted(self.graded) if g[0] <= t]
            q = self.grades[before[-1][1]]["scientific_quality"] if before else 0.0
            q = max(q - 4, min(100, self.skill * (1 - math.exp(-max(0, self.work(m)) / self.tau)) + r.gauss(0, 2.5)))
            arts = min(100, max(0.0, q * self.code + r.gauss(0, 3)))  # code and data follow the science, loosely
            self.grades[commit] = {"scientific_quality": round(max(0.0, q), 1), "artifacts": round(arts, 1)}
            self.graded.append((t, commit, lab))
        return self.events[-1]

    def report(self, m: float) -> tuple[str, str] | None:
        """The report after m minutes of race, as Markdown and LaTeX: its sections come with the work, a row of results
        per hour; None before the agent starts writing."""
        f = 1 - math.exp(-max(0, self.work(m)) / self.tau)
        n = min(len(REPORT), int(f * len(REPORT) * 1.6))
        if m < 15 or not n:
            return None
        err = 0.08 * (1.3 - self.skill / 100)
        rows = [(f"run-{i + 1:02d}", err * 2 ** -(i / 3), 0.5 + i * 0.37) for i in range(min(12, int(m / 60)))]
        title = f"Neural surrogates for 2D incompressible flow: {self.main}"
        md = [f"# {title}", ""]
        tex = [r"\documentclass{article}", r"\usepackage{amsmath,booktabs}", rf"\title{{{title}}}", r"\begin{document}", r"\maketitle"]
        for head, body in REPORT[:n]:
            md += [f"## {head}", "", body, ""]
            tex += [rf"\section{{{head}}}", body.replace("**", "").replace("`", "").replace("%", r"\%"), ""]
            if head == "Results" and rows:
                md += ["| run | relative L2 error | GPU hours |", "|---|---|---|"]
                md += [f"| {a} | {b:.4f} | {c:.2f} |" for a, b, c in rows] + [""]
                tex += [r"\begin{tabular}{lrr}\toprule run & relative $L^2$ error & GPU hours \\ \midrule"]
                tex += [rf"{a} & {b:.4f} & {c:.2f} \\" for a, b, c in rows] + [r"\bottomrule\end{tabular}", ""]
        return "\n".join(md), "\n".join(tex + [r"\end{document}", ""])

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


JUDGES = ("anthropic", "openai", "google")  # a judge never grades the team of its own provider (frontier/jury.py)


def jury(root: pathlib.Path, teams: list[Team], cfg: dict, rng: random.Random, end: dt.datetime) -> None:
    """What a snapshot jury would write in DIR/jury: each judge's calls in its ledger and a verdict event per snapshot."""
    d = root / "jury"
    d.mkdir(exist_ok=True)
    calls, events = jury_rows(teams, cfg, rng, end)
    budget.write_json(d / "budgets.json", {"llm_usd": 100.0})
    for name, rows in (("llm.jsonl", calls), ("events.jsonl", events)):
        (d / name).unlink(missing_ok=True)
        for e in sorted(rows, key=lambda e: e["t"]):
            budget.append_chained(d / name, e)


def jury_rows(teams: list[Team], cfg: dict, rng: random.Random, end: dt.datetime) -> tuple[list[dict], list[dict]]:
    """Each judge's calls and verdicts for every graded snapshot."""
    return verdicts([(tm, *g) for tm in teams for g in tm.graded], cfg, rng, end)


def verdicts(graded: list, cfg: dict, rng: random.Random, end: dt.datetime, judges=JUDGES) -> tuple[list[dict], list[dict]]:
    """Each judge's calls and verdicts for these (team, time, commit, label), a few minutes after the snapshot, never
    on its own provider."""
    calls, events = [], []
    for tm, t, commit, lab in graded:
        g = tm.grades[commit]
        for j in [p for p in judges if p != tm.p and p in cfg["providers"]]:
            start = t + dt.timedelta(minutes=rng.uniform(2, 10))
            secs = rng.uniform(30, 150)
            stop = start + dt.timedelta(seconds=secs)
            if stop >= end:
                continue
            model = cfg["providers"][j]["tiers"]["frontier"]
            inp = rng.randint(15000, 45000)
            usage = {"input": inp, "cached": inp // 2, "output": rng.randint(2000, 6000)}
            usd, _ = budget.llm_cost(j, model, usage, 1.0, at=stop)
            calls.append({"t": iso(stop), "frontier.source": "jury", "gen_ai.agent.name": f"judge:{j}", "gen_ai.provider.name": j,
                          "gen_ai.request.model": model, "frontier.mode": "", "gen_ai.usage.input_tokens": usage["input"],
                          "gen_ai.usage.output_tokens": usage["output"], "gen_ai.usage.cache_read.input_tokens": usage["cached"],
                          "gen_ai.usage.cache_write.input_tokens": 0, "gen_ai.usage.reasoning.output_tokens": usage["output"] // 2,
                          "frontier.cost.usd": round(usd, 6), "frontier.price_exact": True})
            events.append({"t": iso(stop), "event": "verdict", "judge": j, "model": model, "team": tm.name, "commit": commit,
                           "label": lab, "scientific_quality": round(min(100, max(0, g["scientific_quality"] + rng.gauss(0, 3))), 1),
                           "artifacts": round(min(100, max(0, g["artifacts"] + rng.gauss(0, 3))), 1),
                           "seconds": round(secs), "usd": round(usd, 6)})
    return calls, events


def replay(root: pathlib.Path, teams: list[Team], jcalls: list[dict], jevents: list[dict], clock: dict, cfg: dict,
           rng: random.Random, end: dt.datetime) -> None:
    """Write each team's race, its report and the jury's work as the race's clock reaches them; a snapshot's grade is
    published once its judges have answered. A request the dashboard leaves in DIR/requests takes a graded snapshot
    of every running team at once."""
    rq = root / "requests"
    rq.mkdir(parents=True, exist_ok=True)
    for f in rq.glob("*.json"):
        f.unlink()
    asked = 0
    jd = root / "jury"
    jd.mkdir(exist_ok=True)
    for f in [jd / "llm.jsonl", jd / "events.jsonl", jd / ".llm.jsonl.head", jd / ".events.jsonl.head", jd / "panel.json"]:
        f.unlink(missing_ok=True)
    budget.write_json(jd / "budgets.json", {"llm_usd": 100.0})
    budget.write_json(jd / "snapshots.json", {})
    ready = {}  # commit -> when its grade is published: its last verdict, or the snapshot itself without a judge
    for tm in teams:
        for t, commit, _ in tm.graded:
            ready[commit] = iso(t)
    for v in jevents:
        ready[v["commit"]] = max(ready.get(v["commit"], ""), v["t"])
    queues = {}
    for tm in teams:
        for f in ["llm.jsonl", "events.jsonl", ".llm.jsonl.head", ".events.jsonl.head", "delivery.json"]:
            (tm.d / f).unlink(missing_ok=True)
        for f in [tm.d / "ws" / "report.md", tm.d / "ws" / "report" / "report.tex"]:
            f.unlink(missing_ok=True)
        for sub in ("runners", "sessions"):
            (tm.d / sub).mkdir(exist_ok=True)
            for f in (tm.d / sub).glob("*.log"):
                f.unlink()
        budget.write_json(tm.d / "mission.json", {"name": tm.name, "harness": tm.harness, "provider": tm.p, "providers": [tm.p],
                                                 "tier": "frontier", "reasoning": "high", "workspace": str(tm.d / "ws"),
                                                 "state": str(tm.d), "fake": True})
        budget.write_json(tm.d / "budgets.json", {k: v for k, v in tm.caps.items() if not k.startswith("frozen")})
        budget.write_json(tm.d / "resources.json", [])
        queues[tm.name] = {"llm": sorted(tm.llm, key=lambda e: e["t"]), "events": sorted(tm.events, key=lambda e: e["t"]),
                           "i": {"llm": 0, "events": 0}, "res": "", "logs": set(), "delivered": False, "report": None}
    jq = {"llm": sorted(jcalls, key=lambda e: e["t"]), "events": sorted(jevents, key=lambda e: e["t"]), "i": {"llm": 0, "events": 0}}
    published: dict[str, dict] = {}
    judges, panel_seen = list(JUDGES), None
    graded = {g[1]: (tm, *g) for tm in teams for g in tm.graded}  # commit -> (team, time, commit, label)
    print(f"live: replaying {len(teams)} teams; Ctrl-C to stop", flush=True)
    while True:
        at = race_clock(clock, budget.now())
        now = iso(at)
        pf = jd / "panel.json"
        if pf.exists() and pf.stat().st_mtime != panel_seen:  # the dashboard changed the panel: it judges what is not yet judged
            panel_seen, judges = pf.stat().st_mtime, budget.read_json(pf)["judges"]
            todo = [c for c, r in ready.items() if r > now and c in graded]
            gone = {(v["t"], v["judge"]) for v in jq["events"][jq["i"]["events"]:] if v["commit"] in todo}
            jq["events"] = jq["events"][:jq["i"]["events"]] + [v for v in jq["events"][jq["i"]["events"]:] if v["commit"] not in todo]
            jq["llm"] = jq["llm"][:jq["i"]["llm"]] + [c for c in jq["llm"][jq["i"]["llm"]:]
                                                       if (c["t"], c["gen_ai.agent.name"].split(":", 1)[1]) not in gone]
            calls, verdict = verdicts([graded[c] for c in todo], cfg, rng, end, judges)
            for kind, rows in (("llm", calls), ("events", verdict)):
                i = jq["i"][kind]
                jq[kind] = jq[kind][:i] + sorted(jq[kind][i:] + rows, key=lambda e: e["t"])
            for c in todo:
                ready[c] = iso(max(graded[c][1], at))
            for v in verdict:
                ready[v["commit"]] = max(ready[v["commit"]], v["t"])
            print(f"{now}: panel {', '.join(judges)} for the {len(todo)} snapshots not judged yet", flush=True)
        for f in sorted(rq.glob("evaluate-*.json")):
            asked += 1
            new = []
            for tm in teams:
                if at >= end or (tm.delivered_at and at >= tm.delivered_at):
                    continue
                ev = tm.snapshot(at, f"on demand {asked}")
                q = queues[tm.name]
                bisect.insort(q["events"], ev, lo=q["i"]["events"], key=lambda e: e["t"])
                if tm.graded[-1][1] == ev["commit"]:
                    new.append((tm, *tm.graded[-1]))
                    graded[ev["commit"]] = new[-1]
                    ready[ev["commit"]] = now
            calls, verdict = verdicts(new, cfg, rng, end, judges)
            for kind, rows in (("llm", calls), ("events", verdict)):
                i = jq["i"][kind]
                jq[kind] = jq[kind][:i] + sorted(jq[kind][i:] + rows, key=lambda e: e["t"])
            for v in verdict:
                ready[v["commit"]] = max(ready[v["commit"]], v["t"])
            print(f"{now}: on-demand evaluation {asked}, {len(new)} teams graded", flush=True)
            f.unlink()
        for tm in teams:
            q = queues[tm.name]
            m = (min(at, tm.delivered_at or at) - tm.start).total_seconds() / 60
            rep = tm.report(m)
            if rep and rep[0] != q["report"]:
                (tm.d / "ws" / "report").mkdir(parents=True, exist_ok=True)
                (tm.d / "ws" / "report.md").write_text(rep[0])
                (tm.d / "ws" / "report" / "report.tex").write_text(rep[1])
                q["report"] = rep[0]
            for kind, f in (("llm", "llm.jsonl"), ("events", "events.jsonl")):
                rows, i = q[kind], q["i"][kind]
                while i < len(rows) and rows[i]["t"] <= now:
                    budget.append_chained(tm.d / f, rows[i])
                    i += 1
                q["i"][kind] = i
            res = [{**r, "state": "deleted" if r.get("deleted_at") and r["deleted_at"] <= now else "alive",
                    **({} if r.get("deleted_at") and r["deleted_at"] <= now else {"deleted_at": None})}
                   for r in tm.resources if r["created"] <= now]
            res = [{k: v for k, v in r.items() if v is not None} for r in res]
            if json.dumps(res) != q["res"]:
                budget.write_json(tm.d / "resources.json", res)
                q["res"] = json.dumps(res)
            for r in res:
                if ("r", r["id"]) not in q["logs"]:
                    (tm.d / "runners" / f"{r['id']}.log").write_text("\n".join(tm.runners.get(r["id"], [])) + "\n")
                    q["logs"].add(("r", r["id"]))
            for e in q["events"][:q["i"]["events"]]:
                if e["event"] == "session_start" and ("s", e["n"]) not in q["logs"]:
                    (tm.d / "sessions" / f"{e['n']:02d}.log").write_text("\n".join(tm.sessions.get(e["n"], [])) + "\n")
                    q["logs"].add(("s", e["n"]))
            if getattr(tm, "delivery", None) and not q["delivered"] and tm.delivery["delivered_at"] <= now:
                budget.write_json(tm.d / "delivery.json", tm.delivery)
                budget.write_json(tm.d / "budgets.json", tm.caps)
                q["delivered"] = True
            fresh = {c: g for c, g in tm.grades.items() if ready.get(c, "~") <= now}
            if fresh != published.get(tm.name):
                published[tm.name] = fresh
                budget.write_json(jd / "snapshots.json", published)
        for kind, f in (("llm", "llm.jsonl"), ("events", "events.jsonl")):
            rows, i = jq[kind], jq["i"][kind]
            while i < len(rows) and rows[i]["t"] <= now:
                budget.append_chained(jd / f, rows[i])
                i += 1
            jq["i"][kind] = i
        time.sleep(2)


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
    ap.add_argument("--hours", type=float, help="hours of race: written before going live (default 24), or replayed (72)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--replay", action="store_true", help="start the race now and write it as it happens")
    ap.add_argument("--speed", type=float, default=100, help="with --replay: race minutes per real minute at the start")
    ap.add_argument("--doubling", type=float, default=1, help="with --replay: real minutes for the speed to double (0: never)")
    a = ap.parse_args()
    root = pathlib.Path(a.root)
    cfg = load_config()
    hours = a.hours or (72 if a.replay else 24)
    end = budget.now()
    start = end - dt.timedelta(hours=hours)
    rng = random.Random(a.seed)
    if a.replay:
        start, end = end, end + dt.timedelta(hours=hours)
        teams = [Team(root, p, cfg, random.Random(rng.random()), start, hours) for p in cfg["providers"]]
        for tm in teams:
            tm.simulate(end)
            tm.snapshots(end)
        jcalls, jevents = jury_rows(teams, cfg, rng, end)
        clock = {"real_start": iso(start), "race_start": iso(start), "race_end": iso(end), "speed": a.speed,
                 "doubling_minutes": a.doubling}
        root.mkdir(parents=True, exist_ok=True)
        budget.write_json(root / "clock.json", clock)
        real = (real_time(clock, end) - start).total_seconds() / 60
        print(f"replay: {hours:g} h of race in {real:.0f} min, from {iso(start)}", flush=True)
        replay(root, teams, jcalls, jevents, clock, cfg, rng, end)
        return 0
    teams = [Team(root, p, cfg, random.Random(rng.random()), start, hours) for p in cfg["providers"]]
    for tm in teams:
        tm.simulate(end)
        tm.snapshots(end)
        tm.write()
        st = budget.Budget(tm.d).status()
        print(f"{tm.name:22s} {len(tm.llm):5d} calls  {st['llm']['spent_usd']:7.2f} USD  {len(tm.resources)} machine(s)  "
              f"{max(tm.sessions)} session(s)  ledgers {st['ledgers_intact']}")
    (root / "jury").mkdir(exist_ok=True)
    budget.write_json(root / "jury" / "snapshots.json", {tm.name: tm.grades for tm in teams})
    jury(root, teams, cfg, rng, end)
    if a.live:
        print("live: appending calls and actions; Ctrl-C to stop", flush=True)
        live(teams, rng)
    return 0


if __name__ == "__main__":
    sys.exit(main())
