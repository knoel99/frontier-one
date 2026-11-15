"""Regression tests for the defects found by the review of PR #2: each one failed before its fix."""

from __future__ import annotations

import io
import datetime as dt
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
from frontier import budget, gateway, hf, infra, launcher, llm, mcp, mission, sandbox, tools
from frontier.config import load_config


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


class _CutStream(BaseHTTPRequestHandler):
    """An upstream that breaks mid-stream: one chunk of a chunked SSE answer, then the connection drops."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        d = b'data: {"id": "x", "model": "glm-5.3"}\n\n'
        self.wfile.write(b"%x\r\n" % len(d) + d + b"\r\nff\r\npartial")
        self.close_connection = True


def test_gateway_charges_a_stream_the_upstream_cut(tmp_path):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _CutStream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    b = budget.Budget(tmp_path)
    gw = gateway.Gateway(("127.0.0.1", 0), b, {"zhipu": (f"http://127.0.0.1:{up.server_address[1]}", "zhipu")})
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    body = json.dumps({"model": "glm-5.3", "messages": [], "max_tokens": 1000, "stream": True}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/zhipu/api/paas/v4/chat/completions",
                                 data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
    gw.shutdown()
    up.shutdown()
    (e,) = b.llm_entries()  # the provider billed the partial generation: so does the ledger, at the worst case
    assert e["frontier.price_exact"] is False and e["frontier.cost.usd"] > 0 and b.reserved() == 0


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
        try:
            if stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return True
        except (FileNotFoundError, ProcessLookupError):  # reaped between two looks (CI, 2026-11-15)
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


# ---------------------------------------------------------------- race without a sandbox: outbound filter, extra skills


def test_outbound_filter_blocks_denied_hosts_and_logs_every_host(tmp_path, monkeypatch):
    from frontier import egress

    for v in ("NO_PROXY", "no_proxy"):  # 127.0.0.1 stands for an allowed site: it must go through the filter
        monkeypatch.delenv(v, raising=False)

    assert egress.denied("knoel99.github.io", ["knoel99.github.io"]) and egress.denied("a.b.example.org", ["*.example.org"])
    assert not egress.denied("scheng2024.github.io", ["knoel99.github.io"]) and not egress.denied("github.io", ["knoel99.github.io"])
    up = ThreadingHTTPServer(("127.0.0.1", 0), _NoUsage)  # stands for any allowed site
    threading.Thread(target=up.serve_forever, daemon=True).start()
    e = egress.Egress(tmp_path, ["knoel99.github.io"])
    threading.Thread(target=e.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": e.url}))
    with pytest.raises(urllib.error.HTTPError) as err:
        opener.open("http://knoel99.github.io/milady-2027/", timeout=10)
    assert err.value.code == 403
    ok = opener.open(urllib.request.Request(f"http://127.0.0.1:{up.server_address[1]}/x", data=b"{}"), timeout=10).read()
    e.shutdown()
    up.shutdown()
    assert b"output" in ok
    log = [ev for ev in budget.read_jsonl(tmp_path / "events.jsonl") if ev.get("event") == "egress"]
    assert {(ev["host"], ev["allowed"]) for ev in log} == {("knoel99.github.io", False), ("127.0.0.1", True)}


def test_extra_skills_are_copied_next_to_the_frontier_skill(tmp_path):
    src = tmp_path / "skillrepo"
    (src / "skills" / "drawing").mkdir(parents=True)
    (src / "skills" / "drawing" / "SKILL.md").write_text("---\nname: drawing\n---\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "s"]):
        subprocess.run(["git", "-C", str(src), *cmd], check=True)
    rev = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    f = _mission(tmp_path)
    f.write_text(f.read_text() + f'\n[[skills]]\nrepo = "{src}"\nrev = "{rev}"\npath = "skills/drawing"\n')
    info = mission.prepare(f, ping=False, offers=False)
    ws = info["workspace"]
    assert (ws / ".agents/skills/drawing/SKILL.md").exists()  # beside .agents/skills/frontier (Codex)
    assert "`drawing`" in (ws / "TOOLS.md").read_text()


def test_reservation_caps_a_huge_requested_output_window(cfg):
    body = json.dumps({"model": "kimi-k3", "messages": [], "max_tokens": 262144}).encode()  # what Kimi Code sends
    capped = gateway.worst_case(cfg, "moonshot", "/v1/chat/completions", body)
    full = budget.worst_case_usd("moonshot", "kimi-k3", len(body), 262144)
    limit = cfg["limits"]["max_reserved_output_tokens"]
    assert capped == pytest.approx(budget.worst_case_usd("moonshot", "kimi-k3", len(body), limit)) and capped < full / 5


def test_mission_status_gives_the_team_its_dashboard_with_rate_and_projection(tmp_path, cfg):
    from frontier.status import mission_status

    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    r = agent_mod.Run(cfg, workspace=info["workspace"], log_dir=info["state"], settings=info["settings"], allow_spend=True)
    r.budget.charge_llm("gateway", "harness", "openai", "gpt-6-astra", {"input": 10_000, "output": 2_000})  # 0.2 USD now
    r.record({"provider": "runpod", "kind": "pod", "id": "p9", "type": "1x X", "usd_per_hour": 0.5, "max_hours": 1})
    d = json.loads(mission_status(agent_mod.Ctx(r, None)))
    assert d["llm"]["rate_usd_per_hour_last_10_min"] == pytest.approx(0.2 * 6, rel=0.01)
    assert d["llm"]["hours_until_cap_at_this_rate"] == pytest.approx((5 - 0.2) / 1.2, rel=0.01)
    assert d["cloud"]["runpod"]["committed_until_max_hours"] > 0 and d["machines"][0]["id"] == "p9"
    assert set(d) >= {"tokens", "sub_agents", "tool_calls", "sessions", "hours_left"}


def test_gateway_remaps_a_hard_wired_helper_model():
    from frontier.gateway import remap
    from frontier.launcher import model_map
    mm = model_map({"provider": "google"}, {"model_map": {"gemini-3.8-flash": "light"}})
    assert mm == {"gemini-3.8-flash": "gemini-4-hydrogen"}
    path, body = remap("/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse", b"{}", mm)
    assert path == "/v1beta/models/gemini-4-hydrogen:streamGenerateContent?alt=sse" and body == b"{}"
    assert remap("/v1beta/models/gemini-4-argon:generateContent", b"{}", mm)[0].endswith("gemini-4-argon:generateContent")
    _, body = remap("/v1/chat/completions", b'{"model": "gemini-3.8-flash"}', mm)
    assert json.loads(body)["model"] == "gemini-4-hydrogen"
    assert remap("/x", b"not json", mm) == ("/x", b"not json")


def test_gateway_serves_only_the_mission_providers(tmp_path):
    from frontier.gateway import serve
    gw = serve(str(tmp_path), 0, providers=["google"])
    try:
        assert set(gw.routes) == {"google"}
        u = f"http://127.0.0.1:{gw.server_address[1]}/deepseek/v1/chat/completions"
        req = urllib.request.Request(u, data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("a route of another provider was served")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        gw.shutdown()


def test_a_refusal_late_in_a_session_does_not_end_the_mission(tmp_path):
    """A late call carries the whole context; a fresh session's first call may still fit (race 2026-11-15, xAI)."""
    import time

    from frontier.budget import append_chained, now
    from frontier.launcher import _stop_reason
    cfg = load_config()
    r = agent_mod.Run(cfg, workspace=tmp_path / "ws", log_dir=tmp_path / "state")
    r.budget.set_caps(llm_usd=5.0)
    ev = tmp_path / "state" / "events.jsonl"
    append_chained(ev, {"t": now().isoformat(timespec="seconds"), "event": "session_start"})
    r.budget.charge_llm("gateway", "harness:x", "xai", "grok-4.7", {"input_tokens": 1000, "output_tokens": 100})
    time.sleep(1.1)
    append_chained(ev, {"t": now().isoformat(timespec="seconds"), "event": "llm_refused", "why": "worst case 0.80 > 0.79"})
    assert _stop_reason(r) == ""
    time.sleep(1.1)
    append_chained(ev, {"t": now().isoformat(timespec="seconds"), "event": "session_start"})
    append_chained(ev, {"t": now().isoformat(timespec="seconds"), "event": "llm_refused", "why": "worst case 0.80 > 0.79"})
    assert "fresh session" in _stop_reason(r)


