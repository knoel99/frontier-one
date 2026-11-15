"""Provider-agnostic chat client with tool calling, standard library only.

Internal message format (OpenAI-like, provider-neutral):
  {"role": "system" | "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}], "_raw": <provider data>}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Each style ("openai-chat", "openai-responses", "gemini", "anthropic") converts it to its wire format and back. `_raw` keeps what a
provider needs replayed on the next turn (reasoning_content, Gemini thought signatures, Anthropic thinking blocks).
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from .credentials import PLACEHOLDER as PROXY_PLACEHOLDER

LEVELS = ["none", "low", "medium", "high", "max"]
TIERS = ["frontier", "standard", "light"]


class LLMError(RuntimeError):
    def __init__(self, msg: str, maybe_billed: bool = False):
        super().__init__(msg)
        self.maybe_billed = maybe_billed  # the provider probably ran the call: the caller charges an estimate


@dataclass
class Reply:
    content: str
    tool_calls: list[dict]
    usage: dict  # {"input": int, "output": int, "reasoning": int}
    raw_message: dict  # internal assistant message, ready to append to the history
    stop: str = ""


@dataclass
class ModelSpec:
    provider: str
    style: str
    base_url: str
    key_env: str
    model: str
    tier: str
    reasoning: str  # abstract level
    effort_value: object  # provider value, or None when the level is "none" and unmapped
    effort_field: str
    extra: dict = field(default_factory=dict)
    mode: str = ""  # speed/price mode ("" = standard)
    price_x: float = 1.0  # price multiplier of the mode

    def label(self) -> str:
        return f"{self.provider}/{self.model}@{self.reasoning}" + (f"+{self.mode}" if self.mode else "")


def resolve(cfg: dict, provider: str, tier: str, reasoning: str, mode: str = "") -> ModelSpec:
    """Turn (provider, abstract tier, abstract level, speed mode) into a concrete model and request parameters."""
    provs = cfg["providers"]
    if provider not in provs:
        raise LLMError(f"unknown provider {provider!r}; known: {', '.join(provs)}")
    if tier not in TIERS:
        raise LLMError(f"tier must be one of {TIERS}")
    if reasoning not in LEVELS:
        raise LLMError(f"reasoning must be one of {LEVELS}")
    p = provs[provider]
    tiers = p["tiers"]
    # A tier the provider lacks: the nearest stronger tier first (a two-model provider's standard is its frontier
    # model, not its light one: race 2026-11-15, Kimi and ZCode ran kimi-k3 and glm-5.3-flash), then weaker ones.
    i = TIERS.index(tier)
    order = [tier] + list(reversed(TIERS[:i])) + TIERS[i + 1:]
    used_tier = next(t for t in order if t in tiers)
    rmap = p.get("reasoning", {})
    rmap = {**rmap, **p.get("model_reasoning", {}).get(tiers[used_tier], {})}
    value = None
    for lvl in reversed(LEVELS[: LEVELS.index(reasoning) + 1]):
        if lvl in rmap:
            value = rmap[lvl]
            break
    if value is None:  # nothing at or below: take the lowest available (and turn thinking off for "none")
        value = next((rmap[lvl] for lvl in LEVELS if lvl in rmap), None)
    extra = _merge({}, p.get("extra", {}))
    price_x = 1.0
    model = tiers[used_tier]
    if mode and mode != "standard":
        modes = p.get("modes", {})
        if mode not in modes:
            raise LLMError(f"provider {provider} has no mode {mode!r}; modes: {', '.join(modes) or 'none'}")
        m = modes[mode]
        allowed = m.get("models") or list(m.get("model", {}))
        if allowed and model not in allowed:
            raise LLMError(f"mode {mode!r} of {provider} applies to {', '.join(allowed)}, not {model}")
        _merge(extra, m.get("body", {}))
        price_x = float(m.get("price_x", 1.0))
        model = m.get("model", {}).get(model, model)  # a speed variant that is a separate model
    else:
        mode = ""
    return ModelSpec(
        provider=provider,
        style=p["style"],
        base_url=p["base_url"].rstrip("/"),
        key_env=p["key_env"],
        model=model,
        tier=used_tier,
        reasoning=reasoning,
        effort_value=value,
        effort_field=p.get("effort_field", ""),
        extra=extra,
        mode=mode,
        price_x=price_x,
    )



def _set_path(body: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    d = body
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _merge(dst: dict, src: dict) -> dict:
    """Merge `src` into `dst`, nested dicts included; `dst` never shares a value with `src` (the config stays as read)."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _key(spec: ModelSpec) -> str:
    return os.environ.get(spec.key_env) or PROXY_PLACEHOLDER


