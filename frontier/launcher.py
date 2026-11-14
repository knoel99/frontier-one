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
GOAL_CONDITION = ("The goal is met when the `deliver` tool has returned a delivery record; it is impossible when "
                  "`budget_status` shows the LLM budget spent or no time left.")
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
    env.setdefault(key_env, values["key"])  # the provider's own variable, for harnesses that read it themselves
    env.update({k: str(v) for k, v in _fill(h.get("env", {}), values).items()})
    if gateway and h.get("route"):
        env.update({k: str(v) for k, v in _fill(h["route"], values).items()})
    else:
        env.update({k: str(v) for k, v in _fill(h.get("default_base_url", {}), values).items()})
    env["FRONTIER_STATE"] = str(info["state"])
    env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    cmd = _fill(h["command"], values)
    if gateway and h.get("route_args"):
        extra = _fill(h["route_args"], values)
        cmd = cmd[:-1] + extra + cmd[-1:] if h["command"][-1] == "{prompt}" else cmd + extra
    return cmd, env


def routable(h: dict) -> bool:
    return bool(h.get("route") or h.get("route_args"))


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
    if kind == "json-usage":  # the last JSON object of the output that carries token counts or a cost
        found: dict = {}
        cost = None
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            u, c = _scan_usage(obj)
            if u:
                found = u
            if c is not None:
                cost = c
        if not found and len(lines) > 1:  # a single pretty-printed JSON document
            try:
                found, cost = _scan_usage(json.loads(out))
            except json.JSONDecodeError:
                pass
        return cost, found
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


_IN = ("input_tokens", "prompt_tokens", "inputTokens", "promptTokenCount")
_OUT = ("output_tokens", "completion_tokens", "outputTokens", "candidatesTokenCount")
_CACHED = ("cache_read_tokens", "cached_input_tokens", "cached_tokens", "cache_read_input_tokens", "cachedContentTokenCount")
_THINK = ("thinking_tokens", "reasoning_output_tokens", "reasoning_tokens", "thoughtsTokenCount")
_COST = ("total_cost_usd", "cost_usd", "total_cost")


def _scan_usage(obj) -> tuple[dict, float | None]:
    """Find token counts and a USD cost anywhere in a harness's JSON report (formats differ per harness)."""
    best: dict = {}
    cost = None
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if cost is None:
                cost = next((float(o[k]) for k in _COST if isinstance(o.get(k), (int, float))), None)
            i = next((o[k] for k in _IN if isinstance(o.get(k), int)), None)
            out = next((o[k] for k in _OUT if isinstance(o.get(k), int)), None)
            if i is not None and out is not None and i + out > best.get("input", 0) + best.get("output", 0):
                think = next((o[k] for k in _THINK if isinstance(o.get(k), int)), 0)
                best = {"input": i, "output": out + (think if "thinking_tokens" in o else 0), "reasoning": think,
                        "cached": next((o[k] for k in _CACHED if isinstance(o.get(k), int)), 0)}
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return best, cost


def _watchdog(run: Run, stop: threading.Event) -> None:
    while not stop.wait(60):
        try:
            infra.enforce(run)
        except Exception as e:  # noqa: BLE001
            print(f"watchdog: {e}", file=sys.stderr)


def open_gateway(state: pathlib.Path, s: dict, h: dict):
    """Start the metering gateway when the harness can be routed through it: (server, base URL) or (None, None)."""
    if not routable(h):
        print("this harness cannot be routed through the gateway: its main-thread spend comes from its own usage "
              "report when it gives one (config/harnesses.toml `usage`)")
        return None, None
    try:
        gw = serve(str(state), s.get("gateway_port", 0), agent=f"harness:{s['harness']}")
    except OSError:  # the port chosen at preparation is taken: harnesses configured by file will miss the gateway
        gw = serve(str(state), 0, agent=f"harness:{s['harness']}")
        print(f"warning: port {s.get('gateway_port')} busy, gateway on {gw.server_address[1]}")
    gateway = f"http://127.0.0.1:{gw.server_address[1]}"
    print(f"metering gateway {gateway}")
    return gw, gateway


def harness_setup(info: dict, h: dict, gateway: str | None, run: Run) -> None:
    if h.get("setup"):  # once per machine and key, e.g. `codex login`
        _, env = build_session(info, h, "", gateway)
        p = subprocess.run(["bash", "-c", h["setup"]], cwd=info["workspace"], env=env, capture_output=True, text=True)
        run.log(event="harness_setup", code=p.returncode, output=(p.stdout + p.stderr)[-2000:])
        print(f"setup: exit {p.returncode}")