def test_a_harness_failing_at_start_stops_the_mission(tmp_path):
    """Race 2026-11-15: the Google credit ran out and the launcher burned 18 sessions of 1-2 s each."""
    from frontier.budget import append_chained, now
    from frontier.launcher import _stop_reason
    r = agent_mod.Run(load_config(), workspace=tmp_path / "ws", log_dir=tmp_path / "state")
    r.budget.set_caps(llm_usd=5.0)
    ev = tmp_path / "state" / "events.jsonl"
    (tmp_path / "state" / "sessions").mkdir()
    for n in (1, 2, 3):
        append_chained(ev, {"t": now().isoformat(timespec="seconds"), "event": "session_start", "n": n})
        (tmp_path / "state" / "sessions" / f"{n:02d}.log").write_text("Error 402: prepayment credits are depleted")
        assert _stop_reason(r) == ""
        append_chained(ev, {"t": now().isoformat(timespec="seconds"), "event": "session_end", "n": n, "code": 3, "seconds": 1.5})
    assert "credits are depleted" in _stop_reason(r)


def test_the_operator_session_variables_do_not_reach_the_harness(monkeypatch, tmp_path):
    from frontier.launcher import build_session
    monkeypatch.setenv("CCR_AGENT_PROXY_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "remote_mobile")
    monkeypatch.setenv("GLOBAL_AGENT_HTTPS_PROXY", "http://127.0.0.1:1")
    info = {"settings": {"provider": "anthropic", "tier": "standard", "reasoning": "high"}, "workspace": str(tmp_path),
            "state": str(tmp_path), "egress": "http://127.0.0.1:2"}
    _, env = build_session(info, {"command": ["true"]}, "go", None)
    assert not [k for k in env if k.startswith(("CCR_", "CLAUDE_CODE_", "GLOBAL_AGENT_"))]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:2"


def test_hub_calls_survive_a_bracketed_ipv6_no_proxy(monkeypatch):
    """Race 2026-11-15: Kimi Code's NO_PROXY held "[::1]" and httpx refused every Hub call, so no delivery copy."""
    from frontier import hf
    monkeypatch.setenv("NO_PROXY", "localhost,[::1],api.example.com")
    hf._bracketless_no_proxy()
    assert os.environ["NO_PROXY"] == "localhost,::1,api.example.com"


def test_dashboard_skips_json_lines_that_are_not_objects(tmp_path):
    """Race 2026-11-15: a session log line holding a JSON list ("tool_use" inside) stopped every dashboard snapshot."""
    from frontier.dashboard import subagents
    log = tmp_path / "01.log"
    log.write_text('[{"type": "tool_use"}]\n"tool_use"\n{"type": "assistant", "message": {"content": [{"type": "tool_use", '
                   '"name": "Agent", "input": {"description": "read papers"}}]}}\n')
    out = subagents([], [], [log])
    assert [r["task"] for r in out if r["kind"] == "native"] == ["read papers"]


def test_dsh_patch_sets_the_main_model_and_effort():
    """Race 2026-11-15: dsh ignored DSH_MODEL and ran deepseek-flash, its profile default, instead of deepseek-v4-pro."""
    from frontier.harnesses import McpCommand, artifacts
    files = artifacts(McpCommand("frontier-mcp", ["--state", "/s"]), ["dsh"],
                      {"provider": "deepseek", "model": "deepseek-v4-pro", "effort": "high", "gateway": "http://127.0.0.1:1"})
    patch = files[".dsh/frontier.patch.yml"]
    assert "- id: agent-default-model" in patch and "model: deepseek-v4-pro" in patch and "reasoningEffort: high" in patch


def test_two_model_providers_run_their_frontier_model_on_standard():
    cfg = load_config()
    assert llm.resolve(cfg, "moonshot", "standard", "high").model == cfg["providers"]["moonshot"]["tiers"]["frontier"]
    assert llm.resolve(cfg, "zhipu", "standard", "high").model == cfg["providers"]["zhipu"]["tiers"]["frontier"]


def test_budget_milestones_reach_the_main_agent(tmp_path):
    """The gateway tells the harness when a budget crosses 10, 25, 50, 80, 95 and 98 %: on the next calls only."""
    from frontier.milestones import NOTICE_CALLS, Milestones, inject
    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=1.0, start=budget.now().isoformat(), deadline=(budget.now() + dt.timedelta(hours=1)).isoformat())
    ms = Milestones(b)
    assert ms.notice() == ""
    b.charge_llm("gateway", "harness:x", "openai", "gpt-6.1-terra", {}, usd=0.30)
    notes = [ms.notice() for _ in range(NOTICE_CALLS + 1)]
    assert "LLM budget: 25 % reached" in notes[0] and "0.30 of 1.00 USD" in notes[0] and notes[-1] == ""
    events = [e for e in budget.read_jsonl(tmp_path / "events.jsonl") if e["event"] == "budget_milestone"]
    assert [e["percent"] for e in events] == [10, 25]
    assert Milestones(b).notice() == ""  # a restarted gateway does not repeat a milestone already told
    note = "[notice]"
    anth = json.loads(inject("/v1/messages", json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "x"}]}]}).encode(), note))
    assert anth["messages"][-1]["content"][-1] == {"type": "text", "text": note}
    chat = json.loads(inject("/v1/chat/completions", json.dumps({"messages": [{"role": "tool", "content": "x"}]}).encode(), note))
    assert chat["messages"][-1] == {"role": "user", "content": note}
    resp = json.loads(inject("/v1/responses", json.dumps({"input": [{"role": "user", "content": "hi"}]}).encode(), note))
    assert resp["input"][-1]["content"][0]["text"] == note
    gem = json.loads(inject("/v1beta/models/m:generateContent", json.dumps({"contents": [{"role": "user", "parts": []}]}).encode(), note))
    assert gem["contents"][-1] == {"role": "user", "parts": [{"text": note}]}
    assert inject("/v1/messages", b'{"messages": [{"role": "assistant", "content": "pre"}]}', note).endswith(b'"pre"}]}')