SLOW_FAILURE_S = 30  # a failure after this long: the provider most likely processed (and billed) the request


def usage_of(obj) -> dict:
    """Normalised usage found in one JSON object of a response (any of the four wire formats), or {}."""
    if not isinstance(obj, dict):
        return {}
    if obj.get("type") == "message_start":  # Anthropic stream
        obj = obj.get("message") or {}
    if obj.get("type") in ("response.completed", "response.incomplete", "response.failed"):  # OpenAI Responses stream
        obj = obj.get("response") or {}
    out: dict = {}
    u = obj.get("usage")
    if isinstance(u, dict):
        if "prompt_tokens" in u or "completion_tokens" in u:  # Chat Completions
            out["input"] = u.get("prompt_tokens") or 0
            out["output"] = u.get("completion_tokens") or 0
            out["cached"] = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
            out["reasoning"] = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        elif "input_tokens_details" in u or "output_tokens_details" in u or "total_tokens" in u:  # Responses
            out["input"] = u.get("input_tokens") or 0
            out["output"] = u.get("output_tokens") or 0
            out["cached"] = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
            out["cache_write"] = (u.get("input_tokens_details") or {}).get("cache_write_tokens") or 0
            out["reasoning"] = (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0
        else:  # Anthropic (message, message_start, message_delta)
            if "input_tokens" in u or "cache_read_input_tokens" in u:
                out["input"] = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                                + (u.get("cache_creation_input_tokens") or 0))
                out["cached"] = u.get("cache_read_input_tokens") or 0
                out["cache_write"] = u.get("cache_creation_input_tokens") or 0
            if "output_tokens" in u:
                out["output"] = u.get("output_tokens") or 0
    if isinstance(u, dict) and isinstance(u.get("cost"), (int, float)):  # OpenRouter reports the cost itself
        out["cost_usd"] = float(u["cost"])
    if isinstance(u, dict) and isinstance(u.get("cost_in_usd_ticks"), (int, float)):  # xAI: 1 tick = 1e-10 USD
        out["cost_usd"] = u["cost_in_usd_ticks"] * 1e-10
    served = obj.get("service_tier") or (u.get("service_tier") if isinstance(u, dict) else None)
    if isinstance(u, dict) and u.get("speed"):  # Anthropic fast mode
        served = u["speed"]
    if served:
        out["served_tier"] = served
    um = obj.get("usageMetadata")
    if isinstance(um, dict):  # Gemini (cumulative in a stream)
        out["input"] = um.get("promptTokenCount") or 0
        out["output"] = (um.get("candidatesTokenCount") or 0) + (um.get("thoughtsTokenCount") or 0)
        out["reasoning"] = um.get("thoughtsTokenCount") or 0
        out["cached"] = um.get("cachedContentTokenCount") or 0
    return out


def _usage(obj: dict) -> dict:
    """The usage of a model answer for the ledger: input, output, reasoning and cached tokens (0 when absent)."""
    u = usage_of(obj)
    return {"input": 0, "output": 0, "reasoning": 0, "cached": 0,
            **{k: v for k, v in u.items() if k in ("input", "output", "reasoning", "cached", "cache_write")}}


def _maybe_billed(code: int, t0: float) -> bool:
    """A server error or cut connection after a long wait means the provider probably ran the request (a proxy cut it
    at 180 s on 2026-11-15 while the model kept generating); retrying would pay for it again, unseen by the ledger.
    429 (refused before any work) is always worth a retry."""
    return code != 429 and time.time() - t0 > SLOW_FAILURE_S


def _post(url: str, headers: dict, body: dict, timeout: float = 600, retries: int = 4, sse: bool = False):
    """POST JSON and return the decoded answer, or with `sse` the events of a streamed answer. Retries a refused or
    failed call with backoff, unless the provider probably ran it (`_maybe_billed`)."""
    data = json.dumps(body).encode()
    accept = {"Accept": "text/event-stream"} if sse else {}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **accept, **headers})
        t0 = time.time()
        events: list[dict] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _events(r, events) if sse else json.loads(r.read())
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:2000]
            billed = _maybe_billed(e.code, t0)
            if e.code in (429, 500, 502, 503, 504, 529) and attempt < retries and not billed:
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            raise LLMError(f"HTTP {e.code} from {url}: {text}", maybe_billed=billed) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            billed = bool(events) or _maybe_billed(0, t0)  # a stream that started was run: never sent again
            if attempt < retries and not billed:
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            raise LLMError(f"network error on {url}: {e}", maybe_billed=billed) from None
    raise LLMError("unreachable")


