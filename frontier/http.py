"""Small HTTP helpers shared by the tools (standard library only)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .credentials import auth_header


def request(method: str, url: str, headers: dict | None = None, body=None, timeout: float = 120) -> tuple[int, str]:
    data = None
    h = dict(headers or {})
    if body is not None:
        if isinstance(body, str):
            data = body.encode()
            h.setdefault("Content-Type", "text/plain")
        else:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method.upper(), headers={"User-Agent": "frontier-agent", **h})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, f"network error: {e}"


class APIError(RuntimeError):
    pass


def scw_defaults() -> dict:
    zone = os.environ.get("SCW_DEFAULT_ZONE", "fr-par-2")
    return {"zone": zone, "region": os.environ.get("SCW_DEFAULT_REGION", zone.rsplit("-", 1)[0]),
            "project_id": os.environ.get("SCW_DEFAULT_PROJECT_ID", ""),
            "organization_id": os.environ.get("SCW_DEFAULT_ORGANIZATION_ID", "")}


def scw_raw(method: str, path: str, body=None, query: dict | None = None, content_type: str = "") -> tuple[int, str]:
    for k, v in scw_defaults().items():
        path = path.replace("{" + k + "}", v)
    url = "https://api.scaleway.com" + (path if path.startswith("/") else "/" + path)
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")}, doseq=True)
    headers = auth_header("scaleway")
    if content_type:
        headers["Content-Type"] = content_type
    return request(method, url, headers, body)


def scw(method: str, path: str, body=None, query: dict | None = None, content_type: str = "", ok404: bool = False):
    """JSON call; raises APIError on an error status (None on 404 when ok404)."""
    code, text = scw_raw(method, path, body, query, content_type)
    if ok404 and code == 404:
        return None
    if not 200 <= code < 300:
        raise APIError(f"Scaleway {method} {path}: HTTP {code} {text[:800]}")
    return json.loads(text) if text.strip() else {}


def mcp_rpc(method: str, params: dict | None = None, timeout: float = 180) -> dict:
    """One JSON-RPC call to the Runpod MCP server: its decoded answer, plain JSON or server-sent events."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    code, text = request("POST", os.environ.get("RUNPOD_MCP_URL", "https://mcp.runpod.io/"),
                         {**auth_header("runpod"), "Accept": "application/json, text/event-stream"}, payload, timeout)
    if code != 200:
        raise APIError(f"HTTP {code} {text[:1500]}")
    if "\ndata:" in text or text.startswith(("event:", "data:")):  # server-sent events: last data line
        text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise APIError(text[:1500]) from None


RATE_LIMIT_RETRIES = 4


def mcp_call(tool: str, arguments: dict | None = None, timeout: float = 180) -> tuple[bool, str]:
    """Call a Runpod MCP tool. Returns (ok, text). A call refused by the rate limit (429: no work done) is retried."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            r = mcp_rpc("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)
        except APIError as e:
            ok, text = False, str(e)
        else:
            res = r.get("result", {})
            ok, text = ((False, json.dumps(r["error"])) if "error" in r else
                        (not res.get("isError"), "\n".join(c.get("text", json.dumps(c)) for c in res.get("content", []))))
        if ok or attempt == RATE_LIMIT_RETRIES or not (text.startswith("HTTP 429") or '"status": 429' in text):
            return ok, text
        time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def mcp_json(tool: str, arguments: dict | None = None):
    ok, text = mcp_call(tool, arguments)
    if not ok:
        raise APIError(f"Runpod {tool}: {text[:1500]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