def test_an_empty_reservations_file_does_not_stop_the_gateway(tmp_path):
    """A process killed while writing left reservations.json empty: every later call failed (JSONDecodeError)."""
    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=1.0)
    (tmp_path / "reservations.json").write_text("")
    rid, why = b.reserve(0.1, "x")
    assert rid and why == "" and b.reserved() == 0.1
    assert not (tmp_path / "reservations.json.tmp").exists()


def test_report_paths_finds_workspace_paths_only():
    """Race 2026-11-15: every report cited workspace paths (code/evaluate.py, results/metrics.json, DECISIONS.md)."""
    from frontier.mission import report_paths
    text = ("See `code/evaluate.py` and `results/` for the run; [the table](results/table.md); DECISIONS.md says why.\n"
            "The model `alibaba/qwen3.8-flash`, the rate `dE/dt` and `1/Re` are not paths; nor is https://x.org/a.py.\n"
            "![Fields](figures/fields.png){width=100%}\n\\includegraphics[width=\\linewidth]{fig/a.pdf}\n")
    assert report_paths(text) == ["code/evaluate.py", "results/", "results/table.md", "DECISIONS.md"]
    assert report_paths("Figure 2 and Table 1; the delivery dataset on the Hub, [here](https://huggingface.co/x).") == []


def test_deliver_refuses_a_report_that_names_paths(run, tmp_path):
    from frontier.agent import Ctx
    from frontier.mission import deliver
    ws = run.workspace
    ws.mkdir(parents=True, exist_ok=True)
    for f in ("DELIVERY.md", "COMPLIANCE.md", "DECISIONS.md"):
        (ws / f).write_text("x\n")
    (ws / "REPORT.md").write_text("Results are in `results/metrics.json`.\n")
    out = deliver(Ctx(run, None))
    assert out.startswith("refused: the report names files") and "results/metrics.json" in out
    assert not (run.log_dir / "delivery.json").exists()


def test_jury_files_are_anonymous():
    from frontier.config import load_config
    from frontier.jury import identity_names, incidents_of, leaks, pseudonyms, redact
    names = identity_names(load_config())
    text = ("Produced by Claude Sonnet 5.5 in Claude Code; data on knoel/milady-20261115-race-moonshot-delivery; "
            "model qwen3.8-flash via DashScope; the dsh harness. We continue with the grokking curve.")
    out, n = redact(text, names)
    assert not leaks(out, names) and n >= 5
    assert "Produced by [redacted] in [redacted];" in out and "5.5" not in out and "continue" in out
    head, inc = incidents_of("General.\n\n## Incidents\n\nIntro.\n\n### xai\n\nStopped at budget.\n\n### openai\n\nnone.\n")
    assert head == "General." and inc == {"xai": "Stopped at budget.", "openai": "none."}
    labels = pseudonyms(["a", "b", "c"])
    assert sorted(labels.values()) == ["Team A", "Team B", "Team C"]