def _events(r, events: list[dict]) -> list[dict]:
    """The `data:` JSON of each server-sent event of a response, appended to `events` as they come."""
    for raw in r:
        line = raw.decode(errors="replace").strip()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue
    return events


def _post_sse(url: str, headers: dict, body: dict, timeout: float = 600, retries: int = 4) -> list[dict]:
    """POST a streamed request and return its events. A long call is streamed so that bytes keep flowing: a proxy that
    cuts a connection idle for a few minutes (this happened at 180 s on 2026-11-15) never sees it idle."""
    return _post(url, headers, body, timeout, retries, sse=True)


def responses_from_events(events: list[dict]) -> dict:
    """The final response object of a streamed Responses call (its last response.completed / incomplete event)."""
    for ev in reversed(events):
        if ev.get("type") in ("response.completed", "response.incomplete", "response.failed") and ev.get("response"):
            if ev["type"] == "response.failed":
                raise LLMError(f"response failed: {json.dumps(ev['response'].get('error'))[:500]}")
            return ev["response"]
    raise LLMError("streamed response ended without a final event", maybe_billed=bool(events))  # it started: it ran


def gemini_from_chunks(chunks: list[dict]) -> dict:
    """One generateContent answer from the chunks of streamGenerateContent: every part in order (thought signatures
    kept), the last finish reason and usage."""
    parts, finish, usage = [], "", {}
    for c in chunks:
        if c.get("error"):
            raise LLMError(f"gemini stream error: {json.dumps(c['error'])[:500]}")
        cand = (c.get("candidates") or [{}])[0]
        parts += (cand.get("content") or {}).get("parts") or []
        finish = cand.get("finishReason") or finish
        usage = c.get("usageMetadata") or usage
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}], "usageMetadata": usage}


# ---------------------------------------------------------------- OpenAI Chat Completions (and compatibles)


