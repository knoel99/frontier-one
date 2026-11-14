"""Tools every agent can be given. A tool is a JSON-schema function spec plus a Python function.

Cloud tools (scaleway, runpod, runpod_mcp) only read unless the run allows spending (--allow-spend): anything that
creates, changes or deletes a resource costs money or loses data.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from collections.abc import Callable

from . import apidocs
from .llm import PROXY_PLACEHOLDER


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable  # fn(ctx, **arguments) -> str

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
I = {"type": "integer"}
J = {"type": "object", "description": "JSON object"}


def _path(ctx, p: str) -> pathlib.Path:
    q = (ctx.workspace / p).resolve()
    if not str(q).startswith(str(ctx.workspace.resolve())):
        raise PermissionError(f"{p} is outside the workspace {ctx.workspace}")
    return q


# ---------------------------------------------------------------- workspace


def t_shell(ctx, command: str, timeout_s: int = 0) -> str:
    limit = int(timeout_s or ctx.limits.get("shell_timeout_s", 600))
    try:
        r = subprocess.run(["bash", "-lc", command], cwd=ctx.workspace, capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return f"timeout after {limit} s"
    return f"exit {r.returncode}\n--- stdout\n{r.stdout}\n--- stderr\n{r.stderr}"


def t_read_file(ctx, path: str, offset: int = 0, limit: int = 400) -> str:
    lines = _path(ctx, path).read_text(errors="replace").splitlines()
    chunk = lines[offset: offset + limit]
    tail = f"\n[{len(lines)} lines; showing {offset}-{offset + len(chunk)}]" if len(lines) > len(chunk) else ""
    return "\n".join(f"{i + offset + 1:>6}\t{ln}" for i, ln in enumerate(chunk)) + tail


def t_write_file(ctx, path: str, content: str) -> str:
    p = _path(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {path}"


def t_edit_file(ctx, path: str, old: str, new: str) -> str:
    p = _path(ctx, path)
    s = p.read_text()
    n = s.count(old)
    if n != 1:
        return f"error: `old` occurs {n} times in {path}; it must occur exactly once"
    p.write_text(s.replace(old, new))
    return f"edited {path}"


# ---------------------------------------------------------------- HTTP


def _http(method: str, url: str, headers: dict | None = None, body=None, timeout: float = 120) -> tuple[int, str]:
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


def t_http_request(ctx, method: str, url: str, headers: dict | None = None, body=None) -> str:
    code, text = _http(method, url, headers, body)
    return f"HTTP {code}\n{text}"


def _spend_guard(ctx, what: str) -> str | None:
    if ctx.allow_spend:
        return None
    return (f"refused: {what} would create, change or delete a cloud resource and this run does not allow spending. "
            "Read-only calls work. Ask the user to start the run with --allow-spend if this is needed.")


# ---------------------------------------------------------------- Scaleway


def t_scaleway(ctx, method: str, path: str, body: dict | None = None, query: dict | None = None,
               raw_body: str = "", content_type: str = "") -> str:
    """REST call to https://api.scaleway.com. {zone}, {region}, {project_id} in the path are filled from defaults."""
    zone = os.environ.get("SCW_DEFAULT_ZONE", "fr-par-2")
    defaults = {"zone": zone, "region": os.environ.get("SCW_DEFAULT_REGION", zone.rsplit("-", 1)[0]),
                "project_id": os.environ.get("SCW_DEFAULT_PROJECT_ID", ""),
                "organization_id": os.environ.get("SCW_DEFAULT_ORGANIZATION_ID", "")}
    for k, v in defaults.items():
        path = path.replace("{" + k + "}", v)
    if method.upper() != "GET" and (g := _spend_guard(ctx, f"{method} {path}")):
        return g
    if isinstance(body, dict) and method.upper() == "POST" and "project" in body and not body["project"]:
        body["project"] = defaults["project_id"]
    url = "https://api.scaleway.com" + (path if path.startswith("/") else "/" + path)
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    key = os.environ.get("SCW_SECRET_KEY") or PROXY_PLACEHOLDER
    if raw_body:
        body = raw_body
    headers = {"X-Auth-Token": key, **({"Content-Type": content_type} if content_type else {})}
    code, text = _http(method, url, headers, body)
    return f"HTTP {code}\n{text}"


# ---------------------------------------------------------------- Runpod


def t_runpod(ctx, method: str, path: str, body: dict | None = None, query: dict | None = None) -> str:
    """REST v1 call to https://rest.runpod.io/v1 (needs RUNPOD_API_KEY), or the serverless job API api.runpod.ai/v2."""
    if method.upper() != "GET" and (g := _spend_guard(ctx, f"{method} {path}")):
        return g
    if path.startswith("https://"):
        url = path
    else:
        url = "https://rest.runpod.io/v1" + (path if path.startswith("/") else "/" + path)
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    key = os.environ.get("RUNPOD_API_KEY") or PROXY_PLACEHOLDER
    code, text = _http(method, url, {"Authorization": f"Bearer {key}"}, body)
    if code == 401 and key == PROXY_PLACEHOLDER:
        text += "\n(no RUNPOD_API_KEY and no injected credential for this host: use the runpod_mcp tool instead)"
    return f"HTTP {code}\n{text}"