def test_gateway_strips_message_fields_a_provider_refuses():
    """Cerebras refuses the reasoning_content OpenCode sends back in the history (400, 2026-11-15)."""
    from frontier.gateway import strip_fields
    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"},
                                                  {"role": "assistant", "content": "ok", "reasoning_content": "..."}]}).encode()
    out = json.loads(strip_fields(body, ["reasoning_content", "reasoning"]))
    assert out["messages"][1] == {"role": "assistant", "content": "ok"}
    assert strip_fields(body, []) == body and strip_fields(b"x", ["a"]) == b"x"
    cfg = load_config()
    assert cfg["providers"]["cerebras"]["strip_message_fields"] == ["reasoning_content", "reasoning"]
    assert llm.resolve(cfg, "cerebras", "standard", "high").model == cfg["providers"]["cerebras"]["tiers"]["frontier"]


def test_opencode_runs_in_the_workspace_whatever_the_launcher_directory():
    from frontier.launcher import harness_conf
    assert ["--dir", "{workspace}"] == harness_conf("opencode", "cerebras")["command"][3:5]


def test_streamed_answers_are_rebuilt_whole():
    """Responses and Gemini calls are streamed (a proxy cut idle connections at 180 s on 2026-11-15)."""
    done = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "status": "completed"}
    assert llm.responses_from_events([{"type": "response.output_text.delta", "delta": "o"},
                                      {"type": "response.completed", "response": done}]) == done
    with pytest.raises(llm.LLMError):
        llm.responses_from_events([{"type": "response.output_text.delta"}])
    g = llm.gemini_from_chunks([{"candidates": [{"content": {"parts": [{"text": "a", "thoughtSignature": "s"}]}}]},
                                {"candidates": [{"content": {"parts": [{"text": "b"}]}, "finishReason": "STOP"}],
                                 "usageMetadata": {"promptTokenCount": 3}}])
    assert [x["text"] for x in g["candidates"][0]["content"]["parts"]] == ["a", "b"]
    assert g["candidates"][0]["finishReason"] == "STOP" and g["usageMetadata"] == {"promptTokenCount": 3}


def test_a_slow_server_failure_is_not_retried(monkeypatch):
    """Race jury 2026-11-15: a proxy cut calls at 180 s and every one was retried 4 times, each billed by the provider."""
    real_time = llm.time.time  # the module's own function, kept before it is patched
    calls = []

    def slow_502(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 502, "upstream request failed", {}, io.BytesIO(b"cut"))

    clock = iter([0.0, 200.0] * 10)
    monkeypatch.setattr(llm.time, "time", lambda: next(clock))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.urllib.request, "urlopen", slow_502)
    with pytest.raises(llm.LLMError):
        llm._post("https://x/y", {}, {})
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(llm.time, "time", real_time)
    with pytest.raises(llm.LLMError):
        llm._post("https://x/y", {}, {}, retries=2)
    assert len(calls) == 3  # a fast failure is still retried


def test_a_stream_cut_after_it_started_is_not_retried_and_is_charged(run, monkeypatch):
    """A stream that started was run by the provider: sending it again would pay for it twice, unseen."""
    calls = []

    class Cut:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            yield b'data: {"type": "response.created"}\n'
            raise ConnectionResetError("reset mid-stream")

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout=0: calls.append(1) or Cut())
    with pytest.raises(llm.LLMError) as e:
        llm._post_sse("https://x/y", {}, {})
    assert len(calls) == 1 and e.value.maybe_billed

    def billed(*a, **kw):
        raise llm.LLMError("network error", maybe_billed=True)

    monkeypatch.setattr(llm, "chat", billed)
    a = agent_mod.Agent(run, "openai", "light", "low", tools=[])
    with pytest.raises(llm.LLMError):
        a.run_task("t")
    (entry,) = run.budget.llm_entries()
    assert entry["frontier.price_exact"] is False and entry["frontier.cost.usd"] > 0 and run.budget.reserved() == 0


def test_shared_state_files_are_replaced_whole(run):
    """budgets.json and resources.json are read by other processes (gateway, dashboard): never written in place."""
    run.budget.set_caps(llm_usd=5)
    run.record({"provider": "runpod", "id": "p1"})
    files = [run.log_dir / "budgets.json", run.log_dir / "resources.json"]
    inodes = [f.stat().st_ino for f in files]
    run.budget.set_caps(llm_usd=6)
    run.record({"provider": "runpod", "id": "p2"})
    assert all(f.stat().st_ino != i for f, i in zip(files, inodes, strict=True))  # a new file, renamed over the old
    assert run.budget.caps["llm_usd"] == 6 and [r["id"] for r in run.resources] == ["p1", "p2"]
    assert not list(run.log_dir.glob("*.tmp"))


def test_scoring_log_scales_and_relative_efficiency():
    from frontier import scoring
    assert scoring.log_score(5, 5) == 100 and scoring.log_score(10, 5) == 90 and scoring.log_score(40, 5) == 70
    assert scoring.log_score(5 * 2 ** 12, 5) == 0
    assert scoring.relative(10, [40]) == 100 and scoring.relative(40, [40]) == 50 and scoring.relative(160, [40]) == 0
    # a team or a peer that spent nothing (an unmetered harness) neither crashes the jury nor gets par
    assert scoring.relative(0, [40]) == 100 and scoring.relative(40, [0]) == 0 and scoring.relative(0, [0]) == 50
    teams = {"fast": {"quality": 15, "usd": 2, "minutes": 20, "classified": True},
             "slow": {"quality": 12, "usd": 8, "minutes": 80, "classified": True},
             "empty": {"quality": 2, "usd": 1, "minutes": 5, "classified": True},
             "out": {"quality": 30, "usd": 1, "minutes": 5, "classified": False}}
    e = scoring.efficiency(teams)
    assert e["fast"]["peers"] == ["slow"] and e["fast"]["relative"] == 100  # 4x cheaper and 4x faster than its peer
    assert e["slow"]["relative"] == 0 and e["out"]["efficiency"] == 0
    assert e["empty"]["guard"] == 0.2 and e["empty"]["efficiency"] == 15.0  # no peer (relative 50), scaled down by its science
    assert scoring.science({"soundness": 20, "significance": 10, "positioning": 40, "clarity": 50}, "partial") == \
        pytest.approx((20 * 20 + 15 * 10 + 5 * 40 + 10 * 50) / 50 * 0.5)
    assert scoring.final(20, 40, 60, penalty=5) == pytest.approx((50 * 20 + 25 * 40 + 25 * 60) / 100 - 5)
    assert scoring.final(20, 40, 60, disqualified=True) == 0


