"""Offline tests: no network. The model is replaced by a scripted fake."""

from __future__ import annotations

import json

import pytest

from frontier import agent as agent_mod
from frontier import apidocs, llm, tools
from frontier.config import load_config


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def run(cfg, tmp_path):
    return agent_mod.Run(cfg, workspace=tmp_path, log_dir=tmp_path / "log")


# ---------------------------------------------------------------- resolve


def test_every_provider_resolves_every_tier_and_level(cfg):
    for name in cfg["providers"]:
        for t in llm.TIERS:
            for lvl in llm.LEVELS:
                s = llm.resolve(cfg, name, t, lvl)
                assert s.model and s.style in llm.STYLES
                assert s.effort_value is not None, (name, t, lvl)


def test_tier_fallback_and_model_override(cfg):
    assert llm.resolve(cfg, "xai", "standard", "low").model == cfg["providers"]["xai"]["tiers"]["light"]
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

    monkeypatch.setattr(llm, "_post", fake_post)
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


# ---------------------------------------------------------------- docs


def test_docs_index_and_show():
    ids = {o["id"] for o in apidocs.index()}
    assert "scaleway/instance:CreateServer" in ids and "runpod/rest-v1:CreatePod" in ids
    assert "runpod/mcp:create-pod" in ids
    assert apidocs.search("create server", limit=1).startswith("scaleway/instance:CreateServer")
    s = json.loads(apidocs.show("scaleway/instance:CreateServer"))
    assert s["path"] == "/instance/v1/zones/{zone}/servers"
    assert "commercial_type" in json.dumps(s["requestBody"])  # $refs resolved
    assert "Product Catalog" in apidocs.show("scaleway/guide")
    assert "mcp.runpod.io" in apidocs.show("runpod/guide")


# ---------------------------------------------------------------- tools


def test_spend_guard(run, monkeypatch):
    called = []
    monkeypatch.setattr(tools, "_http", lambda *a, **k: (called.append(a) or (200, "{}")))
    ctx = agent_mod.Ctx(run, None)
    assert tools.t_scaleway(ctx, "POST", "/instance/v1/zones/{zone}/servers", {}).startswith("refused")
    assert tools.t_runpod(ctx, "DELETE", "/pods/x").startswith("refused")
    assert tools.t_runpod_mcp(ctx, "create-pod", {}).startswith("refused")
    assert not called
    tools.t_scaleway(ctx, "GET", "/instance/v1/zones/{zone}/servers")
    assert "/zones/fr-par" in called[-1][1] or "/zones/" in called[-1][1]
    run.allow_spend = True
    tools.t_runpod(ctx, "DELETE", "/pods/x")
    assert called[-1][0] == "DELETE"


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
    for name in ("worker", "reviewer", "cloud-ops", "scout"):
        r = agent_mod.load_role(name)
        assert r["tier"] in llm.TIERS and r["reasoning"] in llm.LEVELS and r["instructions"]
    assert agent_mod.load_role("reviewer")["tools"] == ["shell", "read_file", "api_docs", "http_request"]