def run_one(info: dict, h: dict, n: int, run: Run, gateway: str | None) -> int:
    """One headless session of the harness; its output is written live to sessions/<n>.log."""
    s, cfg = info["settings"], load_config()
    prompt = FIRST_PROMPT if n == 1 else NEXT_PROMPT.format(n=n)
    if h.get("goal"):
        prompt = f"{h['goal']} {prompt} {GOAL_CONDITION}"
    cmd, env = build_session(info, h, prompt, gateway)
    run.log(event="session_start", n=n, command=cmd[0])
    started = budget_mod.now().isoformat(timespec="seconds")
    t0 = time.time()
    logs = pathlib.Path(info["state"]) / "sessions"
    logs.mkdir(exist_ok=True)
    log = logs / f"{n:02d}.log"
    with open(log, "w") as f:  # written live: the dashboard follows the session as it runs
        p = subprocess.run(cmd, cwd=info["workspace"], env=env, stdout=f, stderr=subprocess.STDOUT, text=True,
                           stdin=subprocess.DEVNULL)
    out = log.read_text(errors="replace")
    metered = any(e["frontier.source"] == "gateway" and e["t"] >= started for e in run.budget.llm_entries())
    if not metered:  # the gateway saw none of the session's traffic: charge what the harness reports
        usd, usage = usage_from_report(h, out)
        model = llm.resolve(cfg, s["provider"], s["tier"], s["reasoning"]).model
        if usd is not None or usage:
            run.budget.charge_llm("harness-report", f"harness:{s['harness']}", s["provider"], model, usage, usd=usd)
        else:
            run.log(event="session_unmetered", n=n, note="no gateway traffic and no usage report")
    run.log(event="session_end", n=n, code=p.returncode, seconds=round(time.time() - t0, 1))
    print(f"session {n}: exit {p.returncode}, {time.time() - t0:.0f} s")
    return p.returncode


def session(state_dir: str, n: int) -> int:
    """`frontier mission session <state> <n>`: one session where the state and workspace are local, e.g. inside an
    OpenShell sandbox (the host launcher runs this through `openshell sandbox exec`)."""
    state = pathlib.Path(state_dir)
    s = json.loads((state / "mission.json").read_text())
    s["workspace"] = os.getcwd()
    (state / "mission.json").write_text(json.dumps(s, indent=1) + "\n")
    info = {"mission": s.get("conf", {}), "settings": s, "state": state, "workspace": pathlib.Path(os.getcwd())}
    h = harness_conf(s["harness"], s["provider"])
    run = Run(load_config(), workspace=info["workspace"], allow_spend=True, run_id=state.name, log_dir=state, settings=s)
    gw, gateway = open_gateway(state, s, h)
    try:
        harness_setup(info, h, gateway, run)
        return run_one(info, h, n, run, gateway)
    finally:
        if gw:
            gw.shutdown()


def start(mission_file: str, prepare_only: bool = False, keep_resources: bool = False, max_sessions: int | None = None,
          ping: bool = True, dry_run: bool = False) -> int:
    info = mission.prepare(mission_file, ping=ping)
    s, state, ws = info["settings"], info["state"], info["workspace"]
    print(f"workspace {ws}\nharness {s['harness']}, provider {s['provider']} ({s['tier']}/{s['reasoning']})"
          + (f", sandbox {s['sandbox']}" if s.get("sandbox") else ""))
    for p, r in info["pings"].items():
        print(f"  {p}: {r}")
    h = harness_conf(s["harness"], s["provider"])
    box = None
    if s.get("sandbox") == "openshell":
        from .sandbox import OpenShell

        box = OpenShell(info, h, dry_run=dry_run)
        for w in box.write_files():
            print(f"network list: {w}")
        print(f"OpenShell files in {box.dir}")
    if prepare_only:
        print("prepared (MISSION.md, TOOLS.md, AGENTS.md, harness files); not launched")
        return 0
    cfg = load_config()
    run = Run(cfg, workspace=ws, allow_spend=True, run_id=state.name, log_dir=state, settings=s)
    gw = gateway = None
    stop = threading.Event()
    threading.Thread(target=_watchdog, args=(run, stop), daemon=True).start()
    sessions = max_sessions or int(info["mission"].get("harness", {}).get("max_sessions", 20))
    try:
        if box:  # inside the try: a failed setup still deletes what it created
            box.setup()
        else:
            gw, gateway = open_gateway(state, s, h)
            harness_setup(info, h, gateway, run)
        for n in range(1, sessions + 1):
            why = _stop_reason(run)
            if why:
                print(f"stop: {why}")
                break
            if box:
                (state / "sessions").mkdir(exist_ok=True)
                with open(state / "sessions" / f"sandbox-{n:02d}.log", "w") as f:
                    box.commands.append(box.session_command(n))
                    code = 0 if dry_run else subprocess.run(box.session_command(n), stdout=f, stderr=subprocess.STDOUT,
                                                            stdin=subprocess.DEVNULL).returncode
                box.pull()
                print(f"sandbox session {n}: exit {code}")
                if dry_run:
                    break
            else:
                run_one(info, h, n, run, gateway)
            commit(ws, f"Session {n}")
    finally:
        stop.set()
        if gw:
            gw.shutdown()
        if box:
            box.teardown()
            if dry_run:
                print("\n".join(" ".join(c) for c in box.commands))
        if not (state / "delivery.json").exists():
            if run.alive() and not keep_resources and not dry_run:
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