def test_jury_aggregate_ranks_classified_teams_first_and_keeps_ungraded_work_unranked():
    from frontier.jury import aggregate
    rub = lambda q, a: {"scientific_quality": {"rubrics": {k: {"score": q} for k in ("soundness", "significance",  # noqa: E731
                                                                                     "positioning", "clarity")},
                                               "adequacy": "on_subject"},
                        "artifacts": {"rubrics": {k: {"score": a} for k in ("reproducibility", "code", "data", "traceability")}}}
    f = {"team": "a", "classified": True, "delivered": True, "total_usd": 3.0, "minutes": 40, "penalties": [],
         "disqualified": False}
    teams = {"a": {"facts": f, "judges": {"x": rub(10, 30), "y": rub(14, 30)}, "has_work": True},
             "b": {"facts": {**f, "team": "b", "classified": False, "delivered": False}, "judges": {"x": rub(60, 60)},
                   "has_work": True},
             "c": {"facts": {**f, "team": "c"}, "judges": {}, "has_work": True}}
    res = aggregate(teams, 0.0)
    assert res["ranking"][:2] == ["a", "b"] and res["teams"]["c"]["final"] is None and res["teams"]["c"]["rank"] is None
    assert res["teams"]["a"]["scores"]["scientific_quality"] == 12.0  # the judges' median, rubric by rubric