def _openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "assistant":
            w = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                w["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in m["tool_calls"]
                ]
            raw = m.get("_raw") or {}
            if raw.get("reasoning_content"):  # DeepSeek, Kimi, GLM, Qwen want it back during a tool loop
                w["reasoning_content"] = raw["reasoning_content"]
            out.append(w)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _chat_openai(spec: ModelSpec, messages, tools, max_tokens) -> Reply:
    body = {"model": spec.model, "messages": _openai_messages(messages)}
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    body["max_tokens"] = max_tokens
    _merge(body, spec.extra)
    if spec.reasoning == "none" and "thinking" in body:
        body["thinking"] = {"type": "disabled"}
    elif spec.effort_value is not None and spec.effort_field:
        _set_path(body, spec.effort_field, spec.effort_value)
    r = _post(f"{spec.base_url}/chat/completions", {"Authorization": f"Bearer {_key(spec)}"}, body)
    ch = (r.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_unparsed": fn.get("arguments")}
        calls.append({"id": c.get("id") or uuid.uuid4().hex[:12], "name": fn.get("name"), "arguments": args})
    usage = _usage(r)
    content = msg.get("content") or ""
    if isinstance(content, list):  # Mistral can return typed chunks (thinking + text)
        content = "".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    raw = {"reasoning_content": msg.get("reasoning_content")} if msg.get("reasoning_content") else {}
    am = {"role": "assistant", "content": content, "tool_calls": calls, "_raw": raw}
    return Reply(content, calls, usage, am, ch.get("finish_reason", ""))


# ---------------------------------------------------------------- OpenAI Responses API


def _responses_input(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    items: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            items.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            raw = (m.get("_raw") or {}).get("output")
            if raw is not None:  # replay reasoning, message and function_call items as returned
                items.extend(raw)
            else:
                if m.get("content"):
                    items.append({"role": "assistant", "content": m["content"]})
                items.extend({"type": "function_call", "call_id": c["id"], "name": c["name"],
                              "arguments": json.dumps(c["arguments"])} for c in m.get("tool_calls") or [])
        elif m["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
    return system, items


def _chat_responses(spec: ModelSpec, messages, tools, max_tokens) -> Reply:
    system, items = _responses_input(messages)
    body = {"model": spec.model, "input": items, "max_output_tokens": max_tokens, "store": False,
            "include": ["reasoning.encrypted_content"]}
    if system:
        body["instructions"] = system
    if tools:
        body["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]} for t in tools]
    _merge(body, spec.extra)
    if spec.effort_value is not None and spec.effort_field:
        _set_path(body, spec.effort_field, spec.effort_value)
    body["stream"] = True  # see _post_sse
    r = responses_from_events(_post_sse(f"{spec.base_url}/responses", {"Authorization": f"Bearer {_key(spec)}"}, body))
    output = r.get("output") or []
    text, calls = "", []
    for it in output:
        if it.get("type") == "message":
            text += "".join(c.get("text", "") for c in it.get("content", []) if c.get("type") == "output_text")
        elif it.get("type") == "function_call":
            try:
                args = json.loads(it.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": it.get("arguments")}
            calls.append({"id": it["call_id"], "name": it["name"], "arguments": args})
    usage = _usage(r)
    replay = [{k: v for k, v in it.items() if k not in ("status",)} for it in output]
    am = {"role": "assistant", "content": text, "tool_calls": calls, "_raw": {"output": replay}}
    return Reply(text, calls, usage, am, r.get("status", ""))


# ---------------------------------------------------------------- Gemini generateContent


def _gemini_schema(s):
    """Gemini accepts an OpenAPI subset: drop keys it refuses."""
    if isinstance(s, dict):
        return {k: _gemini_schema(v) for k, v in s.items() if k not in ("additionalProperties", "$schema", "default")}
    if isinstance(s, list):
        return [_gemini_schema(x) for x in s]
    return s


def _chat_gemini(spec: ModelSpec, messages, tools, max_tokens) -> Reply:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif m["role"] == "assistant":
            parts = (m.get("_raw") or {}).get("parts")
            if not parts:
                parts = ([{"text": m["content"]}] if m.get("content") else []) + [
                    {"functionCall": {"name": c["name"], "args": c["arguments"]}} for c in m.get("tool_calls") or []
                ]
            contents.append({"role": "model", "parts": parts})
        elif m["role"] == "tool":
            part = {"functionResponse": {"name": m["name"], "response": {"result": m["content"]}}}
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    body = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parameters": _gemini_schema(t["parameters"])}
            for t in tools]}]
    _merge(body, spec.extra)
    if spec.effort_value is not None and spec.effort_field:
        _set_path(body, spec.effort_field, spec.effort_value)
    if "thinkingConfig" in body.get("generationConfig", {}):  # thought summaries stream while the model thinks; without
        body["generationConfig"]["thinkingConfig"]["includeThoughts"] = True  # them nothing comes for minutes (182 s cut)
    url = f"{spec.base_url}/models/{spec.model}:streamGenerateContent?alt=sse"  # see _post_sse
    r = gemini_from_chunks(_post_sse(url, {"x-goog-api-key": _key(spec)}, body))
    cand = (r.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
    calls = [{"id": uuid.uuid4().hex[:12], "name": p["functionCall"]["name"], "arguments": p["functionCall"].get("args", {})}
             for p in parts if "functionCall" in p]
    usage = _usage(r)
    am = {"role": "assistant", "content": text, "tool_calls": calls, "_raw": {"parts": parts}}
    return Reply(text, calls, usage, am, cand.get("finishReason", ""))


# ---------------------------------------------------------------- Anthropic Messages


def _chat_anthropic(spec: ModelSpec, messages, tools, max_tokens) -> Reply:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = (m.get("_raw") or {}).get("content")
            if not blocks:
                blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                    for c in m.get("tool_calls") or []]
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    body = {"model": spec.model, "max_tokens": max_tokens, "messages": out}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    _merge(body, spec.extra)  # the speed mode's fields too: they are billed (price_x)
    if spec.reasoning == "none":
        body.pop("thinking", None)
    elif spec.effort_value is not None and spec.effort_field:
        _set_path(body, spec.effort_field, spec.effort_value)
    headers = {"x-api-key": _key(spec), "anthropic-version": "2023-06-01"}
    r = _post(f"{spec.base_url}/messages", headers, body)
    blocks = r.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})} for b in blocks if b.get("type") == "tool_use"]
    usage = _usage(r)
    am = {"role": "assistant", "content": text, "tool_calls": calls, "_raw": {"content": blocks}}
    return Reply(text, calls, usage, am, r.get("stop_reason", ""))


STYLES = {"openai-chat": _chat_openai, "openai-responses": _chat_responses, "gemini": _chat_gemini, "anthropic": _chat_anthropic}


def chat(spec: ModelSpec, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 16000) -> Reply:
    """One model call. `tools` are JSON-schema function specs: {"name", "description", "parameters"}."""
    if spec.style not in STYLES:
        raise LLMError(f"unknown style {spec.style!r}")
    return STYLES[spec.style](spec, messages, tools or [], max_tokens)
