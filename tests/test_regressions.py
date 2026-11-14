"""Regression tests for the defects found by the review of PR #2: each one failed before its fix."""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_frontier import FakeScaleway
from test_mission import _mission

from frontier import agent as agent_mod
from frontier import budget, gateway, hf, infra, launcher, mcp, mission, sandbox, tools
from frontier.config import load_config


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def run(cfg, tmp_path):
    return agent_mod.Run(cfg, workspace=tmp_path / "ws", log_dir=tmp_path / "log")


# ---------------------------------------------------------------- gateway


class _NoUsage(BaseHTTPRequestHandler):
    """A 2xx answer whose usage the gateway cannot read."""

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        data = b'{"output": "hi"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_gateway_charges_the_worst_case_when_usage_is_unknown(tmp_path):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _NoUsage)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    b = budget.Budget(tmp_path)
    gw = gateway.Gateway(("127.0.0.1", 0), b, {"zhipu": (f"http://127.0.0.1:{up.server_address[1]}", "zhipu")})
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    body = json.dumps({"model": "glm-5.3", "messages": [], "max_tokens": 1000}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/zhipu/api/paas/v4/chat/completions",
                                 data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
    gw.shutdown()
    up.shutdown()
    (e,) = b.llm_entries()
    assert e["frontier.cost.usd"] == budget.worst_case_usd("zhipu", "glm-5.3", len(body), 1000)
    assert e["frontier.price_exact"] is False and b.reserved() == 0


def test_gateway_reserves_at_the_requested_mode(cfg):
    def worst(**req):
        body = json.dumps({"input": "x", "max_output_tokens": 1000, **req}).encode()
        return gateway.worst_case(cfg, "openai", "/v1/responses", body) / budget.worst_case_usd(
            "openai", req["model"], len(body), 1000)

    assert worst(model="gpt-6-astra", service_tier="ultrafast") == pytest.approx(6.0)
    assert worst(model="gpt-6-astra") == pytest.approx(1.0)
    assert worst(model="gpt-6-luna", service_tier="ultrafast") == pytest.approx(1.0)  # a mode the model lacks


def test_only_generation_calls_are_metered():
    assert gateway.is_generation("POST", "/v1/responses") and gateway.is_generation("POST", "/v1/messages")
    assert gateway.is_generation("POST", "/v1beta/models/g:streamGenerateContent?alt=sse")
    for path in ("/v1/responses/r1/cancel", "/v1/messages/batches", "/v1/messages/count_tokens",
                 "/v1/responses/input_tokens"):
        assert not gateway.is_generation("POST", path), path
    assert not gateway.is_generation("GET", "/v1/responses")


# ---------------------------------------------------------------- launcher


def test_a_session_is_killed_at_the_deadline(tmp_path):
    t0 = time.time()
    with open(tmp_path / "log", "w") as f:
        code = launcher.run_until(["bash", "-c", "sleep 30 & echo $! > child; wait"], 1, cwd=tmp_path, stdout=f)
    assert time.time() - t0 < 10 and code == launcher.DEADLINE_EXIT
    child = int((tmp_path / "child").read_text())
    time.sleep(0.2)
    assert _dead(child)  # the harness's own children are killed too


def _dead(pid: int) -> bool:
    stat = pathlib.Path(f"/proc/{pid}/stat")  # gone, or a zombie left to a PID 1 that does not reap
    for _ in range(50):
        if not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return True
        time.sleep(0.1)
    return False


def test_an_interrupted_launcher_ends_the_session(tmp_path, monkeypatch):
    real_wait = subprocess.Popen.wait
    calls = []

    def interrupted(self, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            while not (tmp_path / "child").exists():
                time.sleep(0.01)
            raise KeyboardInterrupt  # Ctrl-C: the harness's group does not get it, being in its own session
        return real_wait(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupted)
    monkeypatch.setattr(launcher, "KILL_GRACE_S", 0.5)
    with pytest.raises(KeyboardInterrupt), open(tmp_path / "log", "w") as f:
        launcher.run_until(["bash", "-c", "trap '' TERM; sleep 30 & echo $! > child; wait"], None, cwd=tmp_path, stdout=f)
    assert _dead(int((tmp_path / "child").read_text()))  # even a group that ignores SIGTERM


# ---------------------------------------------------------------- sub-agents


def test_a_sub_agent_never_gets_tools_the_parent_lacks_even_when_none_are_left(run, monkeypatch):
    seen = []
    monkeypatch.setattr(agent_mod.Agent, "run_task", lambda self, task: seen.append(list(self.tools)) or "ok")
    small = agent_mod.Agent(run, "openai", "light", "low", tools=["shell"], depth=1)
    agent_mod.t_spawn_agent(agent_mod.Ctx(run, small), task="t", tools=["infra"])
    assert seen == [[]]


# ---------------------------------------------------------------- cloud


def test_a_server_that_outlives_the_wait_is_not_marked_deleted(run, monkeypatch):
    fake = FakeScaleway()
    monkeypatch.setattr(infra, "scw", fake)
    monkeypatch.setattr(infra, "POLL_S", 0)
    monkeypatch.setattr(infra, "DELETE_WAIT_S", 0.05)
    run.allow_spend = True
    infra.scw_create_instance(agent_mod.Ctx(run, None), "n", "L4-1-24G", max_hours=2)
    stuck = FakeScaleway.__call__

    def never_terminates(self, method, path, body=None, **kw):
        if path.endswith("/action"):
            self.calls.append((method, path))
            return {}
        return stuck(self, method, path, body, **kw)

    monkeypatch.setattr(FakeScaleway, "__call__", never_terminates)
    (res,) = infra.cleanup(run)
    assert res["server"].startswith("still there") and run.alive()  # still billed, retried by the watchdog
    monkeypatch.setattr(infra, "DELETE_WAIT_S", 30)
    t0 = time.time()
    (res,) = infra.cleanup(run)  # a retry checks once and does not wait again
    assert res["server"].startswith("still there") and time.time() - t0 < 5
    monkeypatch.setattr(FakeScaleway, "__call__", stuck)
    fake.server_alive = False
    (res,) = infra.cleanup(run)  # the server is gone: its IP, known from the ledger, is released too
    assert res["ips_deleted"] == ["ip"] and not run.alive()


def test_a_volume_still_attached_keeps_the_resource_alive(run, monkeypatch):
    fake = FakeScaleway()
    monkeypatch.setattr(infra, "scw", fake)
    monkeypatch.setattr(infra, "POLL_S", 0)
    monkeypatch.setattr(infra, "VOLUME_WAIT_S", 0.05)
    run.allow_spend = True
    infra.scw_create_instance(agent_mod.Ctx(run, None), "n", "L4-1-24G", max_hours=2)
    real = FakeScaleway.__call__
    monkeypatch.setattr(FakeScaleway, "__call__", lambda self, method, path, body=None, **kw: (
        {"status": "in_use", "references": ["x"]} if "/block/" in path and method == "GET" else real(self, method, path, body, **kw)))
    (res,) = infra.cleanup(run)
    assert res["volumes_left"] == ["vol"] and run.alive()


def test_parallel_creations_cannot_pass_the_same_budget_check(run, monkeypatch):
    fake = FakeScaleway()
    monkeypatch.setattr(infra, "scw", fake)
    run.allow_spend = True
    run.budget.set_caps(scaleway_eur=2.0)  # room for one machine: 0.7875 EUR/h x 2 h
    real = FakeScaleway.__call__
    created = iter(["srv1", "srv2"])

    def slow_create(self, method, path, body=None, **kw):
        if method == "POST" and path.endswith("/servers"):
            time.sleep(0.2)  # both threads are past their check when the first records its machine
            r = real(self, method, path, body, **kw)
            return {"server": {**r["server"], "id": next(created)}}
        return real(self, method, path, body, **kw)

    monkeypatch.setattr(FakeScaleway, "__call__", slow_create)
    out = []
    ts = [threading.Thread(target=lambda: out.append(infra.scw_create_instance(agent_mod.Ctx(run, None), "n", "L4-1-24G",
                                                                               max_hours=2))) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(run.alive()) == 1 and sum(o.startswith("refused") for o in out) == 1


# ---------------------------------------------------------------- workspace


def test_a_sibling_directory_with_the_same_prefix_is_outside_the_workspace(tmp_path, cfg):
    ws = tmp_path / "mission"
    (tmp_path / "mission-old").mkdir()
    (tmp_path / "mission-old" / "secret").write_text("s")
    r = agent_mod.Run(cfg, workspace=ws, log_dir=tmp_path / "log", settings={"allow_publish": True})
    ctx = agent_mod.Ctx(r, None)
    with pytest.raises(PermissionError):
        tools.t_read_file(ctx, "../mission-old/secret")
    assert hf.hf_publish(ctx, "x", "../mission-old").startswith("error")


# ---------------------------------------------------------------- mission settings


def test_renamed_key_variables_reach_every_reader(tmp_path, monkeypatch):
    for v in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "SCW_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID"):
        monkeypatch.setenv(v, "")  # recorded, so that what credentials.load writes is undone after the test
        monkeypatch.delenv(v)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-personal")  # an ambient default: the mission's own name wins
    monkeypatch.setenv("MY_OPENAI", "sk-mine")
    monkeypatch.setenv("MY_HF", "hf-mine")
    monkeypatch.setenv("MY_SCW", "scw-mine")
    monkeypatch.setenv("MY_PROJECT", "proj-1")
    f = _mission(tmp_path)
    f.write_text(f.read_text().replace('provider = "openai"', 'provider = "openai"\nkey_env = "MY_OPENAI"')
                 .replace("budget_eur = 3", 'budget_eur = 3\nkey_env = "MY_SCW"')
                 .replace("NO_SUCH_TOKEN_FOR_TESTS", "MY_HF") + '[secrets]\nSCW_DEFAULT_PROJECT_ID = "env:MY_PROJECT"\n')
    info = mission.prepare(f, ping=False, offers=False)
    assert os.environ["OPENAI_API_KEY"] == "sk-mine" and os.environ["SCW_SECRET_KEY"] == "scw-mine"
    assert hf._token() == "hf-mine" and info["settings"]["allow_publish"]
    monkeypatch.delenv("SCW_DEFAULT_PROJECT_ID")  # inside a sandbox only the settings carry it
    mission.apply_settings(info["settings"])
    assert os.environ["SCW_DEFAULT_PROJECT_ID"] == "proj-1"


def test_prepare_leaves_the_process_environment_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("FRONTIER_STATE", raising=False)
    mission.prepare(_mission(tmp_path), ping=False, offers=False)
    assert "FRONTIER_STATE" not in os.environ  # it reaches the harness through its own environment only


def test_costs_say_which_sessions_were_not_metered(tmp_path, cfg):
    r = agent_mod.Run(cfg, workspace=tmp_path / "ws", log_dir=tmp_path / "state")
    r.log(event="session_unmetered", n=3, note="stopped at the deadline before its usage report")
    st = r.budget.status()
    assert st["llm"]["unmetered_sessions"] == [{"n": 3, "note": "stopped at the deadline before its usage report"}]
    assert "Not included: the spend of 1 harness session(s)" in mission.costs_markdown(st, [], [])


def test_openshell_mission_without_a_name(tmp_path):
    f = _mission(tmp_path, harness="codex")
    f.write_text(f.read_text().replace('name = "t"\n', "", 1) + '\n[sandbox]\nkind = "openshell"\n')
    info = mission.prepare(f, ping=False, offers=False)
    box = sandbox.OpenShell(info, launcher.harness_conf("codex", "openai"), dry_run=True)
    assert box.name == "frontier-mission"


# ---------------------------------------------------------------- MCP


def test_mcp_calls_run_concurrently(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTIER_RUNS", str(tmp_path))
    srv = mcp.Server()
    release = threading.Event()

    def slow_call(name, args):
        if name == "spawn_agent":
            release.wait(5)
        return "done " + name, False

    monkeypatch.setattr(srv, "call", slow_call)
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "spawn_agent"}},
             {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    out = io.StringIO()
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in lines))
    t = threading.Thread(target=mcp.serve_stdio, args=(srv, inp, out))
    t.start()
    deadline = time.time() + 3
    while '"id": 2' not in out.getvalue() and time.time() < deadline:
        time.sleep(0.01)
    assert '"id": 2' in out.getvalue() and '"id": 1' not in out.getvalue()  # the ping did not wait
    release.set()
    t.join(5)
    assert '"id": 1' in out.getvalue()