def test_red_flag_appends_a_chained_event_the_jury_reads(tmp_path):
    from frontier.__main__ import red_flag
    from frontier.budget import verify_chain

    (tmp_path / "events.jsonl").write_text("")
    assert red_flag(tmp_path, 27, "framework failure") == 0
    assert red_flag(tmp_path, None, "x") == 2
    ev = [json.loads(ln) for ln in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert ev[-1]["event"] == "red_flag" and ev[-1]["minutes"] == 27.0
    assert verify_chain(tmp_path / "events.jsonl") is True


def test_jury_sees_the_team_files_only_and_all_its_code(tmp_path):
    """Judges read the team's work: never the harness files the framework wrote, and every file of code it delivered."""
    import subprocess

    from frontier import jury
    ws = tmp_path / "ws"
    ws.mkdir()
    for name, text in {"résultat.py": "x = 1\n", "model.rs": "fn main() {}\n", "report.md": "# R\n",
                       ".mcp.json": "{}\n", "opencode.json": "{}\n", ".opencode/agents/reviewer.md": "role\n"}.items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(text)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    assert sorted(jury._tracked(ws)) == ["model.rs", "report.md", "résultat.py"]
    f = {k: 0 for k in ("delivered", "minutes", "neutralised_minutes", "duration_hours", "llm_usd", "llm_cap_usd",
                        "simulation_usd", "rented_usd", "local_cpu_core_hours", "simulation_cap_usd", "sessions",
                        "deliverables", "ledgers_intact")}
    text = jury.team_file(ws, f, "Team A", "")
    assert "### résultat.py" in text and "### model.rs" in text
    assert "opencode.json" not in text and ".mcp.json" not in text and "role" not in text
    assert jury.deliverables(tmp_path / "missing", tmp_path)["code"] is False


def test_concurrent_set_caps_keep_every_update(tmp_path):
    b = budget.Budget(tmp_path)
    keys = [f"k{i}" for i in range(40)]
    threads = [threading.Thread(target=b.set_caps, kwargs={k: 1}) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(keys) <= set(b.caps)


def test_the_launcher_deletes_what_a_delivery_left_alive(tmp_path, cfg, monkeypatch):
    """deliver's cleanup can leave a Scaleway terminate unconfirmed after 10 min: the launcher must retry it, not exit."""
    ws = tmp_path / "ws"
    ws.mkdir()
    r = agent_mod.Run(cfg, workspace=ws, log_dir=tmp_path / "state")
    r.record({"provider": "scaleway", "kind": "instance", "id": "srv-1", "zone": "fr-par-2", "eur_per_hour": 1.0, "max_hours": 1})
    r.mark("srv-1", "deleting")
    (r.log_dir / "delivery.json").write_text("{}\n")
    seen = []
    monkeypatch.setattr(infra, "cleanup", lambda run: seen.append([x["id"] for x in run.alive()]) or [])
    launcher.finish(r, ws)
    assert seen == [["srv-1"]] and (ws / "COSTS.md").exists()
    seen.clear()
    launcher.finish(r, ws, keep_resources=True)
    assert seen == []


def test_raw_cloud_calls_cannot_create_outside_the_budget(run, monkeypatch):
    """With spending allowed, `scaleway POST .../servers` or `runpod_mcp create-pod` billed a machine that no budget
    check saw, no ledger held and no cleanup deleted."""
    run.allow_spend = True
    called = []
    monkeypatch.setattr(tools, "scw_raw", lambda *a, **k: called.append(a) or (200, "{}"))
    monkeypatch.setattr(tools, "mcp_call", lambda *a, **k: called.append(a) or (True, "{}"))
    ctx = agent_mod.Ctx(run, None)
    assert tools.t_scaleway(ctx, "POST", "/instance/v1/zones/fr-par-2/servers", {}).startswith("HTTP 200")  # no cap
    run.budget.set_caps(scaleway_eur=10, runpod_usd=10)
    for path in ("/instance/v1/zones/{zone}/servers", "/block/v1/zones/fr-par-2/volumes", "instance/v1/zones/fr-par-1/ips"):
        assert tools.t_scaleway(ctx, "POST", path, {}).startswith("refused"), path
    assert tools.t_runpod_mcp(ctx, "create-pod", {}).startswith("refused")
    assert tools.t_runpod_mcp(ctx, "deploy-hub-repo", {}).startswith("refused")
    # level 4 check 2026-11-15: the Runpod MCP server has billed creators beyond the bundled catalog
    assert tools.t_runpod_mcp(ctx, "create-cluster", {}).startswith("refused")
    assert tools.t_runpod_mcp(ctx, "create-global-volume", {}).startswith("refused")
    assert len(called) == 1
    s1, p1 = "11111111-1111-1111-1111-111111111111", "p1"  # the run's own server and pod
    run.record({"provider": "scaleway", "kind": "instance", "id": s1, "volumes": [], "ips": []})
    run.record({"provider": "runpod", "kind": "pod", "id": p1})
    assert tools.t_scaleway(ctx, "POST", f"/instance/v1/zones/fr-par-2/servers/{s1}/action", {"action": "poweroff"}).startswith("HTTP")
    assert tools.t_scaleway(ctx, "POST", "/instance/v1/zones/fr-par-2/security_groups", {}).startswith("HTTP")  # not billed
    assert tools.t_runpod_mcp(ctx, "delete-pod", {"id": p1}) == "{}"


def test_raw_cloud_calls_leave_alone_what_the_run_did_not_create(run, monkeypatch):
    """Race check 2026-11-15: the teams of a race share the cloud accounts, and prompts/cloud.md states that resources
    the run did not create are not touched; yet a raw `runpod_mcp delete-pod` or `scaleway DELETE .../servers/<id>`
    went through for any id, and `pod-action start` billed a stopped pod outside the budget."""
    run.allow_spend = True
    called = []
    monkeypatch.setattr(tools, "scw_raw", lambda *a, **k: called.append(a) or (200, "{}"))
    monkeypatch.setattr(tools, "mcp_call", lambda *a, **k: called.append(a) or (True, "{}"))
    ctx = agent_mod.Ctx(run, None)
    run.budget.set_caps(scaleway_eur=10, runpod_usd=10)
    other = "22222222-2222-2222-2222-222222222222"
    for tool, args in (("delete-pod", {"id": "theirs"}), ("pod-action", {"id": "theirs", "action": "start"}),
                       ("update-pod", {"id": "theirs"}), ("exec-pod", {"podId": "theirs", "command": ["ls"]}),
                       ("delete-endpoint", {"id": "theirs"}), ("delete-network-volume", {"id": "theirs"})):
        assert tools.t_runpod_mcp(ctx, tool, args).startswith("refused"), tool
    for method, path in (("DELETE", f"/instance/v1/zones/fr-par-2/servers/{other}"),
                         ("POST", f"/instance/v1/zones/fr-par-2/servers/{other}/action"),
                         ("DELETE", f"/block/v1/zones/fr-par-2/volumes/{other}"), ("PATCH", f"/instance/v1/zones/fr-par-2/ips/{other}"),
                         ("DELETE", f"/serverless-jobs/v1alpha1/regions/fr-par/job-definitions/{other}"),  # level 4 check
                         ("POST", f"/serverless-jobs/v1alpha1/regions/fr-par/job-runs/{other}/stop")):
        assert tools.t_scaleway(ctx, method, path, {}).startswith("refused"), path
    assert not called
    assert tools.t_runpod_mcp(ctx, "list-pods", {}) == "{}"  # reading is not touching
    assert tools.t_scaleway(ctx, "GET", f"/instance/v1/zones/fr-par-2/servers/{other}").startswith("HTTP")


def test_a_sub_agent_call_is_charged_before_its_reservation_is_released(run, monkeypatch):
    """Released first, the reservation left a gap in which a parallel call could take the same room: both ran over
    the hard cap together."""
    monkeypatch.setattr(llm, "chat", lambda spec, m, t, max_tokens=0: llm.Reply(
        "done", [], {"input": 100, "output": 10}, {"role": "assistant", "content": "done"}))
    real_release, seen = run.budget.release, []
    monkeypatch.setattr(run.budget, "release", lambda rid: seen.append(run.budget.llm_spent()) or real_release(rid))
    agent_mod.Agent(run, "openai", "light", "low", tools=[]).run_task("hi")
    assert seen and seen[0] > 0  # the spend was in the ledger when the room was given back


class _SlowAnswer(BaseHTTPRequestHandler):
    """A JSON answer with its usage, sent after the client has hung up."""

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        time.sleep(0.5)
        data = b'{"model": "glm-5.3", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}' + b" " * 300_000
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_gateway_charges_an_answer_the_harness_hung_up_on(tmp_path):
    """A harness that times out on a long call: the provider billed it, the write to the harness fails, and the
    charge came after that write."""
    import socket
    import struct

    up = ThreadingHTTPServer(("127.0.0.1", 0), _SlowAnswer)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    b = budget.Budget(tmp_path)
    gw = gateway.Gateway(("127.0.0.1", 0), b, {"zhipu": (f"http://127.0.0.1:{up.server_address[1]}", "zhipu")})
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    body = json.dumps({"model": "glm-5.3", "messages": [], "max_tokens": 1000}).encode()
    c = socket.create_connection(gw.server_address)
    c.sendall(b"POST /zhipu/api/paas/v4/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
              b"Content-Length: %d\r\n\r\n" % len(body) + body)
    time.sleep(0.1)
    c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # hang up with a reset
    c.close()
    for _ in range(50):
        if b.llm_entries():
            break
        time.sleep(0.1)
    gw.shutdown()
    up.shutdown()
    (e,) = b.llm_entries()
    assert e["gen_ai.usage.input_tokens"] == 1000 and e["frontier.price_exact"] is True and b.reserved() == 0


def test_a_stream_that_ends_without_its_final_event_is_charged(run, monkeypatch):
    """The stream started (the provider ran the call) but closed before response.completed: neither resent nor
    charged before."""
    monkeypatch.setattr(llm, "_post_sse", lambda *a, **k: [{"type": "response.created", "response": {"id": "r"}}])
    with pytest.raises(llm.LLMError):
        agent_mod.Agent(run, "openai", "light", "low", tools=[]).run_task("hi")
    (e,) = run.budget.llm_entries()
    assert e["frontier.price_exact"] is False and e["frontier.cost.usd"] > 0 and run.budget.reserved() == 0


def test_every_judge_call_is_charged_and_held_under_the_jury_cap(tmp_path, cfg, monkeypatch):
    """A judge that first answered in prose was charged for its retry only; parallel judges only checked the spend
    already written, so six of them in flight overran the cap together."""
    from frontier import jury

    rub = {"scientific_quality": {"rubrics": {k: {"score": 50} for k in ("soundness", "significance", "positioning", "clarity")},
                                  "adequacy": "on_subject"},
           "artifacts": {"rubrics": {k: {"score": 50} for k in ("reproducibility", "code", "data", "traceability")}}}
    answers = iter(["I think the team did well.", json.dumps(rub)])
    monkeypatch.setattr(llm, "chat", lambda spec, m, t, max_tokens=0: llm.Reply(
        next(answers), [], {"input": 1000, "output": 100}, {"role": "assistant", "content": "x"}))
    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=100)
    spec = llm.resolve(cfg, "openai", "frontier", "high")
    assert jury.judge(b, spec, "brief", "subject", "context", "file") == rub
    assert [e["gen_ai.agent.name"] for e in b.llm_entries()] == ["judge", "judge"] and b.reserved() == 0
    b.set_caps(llm_usd=b.llm_spent() + 0.0001)  # no worst case fits in what is left
    with pytest.raises(ValueError, match="LLM budget"):
        jury.judge(b, spec, "brief", "subject", "context", "file")


def test_the_gateway_and_the_watchdog_write_a_milestone_once(tmp_path, monkeypatch):
    """Each had its own Snapshots and its own thread lock: both read the events, both wrote the same notice, and the
    snapshot carried the label twice."""
    from frontier import snapshots

    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=50, start=(budget.now() - dt.timedelta(minutes=6)).isoformat())
    snapshots.write_settings(tmp_path, {})
    pair = [snapshots.Snapshots(b), snapshots.Snapshots(b)]
    real = snapshots.Snapshots.crossed
    monkeypatch.setattr(snapshots.Snapshots, "crossed", lambda self: time.sleep(0.2) or real(self))
    threads = [threading.Thread(target=s.notice) for s in pair]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ev = [e["label"] for e in budget.read_jsonl(tmp_path / "events.jsonl") if e["event"] == "snapshot_notice"]
    assert ev == ["5min"]


