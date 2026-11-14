"""Offline tests of budgets, the metering gateway, harness adapters, missions, delivery and publishing."""

from __future__ import annotations

import json
import pathlib
import threading
import tomllib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from frontier import budget, gateway, harnesses, hf, infra, launcher, llm, mission
from frontier.agent import Ctx, Run
from frontier.config import load_config


@pytest.fixture
def cfg():
    return load_config()


# ---------------------------------------------------------------- budget


def test_llm_cost_uses_cache_and_mode_prices():
    pr = budget.prices()["gpt-6.1-sol"]
    usd, exact = budget.llm_cost("openai", "gpt-6.1-sol", {"input": 1_000_000, "cached": 400_000, "output": 100_000})
    assert exact and usd == pytest.approx((600_000 * pr["input"] + 400_000 * pr["cache_read"] + 100_000 * pr["output"]) / 1e6)
    usd2, _ = budget.llm_cost("openai", "gpt-6.1-sol", {"input": 1000, "output": 0}, price_x=2.0)
    assert usd2 == pytest.approx(2 * 1000 * pr["input"] / 1e6)
    _, exact = budget.llm_cost("openai", "not-a-model", {"input": 1})
    assert not exact  # unknown model: priced as the provider's most expensive one


def test_llm_cap_is_hard(tmp_path):
    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=0.01)
    assert b.llm_allowed()[0]
    b.charge_llm("t", "a", "openai", "gpt-6.1-sol", {"input": 1000, "output": 1000})  # 0.012 USD
    ok, why = b.llm_allowed()
    assert not ok and "cap" in why
    b2 = budget.Budget(tmp_path / "b2")
    b2.set_caps(llm_usd=1.0)
    assert not b2.llm_allowed(next_call_usd=1.5)[0]


def test_reservations_and_tamper_evident_ledger(tmp_path):
    b = budget.Budget(tmp_path)
    b.set_caps(llm_usd=1.0)
    r1, _ = b.reserve(0.6)
    r2, why = b.reserve(0.6)  # does not fit next to the first call in flight
    assert r1 and r2 is None and "in flight" in why
    b.release(r1)
    assert b.reserve(0.6)[0]
    for i in range(3):
        b.charge_llm("t", "a", "openai", "gpt-6-luna", {"input": 10 * (i + 1), "output": 1})
    assert b.verify()["llm.jsonl"] is True
    lines = (tmp_path / "llm.jsonl").read_text().splitlines()
    lines[1] = lines[1].replace('"gen_ai.usage.input_tokens": 20', '"gen_ai.usage.input_tokens": 2')
    (tmp_path / "llm.jsonl").write_text("\n".join(lines) + "\n")
    assert b.verify()["llm.jsonl"] == "broken at line 2"


def test_cloud_budget_counts_spent_committed_and_new(tmp_path):
    b = budget.Budget(tmp_path)
    b.set_caps(runpod_usd=10.0)
    start = (budget.now() - budget.dt.timedelta(hours=2)).isoformat()
    (tmp_path / "resources.json").write_text(json.dumps([
        {"provider": "runpod", "id": "p1", "usd_per_hour": 1.0, "max_hours": 5, "created": start, "state": "alive"}]))
    assert b.cloud_spent("runpod") == pytest.approx(2.0, abs=0.01)
    assert b.cloud_committed("runpod") == pytest.approx(3.0, abs=0.01)
    assert b.cloud_allowed("runpod", 1.0, 4)[0]  # 2 + 3 + 4 <= 10
    ok, why = b.cloud_allowed("runpod", 1.0, 6)
    assert not ok and "runpod budget" in why
    assert not b.cloud_allowed("runpod", None, 1)[0]
    b.set_caps(frozen=True)
    assert not b.cloud_allowed("runpod", 0.1, 1)[0] and not b.llm_allowed(1.0)[0]  # beyond the post-delivery grace