MCP_READ_PREFIXES = ("list-", "get-", "describe-", "search-", "lookup-", "read-", "endpoint-health", "stream-",
                     "ask_question")


def t_runpod_mcp(ctx, tool: str, arguments: dict | None = None) -> str:
    """Call a tool of the Runpod MCP server (https://mcp.runpod.io, REST v2 surface)."""
    if not tool.startswith(MCP_READ_PREFIXES) and (g := _spend_guard(ctx, f"runpod MCP {tool}")):
        return g
    key = os.environ.get("RUNPOD_API_KEY") or PROXY_PLACEHOLDER
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments or {}}}
    code, text = _http("POST", os.environ.get("RUNPOD_MCP_URL", "https://mcp.runpod.io/"),
                       {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"}, payload,
                       timeout=180)
    if code != 200:
        return f"HTTP {code}\n{text}"
    if text.startswith("event:") or "\ndata:" in text:  # server-sent events: keep the last data line
        text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
    try:
        r = json.loads(text)
    except json.JSONDecodeError:
        return text
    if "error" in r:
        return f"MCP error: {json.dumps(r['error'])}"
    res = r.get("result", {})
    out = "\n".join(c.get("text", json.dumps(c)) for c in res.get("content", []))
    return ("ERROR: " if res.get("isError") else "") + out


# ---------------------------------------------------------------- docs


def t_api_docs(ctx, action: str, query: str = "", id: str = "", doc: str = "") -> str:  # noqa: A002
    if action == "list":
        return apidocs.list_docs()
    if action == "search":
        return apidocs.search(query, doc)
    if action == "show":
        return apidocs.show(id or query)
    return "action must be list, search or show"


# ---------------------------------------------------------------- registry

REGISTRY: dict[str, Tool] = {}


def register(t: Tool) -> Tool:
    REGISTRY[t.name] = t
    return t


register(Tool("shell", "Run a bash command in the workspace and return exit code, stdout and stderr.",
              _obj({"command": S, "timeout_s": I}, ["command"]), t_shell))
register(Tool("read_file", "Read a text file of the workspace with line numbers (offset and limit in lines).",
              _obj({"path": S, "offset": I, "limit": I}, ["path"]), t_read_file))
register(Tool("write_file", "Create or overwrite a file of the workspace.",
              _obj({"path": S, "content": S}, ["path", "content"]), t_write_file))
register(Tool("edit_file", "Replace the unique occurrence of `old` by `new` in a workspace file.",
              _obj({"path": S, "old": S, "new": S}, ["path", "old", "new"]), t_edit_file))
register(Tool("http_request", "Generic HTTP request (method, url, optional headers and JSON body). Returns status and body.",
              _obj({"method": S, "url": S, "headers": J, "body": J}, ["method", "url"]), t_http_request))
register(Tool(
    "api_docs",
    "Offline official documentation of the Scaleway and Runpod APIs. action=list: the documents; "
    "action=search query='create gpu instance' (optional doc='scaleway/instance'): matching operations with ids; "
    "action=show id='scaleway/instance:CreateServer': parameters, request and response schemas. "
    "Also id='scaleway/guide' or 'runpod/guide' for auth, flows and pitfalls. Read the docs here, never guess an API.",
    _obj({"action": {"type": "string", "enum": ["list", "search", "show"]}, "query": S, "id": S, "doc": S}, ["action"]),
    t_api_docs))
register(Tool(
    "scaleway",
    "Call the Scaleway API (https://api.scaleway.com; auth handled). path like '/instance/v1/zones/{zone}/servers'; "
    "{zone}, {region}, {project_id} are filled with the defaults. Look the operation up with api_docs first. "
    "body: JSON object; raw_body: text sent as is (e.g. a cloud-init script, content_type='text/plain'). "
    "Non-GET calls need a run with --allow-spend.",
    _obj({"method": S, "path": S, "body": J, "query": J, "raw_body": S, "content_type": S},
         ["method", "path"]), t_scaleway))
register(Tool(
    "runpod",
    "Call the Runpod REST API v1 (https://rest.runpod.io/v1, e.g. path '/pods'), or a full https://api.runpod.ai/v2/... "
    "serverless job URL. Needs RUNPOD_API_KEY; otherwise use runpod_mcp. Non-GET calls need --allow-spend.",
    _obj({"method": S, "path": S, "body": J, "query": J}, ["method", "path"]), t_runpod))
register(Tool(
    "runpod_mcp",
    "Call a tool of the Runpod MCP server (pods, endpoints, jobs, GPU stock and prices, billing; 125 tools). "
    "Find the tool and its arguments with api_docs (doc='runpod/mcp'). Tools not starting with list-/get-/... "
    "need --allow-spend.",
    _obj({"tool": S, "arguments": J}, ["tool"]), t_runpod_mcp))

DEFAULT_TOOLS = ["shell", "read_file", "write_file", "edit_file", "http_request", "api_docs", "scaleway", "runpod",
                 "runpod_mcp", "spawn_agent"]