# ---------------------------------------------------------------- runner activity


def test_runner_activity_is_kept_and_shown(tmp_path, cfg, monkeypatch):
    from frontier import dashboard

    state = tmp_path / "missions" / "t"
    r = agent_mod.Run(cfg, workspace=tmp_path / "ws", log_dir=state, allow_spend=True)
    (state / "mission.json").write_text(json.dumps({"name": "t"}))
    answers = {"exec-pod": {"exitCode": 0, "durationMs": 12, "stdout": "pytest: 61 passed\n", "stderr": "warn\n"},
               "stream-pod-logs": {"logs": ["boot ok", "job done"]}, "delete-pod": {}}
    monkeypatch.setattr(infra, "mcp_json", lambda tool, args=None: answers[tool])
    r.record({"provider": "runpod", "kind": "pod", "id": "p1", "type": "1x X", "usd_per_hour": 0.5, "max_hours": 1})
    ctx = agent_mod.Ctx(r, None)
    infra.rp_exec(ctx, "p1", "python -m pytest -q")
    infra.rp_delete_pod(ctx, "p1")
    log = (state / "runners" / "p1.log").read_text()
    for part in ("exec (exit 0, 12 ms): python -m pytest -q", "pytest: 61 passed", "--- stderr", "warn",
                 "logs before deletion", "job done", "deleted (deleted)"):
        assert part in log, part
    (runner,) = dashboard.snapshot(tmp_path / "missions")["teams"][0]["runners"]
    assert runner["state"] == "deleted" and runner["commands"] == 1 and "job done" in runner["log_tail"]


