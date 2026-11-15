#!/usr/bin/env python3
"""Live verification of Frontier, levels 1 and 2 of docs/TEST-PLAN.md: one check per feature, on the real services,
with tiny budgets. Each check prints a verdict (PASS, ISSUES, BLOCKED) and the observable state behind it; the script
deletes what it created and reads each deletion back.

    python3 tools/verify_live.py --root DIR [--only F10,F12] [--skip-cloud]

It spends real money (well under 1 EUR in all): LLM key checks and sub-agents on four providers, one Runpod pod for a
few minutes, one Scaleway DEV1-S for a few minutes, one private Hugging Face repo (deleted at the end), and a jury of
two cheap judges on two small teams.
Keys come from the session's proxy ([secrets] = proxy): none is set or written here.

Coverage across processes, for the live mission (level 3):
    python3 tools/verify_live.py --coverage on|off|report --root DIR
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import site
import subprocess
import sys
import time
import traceback
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from frontier import budget, credentials, gateway, hf, infra, launcher, mission  # noqa: E402
from frontier.agent import Ctx, Run  # noqa: E402
from frontier.config import load_config  # noqa: E402
from frontier.http import scw_raw  # noqa: E402
from frontier.mcp import Server  # noqa: E402

PROVIDERS = ["openai", "google", "deepseek", "zhipu"]  # two closed-model, two open-weight
RESULTS: list[dict] = []
CREATED: list[tuple[str, str]] = []  # (kind, id) of everything this script created, for the cleanup


def verdict(fid: str, name: str, ok: bool | None, evidence: str) -> None:
    v = "PASS" if ok else "BLOCKED" if ok is None else "ISSUES"
    RESULTS.append({"feature": fid, "name": name, "verdict": v, "evidence": evidence})
    print(f"{v:8} {fid:4} {name}: {evidence}", flush=True)


def check(fid: str, name: str):
    def wrap(fn):
        def run(*a, **k):
            if ONLY and fid not in ONLY:
                return None
            try:
                return fn(*a, **k)
            except Exception as e:  # noqa: BLE001 - a failing check is a verdict, not a crash
                verdict(fid, name, False, f"{type(e).__name__}: {e} | {traceback.format_exc(limit=2).splitlines()[-2].strip()}")
        return run
    return wrap


ONLY: set[str] = set()


def write_mission(root: pathlib.Path, name: str, extra: str = "", hours: float = 1, llm_usd: float = 0.3,
                  provider: str = "openai") -> pathlib.Path:
    d = root / "files" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "subject.md").write_text("# Subject\n\nVerification run of the Frontier framework.\n")
    f = d / "m.toml"
    f.write_text(f'''[mission]
name = "{name}"
subject = "subject.md"
workspace = "{root / 'ws' / name}"
state = "{root / 'missions' / name}"
duration_hours = {hours}
[harness]
name = "codex"
[llm]
provider = "{provider}"
other_providers = ["google", "deepseek", "zhipu"]
budget_usd = {llm_usd}
[runpod]
budget_usd = 0.40
[scaleway]
budget_eur = 0.20
[huggingface]
visibility = "private"
[secrets]
OPENAI_API_KEY = "proxy"
GEMINI_API_KEY = "proxy"
DEEPSEEK_API_KEY = "proxy"
ZAI_API_KEY = "proxy"
RUNPOD_API_KEY = "proxy"
SCW_SECRET_KEY = "proxy"
HF_TOKEN = "proxy"
{extra}''')
    return f


# ---------------------------------------------------------------- level 1


@check("F1", "mission preparation")
def f1_f3(root):
    info = mission.prepare(write_mission(root, "level1"), ping=True, offers=False)
    ws, st = info["workspace"], info["state"]
    files = ["AGENTS.md", "MISSION.md", "TOOLS.md", "subject/subject.md", ".codex/config.toml"]
    missing = [f for f in files if not (ws / f).exists()]
    caps = json.loads((st / "budgets.json").read_text())
    verdict("F1", "mission preparation", not missing and (ws / ".git").exists() and caps.get("llm_usd") == 0.3,
            f"workspace files {'all present' if not missing else 'missing ' + str(missing)}; caps llm {caps.get('llm_usd')} USD, "
            f"runpod {caps.get('runpod_usd')} USD, scaleway {caps.get('scaleway_eur')} EUR")
    origin = info["settings"]["credentials"]
    keys = {s.env for s in credentials.services(load_config()).values()}  # keys only: settings come from the environment
    verdict("F2", "credentials (proxy sources)", all(v == "proxy" for k, v in origin.items() if k in keys),
            f"key origins {({k: v for k, v in origin.items() if k in keys})}")
    tools_md = (ws / "TOOLS.md").read_text()
    checked = {p: f"### {p} (key checked with" in tools_md for p in PROVIDERS}
    launcher_calls = [e for e in budget.Budget(st).llm_entries() if e["frontier.source"] == "launcher"]
    verdict("F3", "key check", all(checked.values()) and len(launcher_calls) >= len(PROVIDERS),
            f"{checked}; {len(launcher_calls)} key-check calls priced in llm.jsonl")
    return info


@check("F2", "credentials (env, file, cmd sources)")
def f2_sources(root):
    (root / "files").mkdir(parents=True, exist_ok=True)
    secret_file = root / "files" / "vault"
    secret_file.write_text("verify-file-value-7a1c")
    os.environ["VERIFY_ENV_SOURCE"] = "verify-env-value-91b2"
    m = {"llm": {"provider": "openai"}, "huggingface": {}, "runpod": {}, "scaleway": {},
         "secrets": {"HF_TOKEN": f"file:{secret_file}", "RUNPOD_API_KEY": "env:VERIFY_ENV_SOURCE",
                     "SCW_SECRET_KEY": f"cmd:cat {secret_file} | tr a-z A-Z", "OPENAI_API_KEY": "proxy"}}
    saved = {k: os.environ.get(k) for k in m["secrets"]}
    try:
        origin = credentials.load(m, load_config())
        got = {k: os.environ.get(k) for k in ("HF_TOKEN", "RUNPOD_API_KEY", "SCW_SECRET_KEY")}
    finally:  # the next checks use the proxy again
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    ok = (origin["HF_TOKEN"] == "file" and origin["RUNPOD_API_KEY"] == "env" and origin["SCW_SECRET_KEY"] == "cmd"
          and got["HF_TOKEN"] == "verify-file-value-7a1c" and got["SCW_SECRET_KEY"] == "VERIFY-FILE-VALUE-7A1C")
    verdict("F2", "credentials (env, file, cmd sources)", ok, f"origins {origin}")


@check("F9", "MCP server and shell fallback")
def f9(st: pathlib.Path):
    os.environ["FRONTIER_ALLOW_SPEND"] = "1"
    srv = Server(state=str(st))
    via_mcp, err = srv.call("budget_status", {})
    p = subprocess.run([str(ROOT / "bin" / "frontier"), "call", "budget_status", "{}", "--state", str(st)],
                       capture_output=True, text=True, timeout=120)
    a, b = json.loads(via_mcp), json.loads(p.stdout)
    verdict("F9", "MCP server and shell fallback", not err and a["llm"]["cap_usd"] == b["llm"]["cap_usd"],
            f"budget_status through MCP and through `frontier call` agree (cap {a['llm']['cap_usd']} USD)")
    return srv


@check("F10", "sub-agents on four providers and a role")
def f10_f11(srv: Server, st: pathlib.Path):
    answers = {}
    for p in PROVIDERS:
        text, err = srv.call("spawn_agent", {"task": f"Reply with exactly: ok-{p}", "provider": p, "tier": "light",
                                             "reasoning": "low", "tools": []})
        answers[p] = (not err) and f"ok-{p}" in text
    text, err = srv.call("spawn_agent", {"task": "Reply with exactly: reviewed", "role": "reviewer", "tier": "light",
                                         "reasoning": "low", "tools": []})
    answers["role reviewer"] = (not err) and "reviewed" in text.lower()
    subs = [e for e in budget.Budget(st).llm_entries() if e["frontier.source"] == "spawn_agent"]
    verdict("F10", "sub-agents on four providers and a role", all(answers.values()),
            f"{answers}; {len(subs)} spawn_agent calls priced in llm.jsonl")
    text, err = srv.call("spawn_agent", {"task": "Reply with exactly: ok-flex", "provider": "openai", "tier": "light",
                                         "reasoning": "low", "mode": "flex", "tools": []})
    flex = [e for e in budget.Budget(st).llm_entries() if e.get("frontier.mode") == "flex"]
    verdict("F11", "speed mode (flex)", (not err) and bool(flex),
            f"{len(flex)} call(s) recorded with mode flex" + ("" if flex else f"; answer: {text[:200]}"))


@check("F5", "gateway metering")
def f5(st: pathlib.Path):
    gw = gateway.serve(str(st))
    base = f"http://127.0.0.1:{gw.server_address[1]}"
    before = len(budget.Budget(st).llm_entries())
    body = {"model": "gpt-6-luna", "input": "Reply with: ok", "max_output_tokens": 32}
    req = urllib.request.Request(base + "/openai/v1/responses", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {credentials.PLACEHOLDER}"})
    code = urllib.request.urlopen(req, timeout=120).status
    gw.shutdown()
    new = budget.Budget(st).llm_entries()[before:]
    verdict("F5", "gateway metering", code == 200 and any(e["frontier.source"] == "gateway" for e in new),
            f"HTTP {code}; {len(new)} gateway entry, {sum(e['frontier.cost.usd'] for e in new):.6f} USD")


@check("F21", "API docs and raw read-only cloud calls")
def f21(srv: Server):
    docs, e1 = srv.call("api_docs", {"action": "search", "query": "create server"}) if "api_docs" in srv.names else ("", True)
    r = Run(load_config(), workspace=ROOT, allow_spend=False)
    from frontier import tools
    scw = tools.t_scaleway(Ctx(r, None), "GET", "/instance/v1/zones/nl-ams-1/products/servers", query={"per_page": 5})
    rp = tools.t_runpod_mcp(Ctx(r, None), "list-cpu-types", {})
    verdict("F21", "API docs and raw read-only cloud calls",
            (not e1 or "api_docs" not in srv.names) and '"servers"' in scw and "cpu" in rp.lower(),
            f"catalog search {'served' if 'api_docs' in srv.names else 'not served over MCP (base group)'}; "
            f"Scaleway GET products: {len(scw)} chars; Runpod list-cpu-types: {len(rp)} chars")


@check("F12", "Runpod runner")
def f12_f15(st: pathlib.Path):
    os.environ["FRONTIER_ALLOW_SPEND"] = "1"
    srv = Server(state=str(st))
    offers = json.loads(srv.call("rp_offers", {})[0])["offers"]
    offer = next(o for o in offers if o.get("usd_per_gpu_hour"))  # cheapest first
    out, err = srv.call("rp_create_pod", {"name": "frontier-verify", "gpu_type_id": offer["gpu_type_id"], "max_hours": 0.03,
                                          "image": "python:3.12-slim", "disk_gb": 10, "command": "sleep infinity"})
    if err:
        return verdict("F12", "Runpod runner", False, f"create refused: {out[:300]}")
    pod = json.loads(out)["id"]
    CREATED.append(("runpod", pod))
    res = ""
    for _ in range(30):  # the container starts
        out, err = srv.call("rp_exec", {"pod_id": pod, "command": "python3 -c 'print(6*7)'", "timeout_s": 15})
        if not err and '"42' in out:
            res = out
            break
        time.sleep(10)
    srv.call("rp_logs", {"pod_id": pod, "tail": 50})
    log = (st / "runners" / f"{pod}.log").read_text()
    verdict("F12", "Runpod runner", bool(res) and "exec (exit 0" in log,
            f"pod {pod} ({offer['gpu_type_id']}, {offer['usd_per_gpu_hour']} USD/h): exec printed 42; runner log has "
            f"{log.count('=== ')} entries")
    t0 = time.time()  # F15: max_hours = 0.03 (108 s) has passed or will soon: the watchdog must delete it
    while time.time() - t0 < 240:
        deleted = infra.enforce(srv.run)
        if any(d.get("id") == pod for d in deleted):
            break
        time.sleep(15)
    alive = [r["id"] for r in srv.run.alive()]
    events = [e for e in budget.read_jsonl(st / "events.jsonl") if e.get("event") == "watchdog_delete"]
    gone = _rp_gone(pod)
    verdict("F15", "watchdog deletes what overran max_hours", pod not in alive and bool(events) and gone,
            f"watchdog_delete events: {len(events)}; ledger state deleted: {pod not in alive}; get-pod 404: {gone}")


def _rp_gone(pod: str) -> bool:
    try:
        infra.mcp_json("get-pod", {"id": pod})
        return False
    except infra.APIError as e:
        return infra.gone(e)


@check("F13", "Scaleway runner")
def f13(st: pathlib.Path):
    os.environ["FRONTIER_ALLOW_SPEND"] = "1"
    srv = Server(state=str(st))
    ci = ("#!/bin/bash\nput() { curl -s --local-port 1-1023 -X PATCH -H 'Content-Type: text/plain' --data-binary \"$2\" "
          "\"http://169.254.42.42/user_data/$1\"; }\nput result \"$(python3 -c 'print(6*7)')\"\n")
    out, err = srv.call("scw_create_instance", {"name": "frontier-verify", "commercial_type": "DEV1-S", "zone": "nl-ams-1",
                                                "max_hours": 0.5, "cloud_init": ci})
    if err:
        return verdict("F13", "Scaleway runner", False, f"create refused: {out[:300]}")
    sid = json.loads(out)["id"]
    CREATED.append(("scaleway", f"{st}|{sid}"))
    result = ""
    for _ in range(40):
        code, text = scw_raw("GET", f"/instance/v1/zones/nl-ams-1/servers/{sid}/user_data/result")
        if code == 200 and text.strip():
            result = text.strip()
            break
        time.sleep(15)
    dele = json.loads(srv.call("scw_delete_instance", {"server_id": sid, "zone": "nl-ams-1"})[0])
    code, _ = scw_raw("GET", f"/instance/v1/zones/nl-ams-1/servers/{sid}")
    verdict("F13", "Scaleway runner", result == "42" and dele.get("server") == "deleted" and code == 404,
            f"VM {sid} (DEV1-S, nl-ams-1): result {result!r} read from its metadata; deleted with volumes "
            f"{dele.get('volumes_deleted')}; GET server {code}")


@check("F16", "Hugging Face publishing")
def f16(root: pathlib.Path, st: pathlib.Path):
    os.environ["FRONTIER_ALLOW_SPEND"] = "1"
    srv = Server(state=str(st))
    ws = pathlib.Path(json.loads((st / "mission.json").read_text())["workspace"])
    bad, good = ws / "hf-bad", ws / "hf-good"
    bad.mkdir(exist_ok=True)
    (bad / "README.md").write_text("no card\n")
    refused, _ = srv.call("hf_publish", {"repo_name": "frontier-verify-bad", "folder": "hf-bad", "repo_type": "dataset"})
    good.mkdir(exist_ok=True)
    (good / "data.csv").write_text("x,y\n1,2\n")
    (good / "README.md").write_text("---\nlicense: mit\n---\n# Frontier verification dataset\n\n" + "A two-row CSV written by "
                                    "the Frontier live verification to check publishing; it is deleted right after. " * 3)
    who = json.loads(srv.call("hf_whoami", {})[0])
    name = f"frontier-verify-{int(time.time())}"
    out, err = srv.call("hf_publish", {"repo_name": name, "folder": "hf-good", "repo_type": "dataset"})
    repo = f"{who.get('name')}/{name}"
    CREATED.append(("hf", repo))
    api = hf._api()
    info = api.dataset_info(repo)
    verdict("F16", "Hugging Face publishing", refused.startswith("refused") and not err and info.private,
            f"bad card refused; {repo} created private={info.private} with {len(info.siblings)} files")


# ---------------------------------------------------------------- level 2


@check("F6", "LLM hard cap")
def f6(root: pathlib.Path):
    st = root / "missions" / "cap"
    b = budget.Budget(st)
    b.set_caps(llm_usd=0.000001)
    Run(load_config(), workspace=root, log_dir=st).log(event="session_start", n=1)  # as the launcher does first
    gw = gateway.serve(str(st))
    req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/openai/v1/responses",
                                 data=json.dumps({"model": "gpt-6-luna", "input": "hi", "max_output_tokens": 16}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    gw.shutdown()
    r = Run(load_config(), workspace=root, log_dir=st)
    why = launcher._stop_reason(r)
    verdict("F6", "LLM hard cap", code == 402 and "LLM budget" in why and not b.llm_entries(),
            f"gateway answered {code} without forwarding; stop reason: {why[:90]}")


@check("F7", "deadline stops a running session")
def f7(root: pathlib.Path):
    st = root / "missions" / "deadline"
    b = budget.Budget(st)
    b.set_caps(deadline=(budget.now() + budget.dt.timedelta(seconds=5)).isoformat(timespec="seconds"))
    r = Run(load_config(), workspace=root, log_dir=st)
    t0 = time.time()
    with open(root / "deadline.log", "w") as f:
        code = launcher.run_until(["bash", "-c", "sleep 300 & wait"], launcher.seconds_left(r), stdout=f)
    took = time.time() - t0
    verdict("F7", "deadline stops a running session", code == launcher.DEADLINE_EXIT and took < 30,
            f"exit {code} after {took:.0f} s; stop reason now: {launcher._stop_reason(r) or 'none'}")


@check("F14", "cloud budget cap")
def f14(root: pathlib.Path):
    st = root / "missions" / "cloudcap"
    b = budget.Budget(st)
    b.set_caps(runpod_usd=0.001, scaleway_eur=0.001)
    r = Run(load_config(), workspace=root, log_dir=st, allow_spend=True)
    ctx = Ctx(r, None)
    rp = infra.rp_create_pod(ctx, "frontier-verify-cap", "NVIDIA GeForce RTX 4090", max_hours=1)
    sc = infra.scw_create_instance(ctx, "frontier-verify-cap", "DEV1-S", max_hours=1, zone="nl-ams-1")
    verdict("F14", "cloud budget cap", rp.startswith("refused") and sc.startswith("refused") and not r.resources,
            f"Runpod: {rp[:80]} | Scaleway: {sc[:80]}")


@check("F18", "tamper-evident ledgers")
def f18(st: pathlib.Path, root: pathlib.Path):
    copy = root / "tamper"
    copy.mkdir(exist_ok=True)
    lines = (st / "llm.jsonl").read_text().splitlines()
    e = json.loads(lines[0])
    e["frontier.cost.usd"] = 0.0
    lines[0] = json.dumps(e)
    (copy / "llm.jsonl").write_text("\n".join(lines) + "\n")
    intact, edited = budget.verify_chain(st / "llm.jsonl"), budget.verify_chain(copy / "llm.jsonl")
    verdict("F18", "tamper-evident ledgers", intact is True and edited is not True,
            f"original: {intact}; copy with one cost set to 0: {edited}")


@check("F2", "no key value on disk")
def leak_scan(root: pathlib.Path):
    needles = ["verify-env-value-91b2", "verify-file-value-7a1c", "VERIFY-FILE-VALUE-7A1C"]
    hits = []
    for d in (root / "missions", root / "ws"):
        for p in d.rglob("*"):
            if p.is_file() and ".git" not in p.parts and any(n in p.read_text(errors="replace") for n in needles):
                hits.append(str(p))
    verdict("F2", "no key value on disk", not hits, f"files holding a test value: {hits or 'none'}")


@check("F14", "parallel creations under one cloud cap, and frontier reap")
def f14_live(root: pathlib.Path):
    """Two rp_create_pod at once under a cap that holds one: exactly one pod; then `frontier reap` (the CLI) deletes
    it once past its max_hours."""
    import concurrent.futures

    info = mission.prepare(write_mission(root, "parallel", hours=0.5), ping=False, offers=False)
    st = info["state"]
    os.environ["FRONTIER_ALLOW_SPEND"] = "1"
    srv = Server(state=str(st))
    offer = next(o for o in json.loads(srv.call("rp_offers", {})[0])["offers"] if o.get("usd_per_gpu_hour"))
    hours = 0.01  # 36 s
    budget.Budget(st).set_caps(runpod_usd=offer["usd_per_gpu_hour"] * hours * 1.5)  # room for one, not two
    args = {"name": "frontier-verify-parallel", "gpu_type_id": offer["gpu_type_id"], "max_hours": hours,
            "image": "python:3.12-slim", "disk_gb": 10, "command": "sleep infinity"}
    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        outs = list(ex.map(lambda _: srv.call("rp_create_pod", args), range(2)))
    made = [json.loads(o)["id"] for o, err in outs if not err and o.lstrip().startswith("{")]
    CREATED.extend(("runpod", pid) for pid in made)
    refused = [o[:120] for o, _ in outs if o.startswith("refused")]
    verdict("F14", "parallel creations under one cloud cap", len(made) == 1 and len(refused) == 1,
            f"{len(made)} pod created ({made}), {len(refused)} refused: {refused}")
    time.sleep(hours * 3600 + 10)
    p = subprocess.run([str(ROOT / "bin" / "frontier"), "reap", str(st)], capture_output=True, text=True, timeout=300)
    events = [e for e in budget.read_jsonl(st / "events.jsonl") if e.get("event") == "watchdog_delete"]
    gone = all(_rp_gone(pid) for pid in made)
    verdict("F15", "frontier reap", p.returncode == 0 and bool(made) and gone and len(events) == len(made),
            f"reap exit {p.returncode}; watchdog_delete events {len(events)}; get-pod 404 for {made}: {gone}")


@check("F7", "deadline stops a real harness session")
def f7_live(root: pathlib.Path):
    """A Codex mission of 90 s whose experiment is a 300 s command: the launcher ends the session at the deadline (exit
    124, `stopped: deadline`) and no process of the session is left."""
    sub = root / "files" / "deadline-live"
    sub.mkdir(parents=True, exist_ok=True)
    f = write_mission(root, "deadline-live", hours=0.025, llm_usd=0.1,
                      extra='[harness]\nname = "codex"\ntier = "light"\nreasoning = "low"\nmax_sessions = 1\n')
    text = f.read_text().replace('[harness]\nname = "codex"\n[llm]', '[llm]', 1)  # one [harness] section only
    f.write_text(text)
    (sub / "subject.md").write_text("# Subject\n\nThe experiment is the shell command `sleep 300`: run it first, in the "
                                    "foreground, and wait for it to finish. Then write its exit code in REPORT.md and deliver.\n")
    p = subprocess.run([sys.executable, "-m", "frontier", "mission", "start", str(f), "--no-ping"], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    st = root / "missions" / "deadline-live"
    ends = [e for e in budget.read_jsonl(st / "events.jsonl") if e.get("event") == "session_end"]
    left = subprocess.run(["pgrep", "-af", f"{st}|sleep 300"], capture_output=True, text=True).stdout.strip()
    ok = bool(ends) and ends[-1].get("code") == launcher.DEADLINE_EXIT and ends[-1].get("stopped") == "deadline" and not left
    verdict("F7", "deadline stops a real harness session", ok,
            f"launcher exit {p.returncode}; session_end {ends[-1] if ends else None}; processes left: {left or 'none'}")


# ---------------------------------------------------------------- features added since 2026-11-14


def _echo(gw_base: str, prompt: str) -> str:
    """One tiny Responses call through the gateway; the answer's text (the model repeats what was added to it)."""
    body = {"model": "gpt-6-luna", "input": prompt, "max_output_tokens": 400, "reasoning": {"effort": "low"}}
    req = urllib.request.Request(gw_base + "/openai/v1/responses", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {credentials.PLACEHOLDER}"})
    return urllib.request.urlopen(req, timeout=120).read().decode(errors="replace")


ECHO = "If this message contains a bracketed notice starting with [Frontier, repeat its first six words; else reply: none."


@check("F25", "Cerebras provider")
def f25(root: pathlib.Path):
    from frontier import llm

    p = subprocess.run([sys.executable, "-m", "frontier", "ping", "--provider", "cerebras", "--tier", "light"],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    st = root / "missions" / "cerebras"
    b = budget.Budget(st)
    b.set_caps(llm_usd=0.05)
    spec = llm.resolve(load_config(), "cerebras", "light", "low")
    r, why = b.metered_chat("verify", "cerebras", spec, [{"role": "user", "content": "Reply with exactly: ok-cerebras"}], [], 256)
    priced = [e for e in b.llm_entries() if e["gen_ai.provider.name"] == "cerebras"]
    verdict("F25", "Cerebras provider", p.returncode == 0 and r is not None and "ok-cerebras" in r.content and bool(priced),
            f"ping exit {p.returncode} ({p.stdout.strip().splitlines()[-1][:90] if p.stdout.strip() else p.stderr[-120:]}); "
            f"metered call answered {(r.content if r else why)[:40]!r} on {spec.model}; {len(priced)} entry priced, "
            f"{sum(e['frontier.cost.usd'] for e in priced):.6f} USD")


@check("F26", "budget milestone notices")
def f26(root: pathlib.Path):
    st = root / "missions" / "milestones"
    b = budget.Budget(st)
    b.set_caps(llm_usd=0.5)
    b.charge_llm("launcher", "verify", "openai", "gpt-6-luna", {}, usd=0.06)  # 12 % of the cap: the 10 % milestone
    gw = gateway.serve(str(st))
    answer = _echo(f"http://127.0.0.1:{gw.server_address[1]}", ECHO)
    gw.shutdown()
    ev = [e for e in budget.read_jsonl(st / "events.jsonl") if e.get("event") == "budget_milestone"]
    verdict("F26", "budget milestone notices", [(e["budget"], e["percent"]) for e in ev] == [("LLM", 10)]
            and "budget notice" in answer.lower(),
            f"events {[(e['budget'], e['percent']) for e in ev]}; the model saw the notice: {'budget notice' in answer.lower()}")


@check("F27", "snapshots: notices, silent early ones, tagged commits, merging")
def f27(root: pathlib.Path, st0: pathlib.Path):
    from frontier import snapshots

    st = root / "missions" / "snapshots"
    ws = pathlib.Path(json.loads((st0 / "mission.json").read_text())["workspace"])
    b = budget.Budget(st)
    b.set_caps(llm_usd=50, start=(budget.now() - budget.dt.timedelta(minutes=6)).isoformat())
    snapshots.write_settings(st, {"grace_minutes": 0.25})
    watchdog = snapshots.Snapshots(b)
    watchdog.notice()
    first = watchdog.take(ws)  # the 5-minute milestone is before notice_from_minutes: silent, taken at once
    b.set_caps(start=(budget.now() - budget.dt.timedelta(minutes=16)).isoformat())  # ten minutes later
    watchdog.notice()  # the watchdog ticks first, as it does every 10 s
    gw = gateway.serve(str(st))
    answer = _echo(f"http://127.0.0.1:{gw.server_address[1]}", ECHO)
    gw.shutdown()
    early = watchdog.take(ws)  # within the grace: nothing yet
    time.sleep(16)  # the 15-minute one has a grace of 15 s
    second = watchdog.take(ws)
    notices = [(e["label"], e["silent"]) for e in budget.read_jsonl(st / "events.jsonl") if e.get("event") == "snapshot_notice"]
    tags = subprocess.run(["git", "-C", str(ws), "tag", "--list", "snapshot-*"], capture_output=True, text=True).stdout.split()
    # merging: two milestones within one grace give one snapshot carrying both labels
    st2 = root / "missions" / "snapshots-merge"
    b2 = budget.Budget(st2)
    b2.set_caps(llm_usd=50, start=(budget.now() - budget.dt.timedelta(minutes=31)).isoformat())
    snapshots.write_settings(st2, {"grace_minutes": 0.05, "notice_from_minutes": 1})
    s2 = snapshots.Snapshots(b2)
    s2.notice()
    time.sleep(4)
    merged = s2.take(ws)
    ok = (notices == [("5min", True), ("15min", False)] and "snapshot notice" in answer.lower()
          and first and first["labels"] == ["5min"] and early is None and second and second["labels"] == ["15min"]
          and {"snapshot-5min", "snapshot-15min"} <= set(tags) and merged and merged["labels"] == ["5min", "15min", "30min"])
    verdict("F27", "snapshots: notices, silent early ones, tagged commits, merging", bool(ok),
            f"notices {notices}; the model saw the notice the watchdog recorded first: {'snapshot notice' in answer.lower()}; "
            f"snapshots {first and first['labels']} then {second and second['labels']}; tags {tags}; "
            f"merged {merged and merged['labels']}")


@check("F28", "local CPU meter")
def f28(root: pathlib.Path):
    from frontier import snapshots

    st = root / "missions" / "cpu"
    st.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "FRONTIER_STATE": str(st)}
    burn = "import time\nt=time.process_time()\nwhile time.process_time()-t<3: pass\ntime.sleep(2)"
    child = subprocess.Popen([sys.executable, "-c", f"import subprocess,sys; subprocess.run([sys.executable,'-c',{burn!r}])"],
                             env=env)  # a mission process whose child burns 3 s of CPU, then is reaped by it
    meter = snapshots.CpuMeter(st)
    peak = 0.0
    while child.poll() is None:
        peak = max(peak, meter.record())
        time.sleep(0.5)
    total = meter.record()
    again = snapshots.CpuMeter(st).record()  # a new launch on the same state adds to what was measured
    ev = [e for e in budget.read_jsonl(st / "events.jsonl") if e.get("event") == "cpu_usage"]
    verdict("F28", "local CPU meter", 2.5 <= total <= 4.5 and again >= round(total, 1) and bool(ev),
            f"{total:.1f} core-seconds measured for a 3 s burn (peak sample {peak:.1f}); {len(ev)} cpu_usage events; "
            f"a new meter on the same state starts from {again:.1f}")


@check("F29", "jury: anonymity, leak check, scoring v3, red flag")
def f29(root: pathlib.Path):
    from frontier import jury, scoring

    race = root / "race"
    for prov in ("openai", "zhipu"):
        info = mission.prepare(write_mission(race, f"team-{prov}", provider=prov), ping=False, offers=False)
        ws = info["workspace"]
        brand = {"openai": "OpenAI gpt-6-luna through Codex CLI", "zhipu": "Zhipu GLM-5.3 through ZCode"}[prov]
        (ws / "REPORT.md").write_text(f"# Report\n\nWe ({brand}) measured that 6 x 7 = 42 in `calc.py`, one run, "
                                      "no error bar.\n")
        (ws / "calc.py").write_text(f"# written by {brand}\nprint(6 * 7)\n")
        launcher.commit(ws, "work")
        (race / "missions" / f"team-{prov}" / "delivery.json").write_text(json.dumps(
            {"delivered_at": budget.now().isoformat(timespec="seconds"), "elapsed_hours": 0.5}))
    flag = subprocess.run([sys.executable, "-m", "frontier", "mission", "red-flag", str(race / "missions" / "team-zhipu"),
                           "--minutes", "10", "--reason", "provider outage"], capture_output=True, text=True, cwd=ROOT)
    bad_flag = subprocess.run([sys.executable, "-m", "frontier", "mission", "red-flag", str(race / "missions" / "team-zhipu"),
                               "--minutes", "-1", "--reason", "x"], capture_output=True, text=True, cwd=ROOT)
    out = root / "jury"
    res = jury.run(race / "missions", "Compute 6 x 7 and report it.", "# Race\n\nBudgets: 0.3 USD of LLM, 1 h.\n",
                   out, ["deepseek", "cerebras"], cap_usd=0.30)
    files = {p.name: p.read_text() for p in (out / "files").glob("Team *.md")}
    names = jury.identity_names(load_config())
    leaks = {n: jury.leaks(t, names) for n, t in files.items()}
    t = res["teams"]
    judged = {n: sorted(x["judges"]) for n, x in t.items()}
    errors = {n: x["errors"] for n, x in t.items() if x["errors"]}
    recomputed = {n: scoring.final(x["scores"]["scientific_quality"], x["scores"]["artifacts"], x["scores"]["efficiency"],
                                   x["penalty"], x["facts"]["disqualified"]) for n, x in t.items()}
    spent = budget.Budget(out).llm_spent()
    # the leak check: with the redaction switched off, no file is sent and no call is made
    real = jury.redact
    jury.redact = lambda text, n: (text, 0)
    try:
        leaky = jury.run(race / "missions", "Compute 6 x 7.", "# Race\n", root / "jury-leak", ["deepseek"], cap_usd=0.01)
    finally:
        jury.redact = real
    refused = {n: x["errors"].get("deepseek", "") for n, x in leaky["teams"].items()}
    ok = (flag.returncode == 0 and bad_flag.returncode == 2 and t["team-zhipu"]["facts"]["neutralised_minutes"] == 10
          and t["team-zhipu"]["facts"]["minutes"] == 20 and all(not v for v in leaks.values())
          and all(x["redactions"] > 0 for x in t.values()) and judged == {"team-openai": ["cerebras", "deepseek"],
                                                                          "team-zhipu": ["cerebras", "deepseek"]}
          and all(recomputed[n] == t[n]["final"] for n in t) and 0 < spent <= 0.30 and (out / "identities.json").exists()
          and all(r.startswith("not sent: names still found") for r in refused.values())
          and budget.Budget(root / "jury-leak").llm_spent() == 0)
    verdict("F29", "jury: anonymity, leak check, scoring v3, red flag", ok,
            f"red-flag exit {flag.returncode}, bad one {bad_flag.returncode}; zhipu minutes {t['team-zhipu']['facts']['minutes']} "
            f"({t['team-zhipu']['facts']['neutralised_minutes']} neutralised); redactions "
            f"{ {n: x['redactions'] for n, x in t.items()} }; names left in the judges' files {leaks}; judges {judged}; "
            f"errors {errors}; finals {[(n, x['final'], x['rank']) for n, x in t.items()]} = recomputed {recomputed}; "
            f"jury spent {spent:.4f} USD; redaction off: {list(refused.values())[0][:60]!r}, spent "
            f"{budget.Budget(root / 'jury-leak').llm_spent()}")


# ---------------------------------------------------------------- cleanup


def cleanup() -> None:
    for kind, rid in CREATED:
        try:
            if kind == "runpod":
                if not _rp_gone(rid):
                    infra.mcp_json("delete-pod", {"id": rid})
                print(f"cleanup runpod {rid}: gone={_rp_gone(rid)}")
            elif kind == "scaleway":  # deleted with its volumes and IPs if a failed check left it alive
                st, sid = rid.split("|")
                code, _ = scw_raw("GET", f"/instance/v1/zones/nl-ams-1/servers/{sid}")
                if code == 200:
                    infra._delete_scw_instance(Run(load_config(), workspace=ROOT, log_dir=pathlib.Path(st)), sid, "nl-ams-1")
                    code, _ = scw_raw("GET", f"/instance/v1/zones/nl-ams-1/servers/{sid}")
                print(f"cleanup scaleway {sid}: GET {code}")
            elif kind == "hf":
                hf._api().delete_repo(rid, repo_type="dataset")
                try:
                    hf._api().dataset_info(rid)
                    print(f"cleanup hf {rid}: STILL THERE")
                except Exception:  # noqa: BLE001 - not found is the expected answer
                    print(f"cleanup hf {rid}: gone")
        except Exception as e:  # noqa: BLE001
            print(f"cleanup {kind} {rid}: {e}")


# ---------------------------------------------------------------- coverage across processes


def coverage_cmd(action: str, root: pathlib.Path) -> int:
    rc = root / "coverage.rc"
    pth = pathlib.Path(site.getsitepackages()[0]) / "zz_frontier_coverage.pth"
    if action == "on":  # every Python process of this machine, the launcher's children included, records coverage
        rc.write_text(f"[run]\nsource = {ROOT / 'frontier'}\nparallel = True\ndata_file = {root / '.coverage'}\n")
        pth.write_text(f"import os; f = {str(rc)!r}; os.path.exists(f) and (os.environ.setdefault('COVERAGE_PROCESS_START', f), "
                       "__import__('coverage').process_startup())\n")
        print(f"coverage on: {pth}")
    elif action == "off":
        pth.unlink(missing_ok=True)
        print("coverage off")
    else:
        subprocess.run([sys.executable, "-m", "coverage", "combine", "--rcfile", str(rc)], cwd=root, check=False)
        subprocess.run([sys.executable, "-m", "coverage", "report", "--rcfile", str(rc), "--skip-empty"], cwd=root, check=False)
        subprocess.run([sys.executable, "-m", "coverage", "json", "--rcfile", str(rc), "-o", str(root / "coverage.json")],
                       cwd=root, check=False)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="directory for the verification's missions, workspaces and report")
    ap.add_argument("--only", default="", help="comma-separated feature ids")
    ap.add_argument("--skip-cloud", action="store_true", help="no Runpod, Scaleway or Hugging Face resource")
    ap.add_argument("--coverage", choices=["on", "off", "report"])
    a = ap.parse_args()
    root = pathlib.Path(a.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if a.coverage:
        return coverage_cmd(a.coverage, root)
    ONLY.update(x for x in a.only.split(",") if x)
    try:
        info = f1_f3(root)
        st = info["state"] if info else root / "missions" / "level1"
        f2_sources(root)
        srv = f9(st)
        if srv:
            f10_f11(srv, st)
            f21(srv)
        f5(st)
        if not a.skip_cloud:
            f12_f15(st)
            f13(st)
            f16(root, st)
        f6(root)
        f7(root)
        f14(root)
        if not a.skip_cloud:
            f14_live(root)
        f7_live(root)
        f18(st, root)
        f25(root)
        f26(root)
        f27(root, st)
        f28(root)
        f29(root)
        leak_scan(root)
    finally:
        cleanup()
    (root / "verify-report.json").write_text(json.dumps(RESULTS, indent=1) + "\n")
    bad = [r for r in RESULTS if r["verdict"] != "PASS"]
    print(f"\n{len(RESULTS) - len(bad)} PASS, {len(bad)} not PASS; report {root / 'verify-report.json'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
