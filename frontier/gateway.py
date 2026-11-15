"""Metering gateway: a local pass-through HTTP proxy between a harness and its LLM provider.

    python3 -m frontier.gateway --state <mission>/.frontier [--port 8787]

The harness's base URL is set to http://127.0.0.1:<port>/<route>/...; the gateway forwards every request unchanged
to the route's upstream (https://<host>/...), streams the answer back, reads the token usage from it (JSON or
server-sent events; OpenAI Chat Completions and Responses, Anthropic Messages, Gemini generateContent), charges the
mission's LLM budget, and answers 402 once the budget is spent. Requests are not translated, so every feature of the
harness keeps working; keys pass through untouched (or are injected further on by a network proxy).

Routes come from config/providers.toml: /<provider>/... -> the origin of its base_url (e.g. /openai/v1/responses ->
https://api.openai.com/v1/responses), plus any extra route given with --route name=https://host.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .milestones import Milestones, inject
from .budget import Budget, append_chained, now, worst_case_usd
from .config import load_config

DEFAULT_MAX_OUT = 32000  # output tokens assumed for a call that sets no limit

HOP = {"host", "content-length", "accept-encoding", "connection", "keep-alive", "transfer-encoding", "upgrade",
       "proxy-connection", "te", "trailer"}


def routes_from_config(cfg: dict) -> dict[str, tuple[str, str]]:
    """route name -> (upstream origin, provider)."""
    out = {}
    for name, p in cfg["providers"].items():
        u = urllib.parse.urlsplit(p["base_url"])
        out[name] = (f"{u.scheme}://{u.netloc}", name)
    return out


def base_url_for(port: int, provider: str, cfg: dict, upstream: str = "") -> str:
    """The URL to give the harness in place of the provider's base URL (same path, local origin)."""
    path = urllib.parse.urlsplit(upstream or cfg["providers"][provider]["base_url"]).path.rstrip("/")
    return f"http://127.0.0.1:{port}/{provider}{path}"


# ---------------------------------------------------------------- usage extraction


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


def merge(total: dict, part: dict) -> None:
    """Stream events repeat or accumulate counts: keep the largest value of each field (the last text value)."""
    for k, v in part.items():
        total[k] = v if isinstance(v, str) else max(total.get(k, 0), v or 0)


def mode_of(provider_conf: dict, served: str) -> tuple[str, float]:
    """The configured mode whose request body asks for the tier the response says was served, and its price
    multiplier; ("", 1.0) for the standard tier."""
    if not served or served in ("default", "standard", "auto", "standard_only"):
        return "", 1.0
    aliases = {"fast": {"fast", "priority"}, "priority": {"fast", "priority"}}
    for name, m in provider_conf.get("modes", {}).items():
        wanted = {str(v) for v in m.get("body", {}).values()}
        if wanted & aliases.get(served, {served}):
            return name, float(m.get("price_x", 1.0))
    return served, 1.0


def worst_case(cfg: dict, provider: str, path: str, body: bytes) -> float:
    """Upper bound of a call: the whole request as uncached input, its output limit (or DEFAULT_MAX_OUT), at the
    dearest speed mode of the provider (the tier actually served is only known from the response)."""
    try:
        obj = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        obj = {}
    out = (obj.get("max_output_tokens") or obj.get("max_completion_tokens") or obj.get("max_tokens")
           or (obj.get("generationConfig") or {}).get("maxOutputTokens") or DEFAULT_MAX_OUT)
    # Some harnesses ask for the model's whole output window on every call (Kimi Code: 262,144 tokens); reserving that
    # would let one call hold most of a small budget. The reservation is capped; the call is charged what it used.
    out = min(int(out), int(cfg.get("limits", {}).get("max_reserved_output_tokens", DEFAULT_MAX_OUT)))
    model = model_of(body, path, None)
    return worst_case_usd(provider, model, len(body), int(out), requested_price_x(cfg, provider, obj, model))


def requested_price_x(cfg: dict, provider: str, req: dict, model: str) -> float:
    """Price multiplier of the speed mode the request asks for (its body fields, for a model the mode covers)."""
    px = 1.0
    for m in cfg.get("providers", {}).get(provider, {}).get("modes", {}).values():
        asked = all(req.get(k) == v for k, v in m.get("body", {}).items())
        if asked and (not m.get("models") or model in m["models"]):
            px = max(px, float(m.get("price_x", 1.0)))
    return px


