"""Offline tests: no network. The model is replaced by a scripted fake."""

from __future__ import annotations

import json

import pytest

from frontier import agent as agent_mod
from frontier import catalog, http, infra, llm, tools


# ---------------------------------------------------------------- resolve


def test_every_provider_resolves_every_tier_and_level(cfg):
    for name in cfg["providers"]:
        for t in llm.TIERS:
            for lvl in llm.LEVELS:
                s = llm.resolve(cfg, name, t, lvl)
                assert s.model and s.style in llm.STYLES
                assert s.effort_value is not None, (name, t, lvl)


def test_tier_fallback_and_model_override(cfg):
    two = {**cfg, "providers": {**cfg["providers"], "xai": {**cfg["providers"]["xai"], "tiers": {"frontier": "big", "light": "small"}}}}
    assert llm.resolve(two, "xai", "standard", "low").model == "big"  # a missing tier: the nearest stronger one first
    tiers = {"frontier": "big", "standard": "mid"}
    no_light = {**cfg, "providers": {**cfg["providers"], "xai": {**cfg["providers"]["xai"], "tiers": tiers}}}
    assert llm.resolve(no_light, "xai", "light", "low").model == "mid"
    assert llm.resolve(cfg, "xai", "light", "low").model == cfg["providers"]["xai"]["tiers"]["frontier"]
    s = llm.resolve(cfg, "mistral", "light", "low")
    assert s.model == "mistral-small-2603" and s.effort_value == "none"
    assert llm.resolve(cfg, "mistral", "frontier", "low").effort_value == "low"
    assert llm.resolve(cfg, "xai", "frontier", "max").effort_value == "xhigh"
    with pytest.raises(llm.LLMError):
        llm.resolve(cfg, "nope", "frontier", "low")


# ---------------------------------------------------------------- wire formats


def _capture(monkeypatch, response):
    seen = {}

    def fake_post(url, headers, body, **kw):
        seen.update(url=url, headers=headers, body=body)
        return response

    def fake_sse(url, headers, body, **kw):  # streamed styles: one final event (Responses) or one chunk (Gemini)
        seen.update(url=url, headers=headers, body=body)
        return [{"type": "response.completed", "response": response}] if "output" in response else [response]

    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setattr(llm, "_post_sse", fake_sse)
    return seen


HIST = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "echo", "arguments": {"w": 1}},
                                                         {"id": "c2", "name": "echo", "arguments": {"w": 2}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "echo", "content": "a"},
    {"role": "tool", "tool_call_id": "c2", "name": "echo", "content": "b"},
]
TOOL = {"name": "echo", "description": "d", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}