def test_watchdog_deletes_overdue_resources(tmp_path, cfg, monkeypatch):
    run = Run(cfg, workspace=tmp_path, log_dir=tmp_path / "s")
    old = (budget.now() - budget.dt.timedelta(hours=3)).isoformat()
    run.resources = [{"provider": "runpod", "id": "p1", "usd_per_hour": 1.0, "max_hours": 2, "created": old, "state": "alive"},
                     {"provider": "runpod", "id": "p2", "usd_per_hour": 1.0, "max_hours": 9, "created": old, "state": "alive"}]
    deleted = []
    monkeypatch.setattr(infra, "mcp_json", lambda tool, args=None: deleted.append(args["id"]) or {})
    out = infra.enforce(run)
    assert deleted == ["p1"] and out[0]["reason"] == "max_hours reached"
    assert [r["id"] for r in run.alive()] == ["p2"]


# ---------------------------------------------------------------- gateway


def test_usage_of_every_wire_format():
    chat = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}}}
    assert gateway.usage_of(chat) == {"input": 10, "output": 5, "cached": 4, "reasoning": 0}
    assert gateway.usage_of({"type": "response.completed", "response": {"usage": {
        "input_tokens": 7, "output_tokens": 3, "total_tokens": 10, "input_tokens_details": {"cached_tokens": 2}}}})["cached"] == 2
    start = gateway.usage_of({"type": "message_start", "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 100,
                                                                             "output_tokens": 1}}})
    assert start == {"input": 105, "cached": 100, "cache_write": 0, "output": 1}
    assert gateway.usage_of({"type": "message_delta", "usage": {"output_tokens": 42}}) == {"output": 42}
    assert gateway.usage_of({"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "thoughtsTokenCount": 8}})["output"] == 10
    total: dict = {}
    for part in (start, {"output": 42}):
        gateway.merge(total, part)
    assert total["output"] == 42 and total["input"] == 105


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((self.path, body))
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for ev in ({"choices": [{"delta": {"content": "hi"}}]},
                       {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}):
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            data = json.dumps({"model": body["model"], "usage": {"prompt_tokens": 2000, "completion_tokens": 10}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


def test_gateway_meters_and_caps(tmp_path):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    up.seen = []
    threading.Thread(target=up.serve_forever, daemon=True).start()
    b = budget.Budget(tmp_path)
    gw = gateway.Gateway(("127.0.0.1", 0), b, {"zhipu": (f"http://127.0.0.1:{up.server_address[1]}", "zhipu")})
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{gw.server_address[1]}/zhipu/api/paas/v4"

    def post(body):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer x"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()

    assert "usage" in post({"model": "glm-5.3", "messages": []})
    streamed = post({"model": "glm-5.3", "messages": [], "stream": True})
    assert "[DONE]" in streamed
    assert up.seen[1][1]["stream_options"] == {"include_usage": True}  # asked for usage in the stream
    entries = b.llm_entries()
    assert [e["gen_ai.usage.input_tokens"] for e in entries] == [2000, 1000]
    assert all(e["frontier.source"] == "gateway" for e in entries) and entries[0]["frontier.cost.usd"] > 0
    assert b.verify() == {"llm.jsonl": True} and b.reserved() == 0  # chained ledger, reservations released
    b.set_caps(llm_usd=b.llm_spent())
    with pytest.raises(urllib.error.HTTPError) as e:
        post({"model": "glm-5.3", "messages": []})
    assert e.value.code == 402
    gw.shutdown()
    up.shutdown()


# ---------------------------------------------------------------- harness adapters


def test_every_harness_gets_parseable_files():
    files = harnesses.artifacts(harnesses.McpCommand())
    assert files["CLAUDE.md"] == "@AGENTS.md\n"
    for rel, text in files.items():
        if rel.endswith(".json"):
            json.loads(text)
        elif rel.endswith(".toml"):
            tomllib.loads(text)
    assert json.loads(files["opencode.json"])["mcp"]["servers"]["frontier"]["command"] == ["python3", "-m", "frontier.mcp"]
    assert tomllib.loads(files[".codex/config.toml"])["mcp_servers"]["frontier"]["args"] == ["-m", "frontier.mcp"]
    codex_agent = tomllib.loads(files[".codex/agents/reviewer.toml"])
    assert codex_agent["model"] == "gpt-6-astra" and codex_agent["model_reasoning_effort"] == "high"
    assert "model: fable" in files[".claude/agents/reviewer.md"]
    assert files[".agents/skills/frontier/SKILL.md"].startswith("---\nname: frontier\ndescription: ")
    assert "deliver" in files[".agents/skills/frontier/SKILL.md"]
    one = harnesses.artifacts(harnesses.McpCommand("/abs/bin/frontier-mcp", ["--state", "/abs/.frontier"]), ["kimi"])
    assert json.loads(one[".kimi-code/mcp.json"])["mcpServers"]["frontier"] == {"command": "/abs/bin/frontier-mcp",
                                                                                "args": ["--state", "/abs/.frontier"]}


def test_repository_harness_files_are_in_sync():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel, text in harnesses.artifacts(harnesses.McpCommand()).items():
        assert (root / rel).read_text() == text, f"{rel} is stale: run python3 tools/sync_harnesses.py"


# ---------------------------------------------------------------- missions


def _mission(tmp_path: pathlib.Path, harness: str = "auto", provider: str = "openai") -> pathlib.Path:
    (tmp_path / "subject.md").write_text("# Subject\n\nDeliver a report in English. Use five seeds.\n")
    f = tmp_path / "m.toml"
    f.write_text(f'''[mission]
name = "t"
subject = "subject.md"
workspace = "ws"
state = "state"
duration_hours = 2
[harness]
name = "{harness}"
[llm]
provider = "{provider}"
budget_usd = 5
[scaleway]
budget_eur = 3
[runpod]
budget_usd = 4
[huggingface]
token_env = "NO_SUCH_TOKEN_FOR_TESTS"
''')
    return f


def test_prepare_writes_the_workspace(tmp_path, monkeypatch):
    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    ws = info["workspace"]
    for f in ("AGENTS.md", "MISSION.md", "TOOLS.md", "subject/subject.md", ".codex/config.toml", ".agents/skills/frontier/SKILL.md"):
        assert (ws / f).exists(), f
    caps = json.loads((info["state"] / "budgets.json").read_text())
    assert caps["llm_usd"] == 5 and caps["scaleway_eur"] == 3 and caps["runpod_usd"] == 4 and caps["deadline"]
    brief = (ws / "MISSION.md").read_text()
    assert "subject/subject.md" in brief and "COMPLIANCE.md" in brief and "The strategy is yours" in brief
    tools_md = (ws / "TOOLS.md").read_text()
    assert "`spawn_agent`" in tools_md and "`deliver`" in tools_md and "gpt-6-astra" in tools_md
    assert "publishing is not available" in tools_md  # no token
    conf = tomllib.loads((ws / ".codex/config.toml").read_text())["mcp_servers"]["frontier"]
    assert conf["command"].endswith("bin/frontier-mcp") and conf["args"] == ["--state", str(info["state"])]
    assert not (ws / ".frontier").exists()  # the state stays out of the AI's workspace
    assert info["settings"]["harness"] == "codex" and not info["settings"]["allow_publish"]


def test_deliver_checks_freezes_and_reports(tmp_path, cfg, monkeypatch):
    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    ws, state = info["workspace"], info["state"]
    run = Run(cfg, workspace=ws, allow_spend=True, run_id=state.name, log_dir=state, settings=info["settings"])
    ctx = Ctx(run, None)
    assert mission.deliver(ctx).startswith("refused: missing DELIVERY.md")
    for f in mission.REQUIRED:
        (ws / f).write_text(f"# {f}\n")
    run.budget.charge_llm("t", "a", "openai", "gpt-6-luna", {"input": 100, "output": 100})
    rec = json.loads(mission.deliver(ctx, "done"))
    assert rec["missing"] == [] and rec["llm_usd"] > 0 and (ws / "COSTS.md").exists()
    assert not run.budget.llm_allowed(1.0)[0] and run.budget.llm_allowed(0.01)[0]  # frozen, small grace to close the turn
    assert mission.deliver(ctx).startswith("already delivered")


def test_harness_choice_and_launch_command(tmp_path, monkeypatch):
    assert mission.load(_mission(tmp_path, provider="anthropic"))["harness"]["name"] == "claude"
    assert mission.load(_mission(tmp_path, provider="zhipu"))["harness"]["name"] == "opencode"
    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    h = launcher.harness_conf("codex", "openai")
    cmd, env = launcher.build_session(info, h, "PROMPT", "http://127.0.0.1:9")
    assert cmd[:2] == ["codex", "exec"] and cmd[cmd.index("-m") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="high"' in cmd
    assert 'model_providers.frontier.base_url="http://127.0.0.1:9/openai/v1"' in cmd and cmd[-1] == "PROMPT"
    assert 'model_provider="frontier"' in cmd and env["OPENAI_API_KEY"] == "sk-test"
    z = launcher.harness_conf("claude", "zhipu")
    assert z["route"]["ANTHROPIC_BASE_URL"] == "{gateway}/zhipu/api/anthropic" and "ANTHROPIC_AUTH_TOKEN" in z["env"]


def test_hf_card_check(tmp_path):
    d = tmp_path / "model"
    d.mkdir()
    assert "card missing" in hf.card_problems(d, "model")[0]
    (d / "README.md").write_text("---\nlicense: mit\nlibrary_name: pytorch\ntags: [physics]\n---\n" + "A model card. " * 30)
    assert hf.card_problems(d, "model") == []
    (d / "README.md").write_text("---\ntags: [x]\n---\nshort")
    assert len(hf.card_problems(d, "model")) == 3  # no license, no library_name, empty body


def test_hf_publish_needs_permission_and_card(tmp_path, cfg, monkeypatch):
    run = Run(cfg, workspace=tmp_path, log_dir=tmp_path / "s", settings={"allow_publish": False})
    assert hf.hf_publish(Ctx(run, None), "r", ".").startswith("refused: publishing is not allowed")
    run.settings = {"allow_publish": True, "hf_namespace": "me", "publish_visibility": "private"}
    (tmp_path / "out").mkdir()
    assert "fix the card" in hf.hf_publish(Ctx(run, None), "r", "out")
    (tmp_path / "out" / "README.md").write_text("---\nlicense: cc-by-4.0\n---\n" + "Dataset of runs. " * 20)
    calls = []

    class FakeApi:
        def create_repo(self, repo_id, **kw):
            calls.append(("create", repo_id, kw))

        def upload_folder(self, **kw):
            calls.append(("upload", kw["repo_id"], kw["repo_type"]))
            return type("C", (), {"oid": "abc"})()

    monkeypatch.setattr(hf, "_api", lambda: FakeApi())
    rec = json.loads(hf.hf_publish(Ctx(run, None), "runs", "out", repo_type="dataset"))
    assert rec["url"] == "https://huggingface.co/datasets/me/runs" and calls[0][2]["private"] is True
    assert json.loads((tmp_path / "s" / "publications.json").read_text())[0]["repo_id"] == "me/runs"


def test_modes_change_body_and_price(cfg):
    cfg = json.loads(json.dumps(cfg))
    cfg["providers"]["openai"]["modes"] = {"priority": {"body": {"service_tier": "priority"}, "price_x": 2.0}}
    s = llm.resolve(cfg, "openai", "standard", "low", "priority")
    assert s.extra["service_tier"] == "priority" and s.price_x == 2.0 and s.label().endswith("+priority")
    with pytest.raises(llm.LLMError):
        llm.resolve(cfg, "openai", "standard", "low", "turbo")


def test_usage_reports_of_unrouted_harnesses():
    codex = "\n".join([
        "Reading additional input from stdin...",
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600,
                                                         "output_tokens": 50, "reasoning_output_tokens": 10}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 500, "output_tokens": 5}})])
    usd, usage = launcher.usage_from_report({"usage": "jsonl:turn.completed"}, codex)
    assert usd is None and usage == {"input": 1500, "cached": 600, "cache_write": 0, "output": 55, "reasoning": 10}
    claude = json.dumps({"type": "result", "total_cost_usd": 0.42, "result": "done"})
    assert launcher.usage_from_report({"usage": "json:total_cost_usd"}, claude) == (0.42, {})
    assert launcher.usage_from_report({"usage": ""}, claude) == (None, {})


def test_gateway_prices_the_tier_actually_served(cfg):
    oa = cfg["providers"]["openai"]
    assert gateway.mode_of(oa, "priority") == ("fast", 2.0)  # OpenAI reports priority for a fast request
    assert gateway.mode_of(oa, "flex") == ("flex", 0.5)
    assert gateway.mode_of(oa, "default") == ("", 1.0)  # downgraded: billed at standard
    assert gateway.mode_of(cfg["providers"]["anthropic"], "fast") == ("fast", 2.0)
    u = gateway.usage_of({"service_tier": "priority", "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                                "cost_in_usd_ticks": 25_000_000}})
    assert u["served_tier"] == "priority" and u["cost_usd"] == pytest.approx(0.0025)
    assert gateway.usage_of({"type": "message_delta", "usage": {"output_tokens": 3, "speed": "fast"}})["served_tier"] == "fast"


def test_deepseek_off_peak_price():
    pr = budget.prices()["deepseek-flash"]
    peak = budget.dt.datetime(2026, 11, 16, 7, 30, tzinfo=budget.dt.UTC)  # Monday 07:30 UTC
    off = budget.dt.datetime(2026, 11, 16, 12, 0, tzinfo=budget.dt.UTC)
    weekend = budget.dt.datetime(2026, 11, 15, 7, 30, tzinfo=budget.dt.UTC)
    assert budget.time_factor(pr, peak) == 1.0 and budget.time_factor(pr, off) == 0.5 and budget.time_factor(pr, weekend) == 0.5
    usage = {"input": 1_000_000, "output": 0}
    assert budget.llm_cost("deepseek", "deepseek-flash", usage, at=off)[0] == pytest.approx(0.15)


def test_file_configured_harnesses_are_routed(tmp_path):
    info = mission.prepare(_mission(tmp_path, harness="opencode", provider="zhipu"), ping=False, offers=False)
    port = info["settings"]["gateway_port"]
    oc = json.loads((info["workspace"] / "opencode.json").read_text())
    prov = oc["providers"]["frontier"]
    assert prov["settings"]["baseURL"] == f"http://127.0.0.1:{port}/zhipu/api/paas/v4" and prov["env"] == ["ZAI_API_KEY"]
    assert list(prov["models"]) == ["glm-5.3"] and oc["mcp"]["servers"]["frontier"]["command"][0].endswith("frontier-mcp")
    (tmp_path / "h").mkdir()
    info2 = mission.prepare(_mission(tmp_path / "h", harness="hermes", provider="openai"), ping=False, offers=False)
    conf = (info2["state"] / "hermes" / "config.yaml").read_text()
    assert "api_mode: codex_responses" in conf and "/openai/v1" in conf and "frontier-mcp" in conf


def test_generic_usage_report():
    agy = json.dumps({"response": "ok", "usage": {"input_tokens": 120, "output_tokens": 30, "thinking_tokens": 50,
                                                  "cache_read_tokens": 100}})
    usd, u = launcher.usage_from_report({"usage": "json-usage"}, agy)
    assert usd is None and u == {"input": 120, "output": 80, "reasoning": 50, "cached": 100}
    qwen = json.dumps({"type": "result", "usage": {"prompt_tokens": 7, "completion_tokens": 3}, "stats": {}})
    assert launcher.usage_from_report({"usage": "json-usage"}, qwen)[1]["input"] == 7


def test_dashboard_snapshot(tmp_path, cfg):
    from frontier import dashboard

    info = mission.prepare(_mission(tmp_path), ping=False, offers=False)
    run = Run(cfg, workspace=info["workspace"], run_id="x", log_dir=info["state"], settings=info["settings"])
    run.budget.charge_llm("gateway", "harness", "openai", "gpt-6-luna", {"input": 1000, "output": 10})
    run.log(event="session_start", n=1)
    run.log(event="mcp_call", tool="rp_exec", arguments='{"command": "python train.py"}', output="ok")
    (info["state"] / "sessions").mkdir()
    (info["state"] / "sessions" / "01.log").write_text(json.dumps(
        {"type": "item.completed", "item": {"type": "command_execution", "command": "pytest -q"}}) + "\n")
    snap = dashboard.snapshot(tmp_path)
    t = snap["teams"][0]
    assert t["name"] == "t" and t["running"]["n"] == 1 and t["tokens"]["input"] == 1000
    assert any(a["kind"] == "harness shell" and "pytest" in a["text"] for a in t["actions"])
    assert t["jobs"] and "python train.py" in t["jobs"][0]["text"]
    assert snap["feed"][0]["team"] == "t"


def test_dashboard_subagent_history(tmp_path, cfg):
    from frontier import dashboard

    events = [{"t": "2026-11-14T10:00:00+00:00", "event": "agent_start", "agent": "sub-1", "parent": "main-1", "depth": 1,
               "provider": "zhipu", "model": "glm-5.3-flash", "reasoning": "low", "task": "compute"},
              {"t": "2026-11-14T10:00:03+00:00", "event": "model_call", "agent": "sub-1", "tool_calls": ["shell"]},
              {"t": "2026-11-14T10:00:05+00:00", "event": "agent_end", "agent": "sub-1", "answer": "391"}]
    llm = [{"gen_ai.agent.name": "sub-1", "gen_ai.usage.input_tokens": 900, "gen_ai.usage.cache_read.input_tokens": 100,
            "gen_ai.usage.output_tokens": 10, "gen_ai.usage.reasoning.output_tokens": 2, "frontier.cost.usd": 0.0002,
            "frontier.mode": ""}]
    log = tmp_path / "01.log"
    log.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "reviewer", "description": "check E1"}}]}}) + "\n")
    rows = dashboard.subagents(events, llm, [log])
    sub = rows[0]
    assert sub["kind"] == "spawn_agent" and sub["seconds"] == 5 and sub["calls"] == 1 and sub["tool_calls"] == 1
    assert sub["input"] == 900 and sub["cached"] == 100 and sub["usd"] == 0.0002 and sub["status"] == "done"
    assert rows[1]["kind"] == "native" and rows[1]["agent"] == "reviewer" and rows[1]["usd"] is None