GENERATION = ("/chat/completions", "/responses", "/responses/compact", "/messages", "/interactions",
              ":generateContent", ":streamGenerateContent")


def is_generation(method: str, rest: str) -> bool:
    """A billed model call (not a batch, a cancel, a token count or a read)."""
    return method == "POST" and rest.split("?", 1)[0].rstrip("/").endswith(GENERATION)


def remap(path: str, body: bytes, model_map: dict[str, str]) -> tuple[str, bytes]:
    """Send a call for a model the harness picks by itself (a hard-wired helper model) to the one the mission maps it to:
    the model in the Gemini path (/models/<model>:...) or the body's `model` field."""
    if not model_map:
        return path, body
    if "/models/" in path:
        head, tail = path.split("/models/", 1)
        m, sep, after = tail.partition(":")
        if m in model_map:
            return f"{head}/models/{model_map[m]}{sep}{after}", body
    try:
        obj = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return path, body
    if isinstance(obj, dict) and obj.get("model") in model_map:
        obj["model"] = model_map[obj["model"]]
        return path, json.dumps(obj).encode()
    return path, body


def strip_fields(body: bytes, fields: list[str]) -> bytes:
    """Drop message fields a provider refuses (config/providers.toml `strip_message_fields`) from a request."""
    if not fields:
        return body
    try:
        obj = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    msgs = obj.get("messages") if isinstance(obj, dict) else None
    if not isinstance(msgs, list) or not any(isinstance(m, dict) and set(fields) & m.keys() for m in msgs):
        return body
    obj["messages"] = [{k: v for k, v in m.items() if k not in fields} if isinstance(m, dict) else m for m in msgs]
    return json.dumps(obj).encode()


def model_of(req_body: bytes, path: str, resp_obj: dict | None) -> str:
    try:
        m = json.loads(req_body or b"{}").get("model")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        m = None
    if not m and resp_obj:
        m = resp_obj.get("model") or (resp_obj.get("message") or {}).get("model") or (resp_obj.get("response") or {}).get("model")
    if not m and "/models/" in path:  # Gemini: /v1beta/models/<model>:generateContent
        m = path.split("/models/", 1)[1].split(":", 1)[0]
    return m or "unknown"


