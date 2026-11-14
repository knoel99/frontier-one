"""Mission launcher: prepare the workspace, start the metering gateway, run the harness headless session after
session until the mission is delivered, the deadline passes or a budget is spent, then clean up.

    python3 -m frontier mission start mission.toml [--prepare-only] [--keep-resources]
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import tomllib

from . import budget as budget_mod
from . import infra, llm, mission
from .agent import Ctx, Run
from .config import ROOT, load_config
from .gateway import serve

HARNESS_CONF = ROOT / "config" / "harnesses.toml"
FIRST_PROMPT = ("Read MISSION.md and TOOLS.md, then carry out the mission. You work autonomously: nobody will answer "
                "questions. Record your strategy and decisions in DECISIONS.md as you go. When the deliverables are "
                "ready, call the `deliver` tool.")
NEXT_PROMPT = ("Continue the mission (session {n}). Your previous session ended. Read MISSION.md, DECISIONS.md and the "
               "state of the repository, call `budget_status` for the spend and the time left, then carry on. When the "
               "deliverables are ready, call the `deliver` tool.")


def harness_conf(name: str, provider: str) -> dict:
    conf = tomllib.loads(HARNESS_CONF.read_text())
    if name not in conf:
        raise ValueError(f"no launch recipe for harness {name!r} in config/harnesses.toml; known: {', '.join(conf)}")
    h = dict(conf[name])
    over = h.pop("providers", {}).get(provider, {})
    for k, v in over.items():
        h[k] = {**h.get(k, {}), **v} if isinstance(v, dict) and k != "env" else v
    if "env" in over:  # a provider profile replaces the environment
        h["env"] = over["env"]
    return h


def _fill(x, values: dict):
    if isinstance(x, str):
        for k, v in values.items():
            x = x.replace("{" + k + "}", str(v))
        return x
    if isinstance(x, list):
        return [_fill(i, values) for i in x]
    if isinstance(x, dict):
        return {k: _fill(v, values) for k, v in x.items()}
    return x


def build_session(info: dict, h: dict, prompt: str, gateway: str | None) -> tuple[list[str], dict]:
    m, cfg = info["mission"], load_config()
    s = info["settings"]
    provider = s["provider"]
    spec = llm.resolve(cfg, provider, s["tier"], s["reasoning"])
    effort = spec.effort_value if h.get("effort") == "provider" else s["reasoning"]
    key_env = m.get("llm", {}).get("key_env") or cfg["providers"][provider]["key_env"]
    values = {"prompt": prompt, "model": spec.model, "effort": effort, "max_turns": 500, "workspace": info["workspace"],
              "state": info["state"], "key": os.environ.get(key_env) or llm.PROXY_PLACEHOLDER, "frontier": ROOT,
              "provider_model": f"{provider}/{spec.model}", "gateway": gateway or ""}
    env = dict(os.environ)
    env.update({k: str(v) for k, v in _fill(h.get("env", {}), values).items()})
    if gateway and h.get("route"):
        env.update({k: str(v) for k, v in _fill(h["route"], values).items()})
    else:
        env.update({k: str(v) for k, v in _fill(h.get("default_base_url", {}), values).items()})
    env["FRONTIER_STATE"] = str(info["state"])
    env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    return _fill(h["command"], values), env


def usage_from_report(h: dict, out: str) -> tuple[float | None, dict]:
    """What a harness reports about its own session: (USD if it gives a cost, token usage if it gives tokens).

    `usage` in config/harnesses.toml: "json:<field>" = a USD field of a JSON line (Claude Code's total_cost_usd);
    "jsonl:turn.completed" = token usage of JSONL events of that type, summed (Codex CLI's --json)."""
    kind = h.get("usage", "")
    lines = out.strip().splitlines()
    if kind.startswith("json:"):
        field = kind.split(":", 1)[1]
        for line in reversed(lines):
            try:
                v = json.loads(line).get(field)
            except (json.JSONDecodeError, AttributeError):
                continue
            if v is not None:
                return float(v), {}
    if kind.startswith("jsonl:"):
        etype = kind.split(":", 1)[1]
        tot = {"input": 0, "cached": 0, "cache_write": 0, "output": 0, "reasoning": 0}
        for line in lines:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict) or e.get("type") != etype:
                continue
            u = e.get("usage") or {}
            tot["input"] += u.get("input_tokens", 0) or 0
            tot["cached"] += u.get("cached_input_tokens", 0) or 0
            tot["cache_write"] += u.get("cache_write_input_tokens", 0) or 0
            tot["output"] += u.get("output_tokens", 0) or 0
            tot["reasoning"] += u.get("reasoning_output_tokens", 0) or 0
        if tot["input"] or tot["output"]:
            return None, tot
    return None, {}


def _watchdog(run: Run, stop: threading.Event) -> None:
    while not stop.wait(60):
        try:
            infra.enforce(run)
        except Exception as e:  # noqa: BLE001
            print(f"watchdog: {e}", file=sys.stderr)


def start(mission_file: str, prepare_only: bool = False, keep_resources: bool = False, max_sessions: int | None = None,
          ping: bool = True) -> int:
    info = mission.prepare(mission_file, ping=ping)
    s, state, ws = info["settings"], info["state"], info["workspace"]
    print(f"workspace {ws}\nharness {s['harness']}, provider {s['provider']} ({s['tier']}/{s['reasoning']})")
    for p, r in info["pings"].items():
        print(f"  {p}: {r}")
    if prepare_only:
        print("prepared (MISSION.md, TOOLS.md, AGENTS.md, harness files); not launched")
        return 0
    h = harness_conf(s["harness"], s["provider"])
    cfg = load_config()
    run = Run(cfg, workspace=ws, allow_spend=True, run_id=state.name, log_dir=state, settings=s)
    gw = gateway = None
    if h.get("route"):
        gw = serve(str(state), 0, agent=f"harness:{s['harness']}")
        gateway = f"http://127.0.0.1:{gw.server_address[1]}"
        print(f"metering gateway {gateway}: " + ", ".join(f"{k}={v}" for k, v in _fill(h["route"], {"gateway": gateway}).items()))
    else:
        print("this harness cannot be routed through the gateway: its main-thread spend comes from its own usage "
              "report when it gives one (config/harnesses.toml `usage`)")
    if h.get("setup"):  # once per machine and key, e.g. `codex login`
        cmd, env = build_session(info, h, "", gateway)
        p = subprocess.run(["bash", "-c", h["setup"]], cwd=ws, env=env, capture_output=True, text=True)
        run.log(event="harness_setup", code=p.returncode, output=(p.stdout + p.stderr)[-2000:])
        print(f"setup: exit {p.returncode}")
    stop = threading.Event()
    threading.Thread(target=_watchdog, args=(run, stop), daemon=True).start()
    sessions = max_sessions or int(info["mission"].get("harness", {}).get("max_sessions", 20))
    logs = state / "sessions"
    logs.mkdir(exist_ok=True)
    try:
        for n in range(1, sessions + 1):
            why = _stop_reason(run)
            if why:
                print(f"stop: {why}")
                break
            prompt = FIRST_PROMPT if n == 1 else NEXT_PROMPT.format(n=n)
            cmd, env = build_session(info, h, prompt, gateway)
            run.log(event="session_start", n=n, command=cmd[0])
            started = budget_mod.now().isoformat(timespec="seconds")
            t0 = time.time()
            with open(logs / f"{n:02d}.log", "w") as f:
                p = subprocess.run(cmd, cwd=ws, env=env, stdout=subprocess.PIPE, stderr=f, text=True,
                                   stdin=subprocess.DEVNULL)
                f.write(p.stdout)
            metered = any(e["frontier.source"] == "gateway" and e["t"] >= started
                          for e in run.budget.llm_entries())
            if not metered:  # the gateway saw none of the session's traffic: charge what the harness reports
                usd, usage = usage_from_report(h, p.stdout)
                model = llm.resolve(cfg, s["provider"], s["tier"], s["reasoning"]).model
                if usd is not None or usage:
                    run.budget.charge_llm("harness-report", f"harness:{s['harness']}", s["provider"], model, usage, usd=usd)
                else:
                    run.log(event="session_unmetered", n=n, note="no gateway traffic and no usage report")
            run.log(event="session_end", n=n, code=p.returncode, seconds=round(time.time() - t0, 1))
            commit(ws, f"Session {n}")
            print(f"session {n}: exit {p.returncode}, {time.time() - t0:.0f} s")
    finally:
        stop.set()
        if gw:
            gw.shutdown()
        if not (state / "delivery.json").exists():
            if run.alive() and not keep_resources:
                print("cleanup: " + json.dumps(infra.cleanup(run)))
            st = run.budget.status()
            (ws / "COSTS.md").write_text(mission.costs_markdown(st, run.budget.resources(), []))
            print("not delivered; COSTS.md written")
        else:
            commit(ws, "Delivery", tag="delivery")
            print("delivered: " + (state / "delivery.json").read_text())
    return 0


def commit(ws: pathlib.Path, message: str, tag: str = "") -> None:
    """Commit the workspace from outside the harness (whose sandbox may keep .git read-only)."""
    subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.name=frontier", "-c", "user.email=frontier@localhost", "commit", "-q",
                    "-m", message], capture_output=True)
    if tag:
        subprocess.run(["git", "-C", str(ws), "tag", "-f", tag], capture_output=True)


def _stop_reason(run: Run) -> str:
    state = run.log_dir
    if (state / "delivery.json").exists():
        return "delivered"
    st = run.budget.status()
    if st.get("hours_left") is not None and st["hours_left"] <= 0:
        return "deadline passed"
    ok, why = run.budget.llm_allowed()
    if not ok:
        return why
    return ""


def status(mission_file: str) -> int:
    m = mission.load(mission_file)
    state = pathlib.Path(m["mission"]["state"])
    settings = json.loads((state / "mission.json").read_text())
    run = Run(load_config(), workspace=settings["workspace"], run_id=state.name, log_dir=state, settings=settings)
    print(infra.budget_status(Ctx(run, None)))
    return 0


__all__ = ["start", "status", "harness_conf", "build_session", "budget_mod"]