def test_openai_chat_format(cfg, monkeypatch):
    seen = _capture(monkeypatch, {"choices": [{"message": {"content": None, "reasoning_content": "think",
        "tool_calls": [{"id": "x", "function": {"name": "echo", "arguments": "{\"w\": 3}"}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
    spec = llm.resolve(cfg, "deepseek", "frontier", "high")
    r = llm.chat(spec, HIST, [TOOL])
    b = seen["body"]
    assert b["reasoning_effort"] == "high" and b["thinking"] == {"type": "enabled"}
    assert b["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{\"w\": 1}"
    assert r.tool_calls == [{"id": "x", "name": "echo", "arguments": {"w": 3}}]
    assert r.raw_message["_raw"]["reasoning_content"] == "think"
    assert r.usage["input"] == 5
    # reasoning_content is sent back on the next turn
    llm.chat(spec, HIST + [r.raw_message], [TOOL])
    assert seen["body"]["messages"][-1]["reasoning_content"] == "think"
    # "none" turns thinking off and sends no effort
    llm.chat(llm.resolve(cfg, "deepseek", "frontier", "none"), HIST, [TOOL])
    assert seen["body"]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in seen["body"]


def test_every_provider_sends_its_reasoning_level(cfg, monkeypatch):
    """A provider's reasoning level reaches its API only through `effort_field` (config/providers.toml). Without it the
    request goes out at the provider's default effort, and nothing fails."""
    seen = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    for name, conf in cfg["providers"].items():
        spec = llm.resolve(cfg, name, "frontier", "high")
        assert spec.effort_value is not None and spec.effort_field, name
        if conf["style"] != "openai-chat":
            continue  # the other styles have a format test each
        llm.chat(spec, HIST, [TOOL])
        value = seen["body"]
        for key in spec.effort_field.split("."):
            value = value[key]
        assert value == spec.effort_value, name


def test_responses_format(cfg, monkeypatch):
    out = [{"type": "reasoning", "id": "r1", "encrypted_content": "e", "summary": []},
           {"type": "function_call", "call_id": "k", "name": "echo", "arguments": "{}", "status": "completed"}]
    seen = _capture(monkeypatch, {"output": out, "usage": {"input_tokens": 3, "output_tokens": 4}, "status": "completed"})
    spec = llm.resolve(cfg, "openai", "frontier", "max")
    r = llm.chat(spec, HIST, [TOOL])
    b = seen["body"]
    assert b["reasoning"] == {"effort": "max"} and b["instructions"] == "sys"
    assert [i.get("type") for i in b["input"]][-2:] == ["function_call_output", "function_call_output"]
    assert r.tool_calls[0]["id"] == "k"
    llm.chat(spec, HIST + [r.raw_message], [TOOL])
    assert seen["body"]["input"][-2]["type"] == "reasoning"  # reasoning items are replayed


def test_gemini_format(cfg, monkeypatch):
    seen = _capture(monkeypatch, {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "echo", "args": {"w": 1}}, "thoughtSignature": "sig"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "thoughtsTokenCount": 4}})
    spec = llm.resolve(cfg, "google", "standard", "medium")
    r = llm.chat(spec, HIST, [TOOL])
    b = seen["body"]
    assert seen["headers"]["x-goog-api-key"]
    assert b["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "medium"
    assert "additionalProperties" not in json.dumps(b["tools"])
    assert len(b["contents"][-1]["parts"]) == 2  # both function responses in one user turn
    assert r.usage["output"] == 5
    llm.chat(spec, HIST + [r.raw_message], [TOOL])
    assert seen["body"]["contents"][-1]["parts"][0]["thoughtSignature"] == "sig"


def test_anthropic_format(cfg, monkeypatch):
    seen = _capture(monkeypatch, {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1, "output_tokens": 1},
                                  "stop_reason": "end_turn"})
    r = llm.chat(llm.resolve(cfg, "anthropic", "light", "low"), HIST, [TOOL])
    b = seen["body"]
    assert b["system"] == "sys" and b["output_config"] == {"effort": "low"}
    assert [x["type"] for x in b["messages"][-1]["content"]] == ["tool_result", "tool_result"]
    assert r.content == "ok"
    llm.chat(llm.resolve(cfg, "anthropic", "frontier", "none", mode="fast"), HIST, [TOOL])
    b = seen["body"]  # reasoning off: no thinking, but the fast mode billed at price_x is still asked for
    assert b["speed"] == "fast" and "thinking" not in b and "output_config" not in b


# ---------------------------------------------------------------- catalog


def test_catalog_signatures():
    ids = catalog.by_id()
    assert "scaleway/instance:CreateServer" in ids and "runpod/mcp:create-pod" in ids
    assert catalog.search("create server", limit=1).startswith("scaleway/instance:CreateServer")
    e = json.loads(catalog.show("scaleway/instance:CreateServer"))
    assert e["path"] == "/instance/v1/zones/{zone}/servers" and "commercial_type*" in e["body"]
    pod = json.loads(catalog.show("runpod/mcp:create-pod"))
    assert "gpu" in pod["args"]["body*"] and "image" in pod["args"]["body*"]
    assert "list-pods" in catalog.read_only_mcp_tools() and "create-pod" not in catalog.read_only_mcp_tools()
    assert "Product Catalog" in catalog.show("scaleway/guide")
    assert len(json.dumps(catalog.entries())) < 400_000  # compact signatures, not the full docs


# ---------------------------------------------------------------- guards


def test_spend_guard(run, monkeypatch):
    called = []
    monkeypatch.setattr(http, "request", lambda *a, **k: (called.append(a) or (200, "{}")))
    ctx = agent_mod.Ctx(run, None)
    assert tools.t_scaleway(ctx, "POST", "/instance/v1/zones/{zone}/servers", {}).startswith("refused")
    assert tools.t_runpod_mcp(ctx, "create-pod", {}).startswith("refused")
    assert infra.scw_create_instance(ctx, "x", "L4-1-24G", max_hours=1).startswith("refused")
    assert infra.rp_create_pod(ctx, "x", "NVIDIA A40", max_hours=1).startswith("refused")
    assert infra.rp_delete_pod(ctx, "foreign").startswith("refused")
    assert infra.scw_delete_instance(ctx, "foreign").startswith("refused")
    assert not called


def test_workspace_confinement(run):
    ctx = agent_mod.Ctx(run, None)
    tools.t_write_file(ctx, "a/b.txt", "hello\n")
    assert "hello" in tools.t_read_file(ctx, "a/b.txt")
    with pytest.raises(PermissionError):
        tools.t_read_file(ctx, "../../etc/passwd")


# ---------------------------------------------------------------- agent loop and sub-agents


class FakeModel:
    """Scripted model: the main agent spawns two sub-agents in one turn, sub-agents answer at once."""

    def __init__(self):
        self.calls = []

    def __call__(self, spec, messages, tools_, max_tokens=0):
        self.calls.append((spec.label(), [t["name"] for t in tools_]))
        is_main = "Your role" not in messages[0]["content"] and messages[1]["content"] == "main task"
        done = any(m["role"] == "tool" for m in messages)
        if is_main and not done:
            calls = [{"id": f"s{i}", "name": "spawn_agent", "arguments": {
                "task": f"sub {i}", "tier": "light", "reasoning": "low", "provider": "zhipu"}} for i in range(2)]
            calls.append({"id": "w", "name": "write_file", "arguments": {"path": "out.txt", "content": "x"}})
            m = {"role": "assistant", "content": "", "tool_calls": calls, "_raw": {}}
            return llm.Reply("", calls, {"input": 10, "output": 5}, m)
        text = "final: " + ";".join(m["content"][:200] for m in messages if m["role"] == "tool") if is_main else "sub done"
        m = {"role": "assistant", "content": text, "tool_calls": [], "_raw": {}}
        return llm.Reply(text, [], {"input": 10, "output": 5}, m)


def test_agent_spawns_subagents_with_their_own_model(run, monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(llm, "chat", fake)
    a = agent_mod.Agent(run, "openai", "frontier", "high")
    answer = a.run_task("main task")
    assert "sub done" in answer and "glm-5.3-flash" in answer
    labels = [c[0] for c in fake.calls]
    assert labels.count("zhipu/glm-5.3-flash@low") == 2 and labels[0] == "openai/gpt-6-astra@high"
    assert (run.workspace / "out.txt").read_text() == "x"
    assert run.agents == 2 and run.tokens == 15 * 4
    events = [json.loads(x)["event"] for x in (run.log_dir / "events.jsonl").read_text().splitlines()]
    assert events.count("agent_start") == 3


def test_depth_and_agent_limits(cfg, tmp_path):
    cfg["limits"].update(max_depth=1, max_agents=0)
    run = agent_mod.Run(cfg, workspace=tmp_path, log_dir=tmp_path / "log")
    main = agent_mod.Agent(run, "openai", "frontier", "high")
    child = agent_mod.Agent(run, "openai", "light", "low", depth=1)
    assert "spawn_agent" in main.tools and "spawn_agent" not in child.tools
    out = agent_mod.t_spawn_agent(agent_mod.Ctx(run, main), task="t")
    assert out.startswith("refused")


def test_role_presets_load():
    for name in ("reviewer", "infra"):
        r = agent_mod.load_role(name)
        assert r["tier"] in llm.TIERS and r["reasoning"] in llm.LEVELS and r["instructions"]
    assert agent_mod.load_role("reviewer")["tools"] == ["shell", "read_file", "http_request"]


def test_main_agent_has_every_tool_and_sub_agents_never_more(run, monkeypatch):
    main = agent_mod.Agent(run, "openai", "frontier", "high")
    assert set(tools.all_tools()) <= set(main.tools)  # the main thread knows every tool
    seen = []

    def fake_run_task(self, task):
        seen.append((list(self.tools), self.messages[0]["content"]))
        return "ok"

    monkeypatch.setattr(agent_mod.Agent, "run_task", fake_run_task)
    ctx = agent_mod.Ctx(run, main)
    agent_mod.t_spawn_agent(ctx, task="t", role="infra")
    agent_mod.t_spawn_agent(ctx, task="t", tools=["scw_create_instance", "shell"], instructions="You are a GPU profiler.")
    agent_mod.t_spawn_agent(ctx, task="t")
    (infra_tools, infra_prompt), (two, own_prompt), (default, _) = seen
    assert "scw_create_instance" in infra_tools and "rp_exec" in infra_tools and "budget_status" in infra_tools
    assert "Cloud infrastructure" in infra_prompt
    assert two == ["scw_create_instance", "shell"] and "You are a GPU profiler." in own_prompt
    assert default == list(main.tools)
    small = agent_mod.Agent(run, "openai", "light", "low", tools=["shell"], depth=1)
    agent_mod.t_spawn_agent(agent_mod.Ctx(run, small), task="t", tools=["scw_create_instance", "shell"])
    assert seen[-1][0] == ["shell"]  # never a tool the parent lacks
    agent_mod.t_spawn_agent(ctx, task="t", tools=[])
    assert seen[-1][0] == []  # an empty list is no tools, not the parent's
    assert agent_mod.t_spawn_agent(ctx, task="t", mode="nope").startswith("refused")


def test_mission_providers_restrict_spawn(run):
    run.settings = {"providers": ["openai"]}
    main = agent_mod.Agent(run, "openai", "frontier", "high")
    assert agent_mod.t_spawn_agent(agent_mod.Ctx(run, main), task="t", provider="zhipu").startswith("refused")


# ---------------------------------------------------------------- infra flows (mocked APIs)


class FakeScaleway:
    def __init__(self):
        self.calls, self.server_alive, self.volume_alive = [], True, True

    def __call__(self, method, path, body=None, query=None, content_type="", ok404=False):
        self.calls.append((method, path))
        if path.endswith("/products/servers"):
            return {"servers": {"L4-1-24G": {"gpu": 1, "gpu_info": {"gpu_name": "L4", "gpu_memory": 24 * 2**30},
                                             "ncpus": 8, "ram": 48 * 2**30}}, "total_count": 1}
        if path.endswith("/availability"):
            return {"servers": {"L4-1-24G": {"availability": "available"}}, "total_count": 1}
        if "public-catalog" in path:
            return {"products": [{"product": "L4-1-24G", "status": "general_availability",
                                  "unit_of_measure": {"unit": "minute", "size": 1},
                                  "price": {"retail_price": {"units": 0, "nanos": 13125000}}}], "total_count": 1}
        if "local-images" in path:
            return {"local_images": [{"id": "img", "compatible_commercial_types": ["L4-1-24G"]}]}
        srv = {"id": "srv", "name": "n", "commercial_type": "L4-1-24G", "state": "running", "zone": "fr-par-2",
               "volumes": {"0": {"id": "vol", "volume_type": "sbs_volume"}}, "public_ips": [{"id": "ip", "address": "1.2.3.4"}]}
        if method == "POST" and path.endswith("/servers"):
            return {"server": srv}
        if method == "GET" and "/servers/srv" in path:
            return {"server": srv} if self.server_alive else None
        if path.endswith("/action") and body == {"action": "terminate"}:
            self.server_alive = False
            return {}
        if "/block/" in path and method == "GET":
            return {"status": "available", "references": []} if self.volume_alive else None
        if "/block/" in path and method == "DELETE":
            self.volume_alive = False
            return {}
        if "/ips/" in path and method == "GET":
            return {"ip": {"server": None}}
        return {}


def test_shell_reports_the_exit_status_and_both_outputs(run):
    out = tools.t_shell(agent_mod.Ctx(run, None), "echo to-out; echo to-err >&2; exit 3")
    assert out.startswith("exit 3") and "to-out" in out and "to-err" in out


def test_scaleway_create_then_cleanup(run, monkeypatch):
    fake = FakeScaleway()
    monkeypatch.setattr(infra, "scw", fake)
    monkeypatch.setattr(infra, "POLL_S", 0)
    run.allow_spend = True
    ctx = agent_mod.Ctx(run, None)
    assert json.loads(infra.scw_offers(ctx))["offers"][0]["eur_per_hour"] == 0.7875  # per-minute price converted
    out = json.loads(infra.scw_create_instance(ctx, "n", "L4-1-24G", max_hours=2, cloud_init="#!/bin/bash\necho hi"))
    assert out["id"] == "srv" and out["public_ips"] == ["1.2.3.4"]
    assert ("PATCH", "/instance/v1/zones/fr-par-2/servers/srv/user_data/cloud-init") in fake.calls
    assert run.alive()[0]["eur_per_hour"] == 0.7875
    res = infra.cleanup(run)
    assert res[0]["server"] == "deleted" and res[0]["volumes_deleted"] == ["vol"] and res[0]["ips_deleted"] == ["ip"]
    assert not run.alive()
    assert json.loads((run.log_dir / "resources.json").read_text())[0]["state"] == "deleted"
    assert infra.cleanup(run) == []  # what is deleted is left alone


def test_runpod_create_exec_delete(run, monkeypatch):
    calls = []

    def fake_mcp(tool, args=None):
        calls.append((tool, args))
        if tool == "list-gpu-types":
            return {"gpus": [{"id": "NVIDIA A40", "memory": 48, "price": {"secure": 0.59}, "availability": "LOW"}]}
        if tool == "create-pod":
            return {"id": "pod1"}
        return {"ok": True}

    monkeypatch.setattr(infra, "mcp_json", fake_mcp)
    run.allow_spend = True
    ctx = agent_mod.Ctx(run, None)
    out = json.loads(infra.rp_create_pod(ctx, "n", "NVIDIA A40", max_hours=1, gpu_count=2, command="python train.py"))
    assert out["id"] == "pod1" and out["usd_per_hour_gpu_only"] == 1.18
    body = calls[-1][1]["body"]
    assert body["gpu"] == {"id": "NVIDIA A40", "count": 2} and "python train.py" in body["args"]
    infra.rp_exec(ctx, "pod1", "nvidia-smi")
    assert calls[-1] == ("exec-pod", {"id": "pod1", "body": {"command": ["sh", "-c", "nvidia-smi"], "timeoutSeconds": 20}})
    assert json.loads(infra.rp_delete_pod(ctx, "pod1"))["state"] == "deleted" and not run.alive()


# ---------------------------------------------------------------- MCP server


def test_mcp_server_protocol(tmp_path, monkeypatch):
    from frontier import mcp

    monkeypatch.setenv("FRONTIER_RUNS", str(tmp_path))
    monkeypatch.setenv("FRONTIER_SESSION", "s1")
    monkeypatch.delenv("FRONTIER_ALLOW_SPEND", raising=False)
    srv = mcp.Server()
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "frontier"
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names[:3] == ["spawn_agent", "frontier_plan", "frontier_roles"] and "rp_create_pod" in names
    assert "shell" not in names  # the harness has its own
    r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "rp_create_pod", "arguments": {"name": "x", "gpu_type_id": "g", "max_hours": 1}}})["result"]
    assert r["isError"] and "refused" in r["content"][0]["text"]
    plan = json.loads(srv.call("frontier_plan", {})[0])
    assert "openai" in plan["providers"]
    assert srv.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601

    # the ledger of a session survives a server restart
    srv.run.record({"provider": "runpod", "kind": "pod", "id": "p1"})
    assert mcp.Server().run.owns("p1")


def test_mcp_spawn_agent(tmp_path, monkeypatch):
    from frontier import mcp

    monkeypatch.setenv("FRONTIER_RUNS", str(tmp_path))
    monkeypatch.setattr(llm, "chat", lambda spec, m, t, max_tokens=0: llm.Reply(
        spec.label(), [], {"input": 1, "output": 1}, {"role": "assistant", "content": spec.label(), "tool_calls": []}))
    srv = mcp.Server()
    text, err = srv.call("spawn_agent", {"task": "t", "provider": "mistral", "tier": "light", "reasoning": "low"})
    assert not err and "mistral/mistral-small-2603@low" in text


def test_runpod_mcp_answers_in_json_or_server_sent_events(monkeypatch):
    answers = iter(['{"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}',
                    'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "list-pods"}]}}\n\n'])
    monkeypatch.setattr(http, "request", lambda *a, **kw: (200, next(answers)))
    assert http.mcp_rpc("tools/list")["result"] == {"tools": []}
    assert http.mcp_rpc("tools/list")["result"]["tools"][0]["name"] == "list-pods"
    monkeypatch.setattr(http, "request", lambda *a, **kw: (502, "bad gateway"))
    assert http.mcp_call("list-pods") == (False, "HTTP 502 bad gateway")


def test_a_request_body_never_shares_a_value_with_the_config(cfg):
    """The speed mode's and `extra` fields are merged into every request: a body edited later must not edit them."""
    spec = llm.resolve(cfg, "openai", "frontier", "high")
    spec.extra = {"text": {"verbosity": "low"}, "tags": ["a"]}
    body = llm._merge({}, spec.extra)
    body["text"]["verbosity"] = "high"
    body["tags"].append("b")
    assert spec.extra == {"text": {"verbosity": "low"}, "tags": ["a"]}


# ---------------------------------------------------------------- symbolic computation


def test_sympy_computes_and_prints_the_last_expression(run):
    pytest.importorskip("sympy")
    ctx = agent_mod.Ctx(run, None)
    sympy = tools.REGISTRY["sympy"].fn
    assert sympy(ctx, "integrate(exp(-x**2), (x, -oo, oo))") == "sqrt(pi)\nlatex: \\sqrt{\\pi}"
    assert sympy(ctx, "dsolve(f(t).diff(t, 2) + 4*f(t), f(t))").startswith("Eq(f(t), C1*sin(2*t) + C2*cos(2*t))")
    assert sympy(ctx, "print(solve(x**2 - 2, x))\nprint(sin(2*x).equals(2*sin(x)*cos(x)), sin(x).equals(x))") == \
        "[-sqrt(2), sqrt(2)]\nTrue False"
    assert sympy(ctx, "a = 3").startswith("no output")


def test_sympy_reports_an_error_from_the_callers_code_and_ends_a_computation_at_its_limit(run):
    pytest.importorskip("sympy")
    ctx = agent_mod.Ctx(run, None)
    failed = tools.REGISTRY["sympy"].fn(ctx, "print('before')\n1/0")
    assert failed.startswith('error: File "<code>", line 2') and "ZeroDivisionError" in failed and failed.endswith("before")
    assert tools.REGISTRY["sympy"].fn(ctx, "x = = 1").startswith('error: File "<code>", line 1')
    (run.workspace / "sympy.py").write_text("raise SystemExit('the workspace file was imported')")
    assert tools.REGISTRY["sympy"].fn(ctx, "1 + 1") == "2"  # sympy.py of the workspace does not stand for SymPy
    assert tools.REGISTRY["sympy"].fn(ctx, "while True: pass", timeout_s=1) == "error: no result after 1 s (timeout_s sets the limit)"


def test_sympy_missing_names_the_fix_and_the_tool_is_served(run, tmp_path, monkeypatch):
    from frontier import mcp, symbolic

    monkeypatch.setenv("FRONTIER_RUNS", str(tmp_path))
    srv = mcp.Server()
    spec = next(t for t in srv.tool_specs() if t["name"] == "sympy")
    assert spec["annotations"]["openWorldHint"] is False and spec["annotations"]["destructiveHint"] is False
    assert "sympy" in tools.all_tools()
    monkeypatch.setattr(symbolic.importlib.util, "find_spec", lambda name: None)
    out, err = srv.call("sympy", {"code": "1 + 1"})
    assert err and "pip install sympy" in out