def test_parallel_publications_are_all_recorded(tmp_path, cfg, monkeypatch):
    """publications.json was read, then rewritten: two publications in flight (a sub-agent and the harness) kept one."""
    r = agent_mod.Run(cfg, workspace=tmp_path, log_dir=tmp_path / "s",
                      settings={"allow_publish": True, "hf_namespace": "me"})
    for n in ("a", "b"):
        (tmp_path / n).mkdir()
        (tmp_path / n / "README.md").write_text("---\nlicense: mit\n---\n" + "Dataset of runs. " * 20)

    class SlowApi:
        def create_repo(self, repo_id, **kw):
            pass

        def upload_folder(self, **kw):
            time.sleep(0.2)
            return None

    real = hf._file_in_race
    monkeypatch.setattr(hf, "_api", lambda: SlowApi())
    monkeypatch.setattr(hf, "_file_in_race", lambda *a: time.sleep(0.3 if a[2].endswith("a") else 0) or real(*a))
    threads = [threading.Thread(target=hf.hf_publish, args=(agent_mod.Ctx(r, None), n, n, "dataset")) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(p["repo_id"] for p in budget.read_json(r.log_dir / "publications.json")) == ["me/a", "me/b"]


def test_the_gateway_moves_to_a_free_port_when_its_own_is_taken(tmp_path):
    import socket

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        s = {"harness": "codex", "provider": "openai", "providers": ["openai"], "gateway_port": port}
        gw, url = launcher.open_gateway(tmp_path, s, {"route": {"OPENAI_BASE_URL": "{gateway}/openai/v1"}})
        try:
            assert gw.server_address[1] != port and url == f"http://127.0.0.1:{gw.server_address[1]}"
        finally:
            gw.shutdown()


def test_the_gateway_tells_a_milestone_the_watchdog_recorded_first(tmp_path):
    """Race check 2026-11-15: the watchdog ticks every 10 s and recorded the 15-minute milestone first; the gateway then
    saw it as already seen, so the harness was never asked to bring its report up to date before the snapshot."""
    from frontier import snapshots

    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=50, start=(budget.now() - dt.timedelta(minutes=16)).isoformat())
    snapshots.write_settings(tmp_path, {"notice_calls": 2})
    watchdog, gw = snapshots.Snapshots(b), snapshots.Snapshots(b)
    watchdog.notice()
    assert "15min reached" in gw.notice()
    assert "15min reached" in gw.notice()
    assert gw.notice() == ""  # told notice_calls times, then no more
def test_a_snapshot_waits_for_a_busy_git(tmp_path):
    """Race check 2026-11-15: with the index locked (the harness or the launcher committing), the snapshot's commit
    failed silently and its event and tag pointed at a commit without the team's latest work."""
    from frontier import snapshots

    ws, st = tmp_path / "ws", tmp_path / "st"
    ws.mkdir()
    git = ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    (ws / "REPORT.md").write_text("results so far\n")
    b = budget.Budget(st)
    b.set_caps(llm_usd=50, start=(budget.now() - dt.timedelta(minutes=6)).isoformat())
    snapshots.write_settings(st, {})
    s = snapshots.Snapshots(b)
    s.notice()
    (ws / ".git" / "index.lock").touch()
    assert s.take(ws) is None
    (ws / ".git" / "index.lock").unlink()
    ev = s.take(ws)
    assert ev["labels"] == ["5min"]
    show = subprocess.run([*git, "show", "snapshot-5min:REPORT.md"], capture_output=True, text=True)
    assert show.stdout == "results so far\n"