# ---------------------------------------------------------------- server


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, budget: Budget, routes: dict[str, tuple[str, str]], agent: str = "harness",
                 cfg: dict | None = None, model_map: dict[str, str] | None = None):
        super().__init__(addr, Handler)
        self.budget, self.routes, self.agent, self.model_map = budget, routes, agent, dict(model_map or {})
        self.milestones = Milestones(budget)
        self.cfg = cfg if cfg is not None else load_config()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Gateway

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._proxy()

    def do_POST(self):  # noqa: N802
        self._proxy()

    def do_PUT(self):  # noqa: N802
        self._proxy()

    def do_DELETE(self):  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        parts = self.path.lstrip("/").split("/", 1)
        route = parts[0]
        if route == "_frontier" and parts[-1].startswith("status"):
            return self._send_json(200, self.server.budget.status())
        if route not in self.server.routes:
            return self._send_json(404, {"error": {"message": f"unknown route /{route}; known: {sorted(self.server.routes)}"}})
        origin, provider = self.server.routes[route]
        rest = "/" + (parts[1] if len(parts) > 1 else "")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        rest, body = remap(rest, body, self.server.model_map)
        body = strip_fields(body, (self.server.cfg["providers"].get(provider) or {}).get("strip_message_fields", []))
        is_llm_call = is_generation(self.command, rest)
        rid, worst = None, 0.0
        if is_llm_call:
            worst = worst_case(self.server.cfg, provider, rest, body)
            rid, why = self.server.budget.reserve(worst, f"gateway {rest[:60]}")
            if rid is None:  # recorded, so that the launcher does not restart a session only to be refused again
                append_chained(self.server.budget.dir / "events.jsonl", {
                    "t": now().isoformat(timespec="seconds"), "event": "llm_refused", "agent": self.server.agent,
                    "why": why})
                return self._send_json(402, {"error": {"type": "budget_exhausted", "code": "budget_exhausted", "message": why}})
            body = inject(rest, body, self.server.milestones.notice())  # 10, 25, 50, 80, 95, 98 % of a budget
            body = self._with_stream_usage(rest, body)
        try:
            self._forward(origin, rest, body, provider, is_llm_call, worst)
        finally:
            self.server.budget.release(rid)

    def _forward(self, origin: str, rest: str, body: bytes, provider: str, is_llm_call: bool, worst: float) -> None:
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Accept-Encoding"] = "identity"
        req = urllib.request.Request(origin + rest, data=body if self.command in ("POST", "PUT") else None,
                                     method=self.command, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
            status = resp.status
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
        except (urllib.error.URLError, TimeoutError) as e:
            return self._send_json(502, {"error": {"message": f"gateway: upstream unreachable: {e}"}})
        ctype = resp.headers.get("Content-Type", "")
        streaming = "text/event-stream" in ctype or "stream" in rest.lower() and "json" not in ctype
        self.send_response(status)
        for k, v in resp.headers.items():
            if k.lower() not in HOP and k.lower() != "content-encoding":
                self.send_header(k, v)
        usage: dict = {}
        last_obj: dict | None = None
        cut = False  # the harness hung up mid-stream: the usage, sent last, was not seen
        if streaming:
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            buf = b""
            while True:
                chunk = resp.readline()
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    resp.close()
                    cut = True
                    break
                if is_llm_call:
                    buf = chunk.strip()
                    if buf.startswith(b"data:"):
                        try:
                            obj = json.loads(buf[5:].strip())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        merge(usage, usage_of(obj))
                        if isinstance(obj, dict) and last_obj is None:
                            last_obj = obj
        else:
            data = resp.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            if is_llm_call and 200 <= status < 300:
                try:
                    last_obj = json.loads(data)
                    objs = last_obj if isinstance(last_obj, list) else [last_obj]  # Gemini can answer a JSON array
                    for o in objs:
                        merge(usage, usage_of(o))
                    last_obj = objs[-1] if objs else None
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        if is_llm_call and 200 <= status < 300 and (cut or not usage):  # billed, but by how much is unknown
            model = model_of(body, rest, last_obj if isinstance(last_obj, dict) else None)
            self.server.budget.charge_llm("gateway", self.server.agent, provider, model, usage, usd=worst, exact=False)
        elif is_llm_call and usage:
            model = model_of(body, rest, last_obj if isinstance(last_obj, dict) else None)
            served = usage.pop("served_tier", "") or resp.headers.get("x-gemini-service-tier", "")
            conf = self.server.cfg.get("providers", {}).get(provider, {})
            mode, px = mode_of(conf, served)
            reported = usage.pop("cost_usd", None)
            self.server.budget.charge_llm("gateway", self.server.agent, provider, model, usage, mode, px, usd=reported)

    @staticmethod
    def _with_stream_usage(path: str, body: bytes) -> bytes:
        """Chat Completions streams carry usage only when asked: ask for it."""
        if "/chat/completions" not in path:
            return body
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
        if obj.get("stream") and not (obj.get("stream_options") or {}).get("include_usage"):
            obj["stream_options"] = {**(obj.get("stream_options") or {}), "include_usage": True}
            return json.dumps(obj).encode()
        return body


def serve(state: str, port: int = 0, extra_routes: dict[str, str] | None = None, agent: str = "harness",
          model_map: dict[str, str] | None = None, providers: list[str] | None = None) -> Gateway:
    """`providers`: when given, only their routes are served (a mission's harness reaches no other provider)."""
    cfg = load_config()
    routes = routes_from_config(cfg)
    if providers:
        routes = {k: v for k, v in routes.items() if v[1] in providers}
    for name, url in (extra_routes or {}).items():
        u = urllib.parse.urlsplit(url)
        routes[name] = (f"{u.scheme}://{u.netloc}", name.split("-")[0])
    gw = Gateway(("127.0.0.1", port), Budget(pathlib.Path(state)), routes, agent, cfg, model_map)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    return gw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m frontier.gateway")
    ap.add_argument("--state", required=True)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--route", action="append", default=[], help="name=https://host (extra upstream)")
    a = ap.parse_args(argv)
    extra = dict(r.split("=", 1) for r in a.route)
    gw = serve(a.state, a.port, extra)
    print(f"gateway on http://127.0.0.1:{gw.server_address[1]} routes: {sorted(gw.routes)}", file=sys.stderr)
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