def test_openshell_files_and_commands(tmp_path, monkeypatch):
    from frontier import sandbox

    f = _mission(tmp_path, harness="codex", provider="anthropic")
    f.write_text(f.read_text().replace('name = "codex"', 'name = "codex"') + '''
[sandbox]
kind = "openshell"
[network]
allow = ["arxiv.org", "*.wikipedia.org", "github.com"]
deny = ["github.com", "de.wikipedia.org"]
''')
    info = mission.prepare(f, ping=False, offers=False)
    assert info["settings"]["sandbox"] == "openshell"
    conf = tomllib.loads((info["workspace"] / ".codex/config.toml").read_text())["mcp_servers"]["frontier"]
    assert conf == {"command": "/opt/frontier/bin/frontier-mcp", "args": ["--state", "/sandbox/state"], "tool_timeout_sec": 3600}
    box = sandbox.OpenShell(info, launcher.harness_conf("codex", "anthropic"), dry_run=True)
    warnings = box.write_files()
    assert any("github.com" in w for w in warnings) and any("wikipedia" in w for w in warnings)
    pol = json.loads((box.dir / "policy.yaml").read_text())
    assert [e["host"] for e in pol["network_policies"]["research"]["endpoints"]] == ["arxiv.org"]
    assert all(e["access"] == "read-only" and e["enforcement"] == "enforce" for e in pol["network_policies"]["research"]["endpoints"])
    llm_prof = json.loads((box.dir / "frontier-llm-anthropic.yaml").read_text())
    assert llm_prof["credentials"][0]["header_name"] == "x-api-key" and llm_prof["endpoints"][0]["host"] == "api.anthropic.com"
    scw = json.loads((box.dir / "frontier-scaleway.yaml").read_text())
    assert scw["credentials"][0]["env_vars"] == ["SCW_SECRET_KEY"] and scw["credentials"][0]["header_name"] == "x-auth-token"
    assert "COPY frontier /opt/frontier" in (box.dir / "image" / "Dockerfile").read_text()
    box.setup()
    created = next(c for c in box.commands if c[:3] == ["openshell", "sandbox", "create"])
    assert "--policy" in created and created.count("--provider") == 4 and created[-3:] == ["--", "sleep", "infinity"]
    assert box.session_command(2)[-3:] == ["session", "/sandbox/state", "2"]
    box.teardown()
    assert box.commands[-1][:3] == ["openshell", "provider", "delete"]