def test_a_runpod_call_refused_by_the_rate_limit_is_retried(monkeypatch):
    """Race check 2026-11-15: a burst of rp_exec calls got six 429 refusals from Runpod back as errors; a refused call
    did no work, and the watchdog's or the cleanup's delete would have left a pod billing."""
    from frontier import http

    answers = [{"result": {"isError": True, "content": [{"type": "text", "text": '{"status": 429, "title": "Too Many Requests"}'}]}},
               {"result": {"isError": True, "content": [{"type": "text", "text": '{"status": 429, "title": "Too Many Requests"}'}]}},
               {"result": {"content": [{"type": "text", "text": '{"exitCode": 0}'}]}}]
    calls = []
    monkeypatch.setattr(http, "mcp_rpc", lambda *a, **k: calls.append(a) or answers.pop(0))
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    assert http.mcp_call("exec-pod", {"id": "p"}) == (True, '{"exitCode": 0}')
    assert len(calls) == 3
    monkeypatch.setattr(http, "mcp_rpc", lambda *a, **k: {"result": {"isError": True, "content": [{"text": "bad input"}]}})
    assert http.mcp_call("exec-pod", {"id": "p"}) == (False, "bad input")  # other errors are not retried


def test_responses_cache_writes_are_booked():
    """Race check 2026-11-15: OpenAI's Responses usage carries input_tokens_details.cache_write_tokens (read live:
    6,006 of 6,009 on a first call); the parser dropped it, so cache writes were billed at the input price (Codex's
    own report showed 201,708 written tokens the ledger did not have)."""
    usage = {"input_tokens": 6009, "input_tokens_details": {"cache_write_tokens": 6006, "cached_tokens": 0},
             "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 6014}
    u = llm.usage_of({"type": "response.completed", "response": {"usage": usage}})
    assert u["cache_write"] == 6006 and u["input"] == 6009 and u["cached"] == 0
    assert budget.llm_cost("openai", "gpt-6-luna", u)[0] > budget.llm_cost("openai", "gpt-6-luna", {**u, "cache_write": 0})[0]
def test_a_snapshot_waits_when_git_add_fails(tmp_path, monkeypatch):
    """Level 4 check 2026-11-15: with the index locked only while `git add -A` ran, the add failed silently, the
    `commit --allow-empty` after it succeeded, and the snapshot was tagged on a tree without the team's latest work."""
    from frontier import snapshots

    ws = tmp_path / "ws"
    ws.mkdir()
    git = ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    (ws / "REPORT.md").write_text("results so far\n")
    real = subprocess.run

    def busy_add(cmd, *a, **k):
        if "add" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: Unable to create '.git/index.lock': File exists.")
        return real(cmd, *a, **k)

    monkeypatch.setattr(snapshots.subprocess, "run", busy_add)
    assert snapshots.commit(ws, "Snapshot 5min", ("snapshot-5min",), allow_empty=True) is False
    monkeypatch.setattr(snapshots.subprocess, "run", real)
    assert subprocess.run([*git, "tag", "--list", "snapshot-5min"], capture_output=True, text=True).stdout == ""


def test_a_harness_inside_the_mission_sandbox_drops_its_own_sandbox(tmp_path, monkeypatch):
    """Check of 2026-11-15 (F23, a session in the sandbox image under Docker): Codex's own sandbox (bubblewrap,
    `--sandbox workspace-write`) cannot create a namespace inside a container, so every command and file write of the
    session failed and the AI gave up; the mission's sandbox already confines it."""
    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    h = launcher.harness_conf("codex", "openai")
    cmd, _ = launcher.build_session(info, h, "PROMPT", None)
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"  # on the host, Codex keeps its own sandbox
    cmd, _ = launcher.build_session({**info, "in_sandbox": True}, h, "PROMPT", None)
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access" and "workspace-write" not in cmd
def test_the_jury_listing_does_not_tell_the_length_of_redacted_names(tmp_path, cfg):
    """Level 4 check of 2026-11-15: the repository listing gave each file's size before redaction (REPORT.md 105 B
    against 113 B for the same text), which told the judges the length of the names removed."""
    from frontier import jury

    names = jury.identity_names(cfg)
    files = {}
    for brand in ("OpenAI", "DeepSeek"):
        ws = tmp_path / brand
        ws.mkdir()
        subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
        (ws / "REPORT.md").write_text(f"# Report\n\nWe ({brand}) found 42.\n")
        subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
        f = {"delivered": True, "minutes": 1, "neutralised_minutes": 0, "duration_hours": 1, "llm_usd": 0, "llm_cap_usd": 1,
             "simulation_usd": 0, "rented_usd": 0, "local_cpu_core_hours": 0, "simulation_cap_usd": None, "sessions": 1,
             "deliverables": {}, "ledgers_intact": True}
        files[brand] = jury.redact(jury.team_file(ws, f, "Team A", ""), names)[0]
    assert files["OpenAI"] == files["DeepSeek"]
def test_costs_of_an_undelivered_mission_list_its_publications(tmp_path, cfg):
    """Commit 4c8df21 (PR #11) had no test: COSTS.md of a mission that did not deliver omitted what it published."""
    ws, st = tmp_path / "ws", tmp_path / "st"
    ws.mkdir()
    st.mkdir()
    (st / "publications.json").write_text(json.dumps([{"repo": "team/data", "type": "dataset", "visibility": "private",
                                                       "url": "https://huggingface.co/datasets/team/data"}]))
    launcher.finish(agent_mod.Run(cfg, workspace=ws, log_dir=st), ws)
    assert "https://huggingface.co/datasets/team/data (dataset, private)" in (ws / "COSTS.md").read_text()