# ---------------------------------------------------------------- LLM cap seen by the launcher


def test_launcher_stops_when_the_gateway_refused_the_last_session(tmp_path, cfg):
    state = tmp_path / "state"
    r = agent_mod.Run(cfg, workspace=tmp_path / "ws", log_dir=state)
    r.budget.set_caps(llm_usd=0.000001)  # nothing spent, but no call fits in what is left
    r.log(event="session_start", n=1)
    gw = gateway.Gateway(("127.0.0.1", 0), r.budget, {"openai": ("http://127.0.0.1:9", "openai")}, cfg=cfg)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/openai/v1/responses",
                                 data=json.dumps({"model": "gpt-6-luna", "input": "hi"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    gw.shutdown()
    assert e.value.code == 402
    assert "LLM budget" in launcher._stop_reason(r)  # no session restarted only to be refused again


# ---------------------------------------------------------------- found by the fresh verification of the live run


def test_a_tool_that_raises_is_logged_as_an_error(tmp_path, monkeypatch):
    from frontier.tools import REGISTRY

    monkeypatch.setenv("FRONTIER_RUNS", str(tmp_path))
    srv = mcp.Server()

    def boom(ctx, **kw):
        raise infra.APIError("Runpod create-pod: HTTP 422")

    monkeypatch.setattr(REGISTRY["rp_pod"], "fn", boom)
    out, err = srv.call("rp_pod", {"pod_id": "x"})
    assert err and "422" in out
    (e,) = [e for e in budget.read_jsonl(srv.run.log_dir / "events.jsonl") if e.get("event") == "mcp_call"]
    assert e["tool"] == "rp_pod" and e["error"] is True and "422" in e["output"]


def test_costs_include_what_was_spent_after_delivery(tmp_path, cfg):
    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    ws = info["workspace"]
    for f in mission.REQUIRED:
        (ws / f).write_text("x\n")
    r = agent_mod.Run(cfg, workspace=ws, log_dir=info["state"], settings=info["settings"], allow_spend=True)
    r.budget.charge_llm("gateway", "harness", "openai", "gpt-6-luna", {"input": 1000, "output": 100})
    mission.deliver(agent_mod.Ctx(r, None), summary="done")
    r.budget.charge_llm("gateway", "harness", "openai", "gpt-6-luna", {"input": 2000, "output": 200})  # finishing its turn
    rec = mission.final_costs(r, ws)
    assert rec["final_llm_usd"] > rec["llm_usd"] and rec["llm_usd_after_delivery"] > 0
    costs = (ws / "COSTS.md").read_text()
    assert "after delivery" in costs and "of None" not in costs and "- scaleway: 0.0000 EUR of 3" in costs
