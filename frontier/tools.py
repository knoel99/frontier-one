"""Tools an agent can be given: a JSON-schema function spec plus a Python function fn(ctx, **arguments) -> str.

Two sets:
  BASE_TOOLS   every agent: workspace, HTTP, spawn_agent (registered in agent.py)
  INFRA_TOOLS  the `infra` role: ready-made Scaleway/Runpod functions, the API catalog, raw API calls
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from . import budget, catalog, hf, infra, symbolic
from .http import mcp_call, request, scw_raw


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class Answer(str):
    """A tool's answer that states whether the call failed. A plain `str` answer failed when it starts with "error" or
    "refused"; a tool whose answer can start with any text (the caller's own output) returns an Answer."""

    failed: bool

    def __new__(cls, text: str, failed: bool):
        self = super().__new__(cls, text)
        self.failed = failed
        return self


def failed(answer) -> bool:
    return answer.failed if isinstance(answer, Answer) else str(answer).startswith(("error", "refused"))


def _truncate(s: str, n: int) -> str:
    """`s` in at most n characters: its head and its tail around a note of what was cut (an answer cut once is not cut
    again)."""
    if len(s) <= n:
        return s
    keep = max(n - 40, 0)  # 40: room for the note
    return s[: keep - keep // 2] + f"\n…[{len(s) - keep} chars cut]…\n" + s[len(s) - keep // 2:]


def _obj(props: dict, required: list[str] | tuple = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


S, I, B, N = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "number"}
J = {"type": "object", "description": "JSON object"}
SL = {"type": "array", "items": {"type": "string"}}


def _path(ctx, p: str) -> pathlib.Path:
    q = (ctx.workspace / p).resolve()
    if not q.is_relative_to(ctx.workspace.resolve()):
        raise PermissionError(f"{p} is outside the workspace {ctx.workspace}")
    return q


def t_shell(ctx, command: str, timeout_s: int = 0) -> str:
    limit = int(timeout_s or ctx.limits.get("shell_timeout_s", 600))
    try:
        r = subprocess.run(["bash", "-lc", command], cwd=ctx.workspace, capture_output=True, text=True, timeout=limit, check=False)
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


def t_sympy(ctx, code: str, timeout_s: float = 0) -> Answer:
    limit = float(timeout_s) if float(timeout_s or 0) >= 1 else float(ctx.limits.get("sympy_timeout_s", 120))
    text, bad = symbolic.run(code, ctx.workspace, limit)
    return Answer(_truncate(text, ctx.limits.get("tool_output_chars", 20000)), bad)


def t_http_request(ctx, method: str, url: str, headers: dict | None = None, body=None) -> str:
    code, text = request(method, url, headers, body)
    return f"HTTP {code}\n{text}"


def t_api_docs(ctx, action: str, query: str = "", id: str = "", doc: str = "") -> str:
    if action == "list":
        return catalog.overview()
    if action == "search":
        return catalog.search(query, doc)
    if action == "show":
        return catalog.show(id or query)
    return "action must be list, search or show"


# Raw calls under a cloud budget. The lists name what is free, not what is billed: the live APIs have more operations
# and tools than the bundled catalog, so a raw call the framework does not know is refused, not forwarded.
# - A creation is forwarded only when it is known to bill nothing; the ready-made functions price, reserve and record
#   the billed ones (a raw creation would bill outside the cap, and be left out of the cleanup).
# - A call that changes, runs on or deletes a resource is forwarded only for resources of the run's ledger (the teams
#   of a race share the accounts). Templates, security groups and the like are not billed and stay open.
FREE_SCW_CREATE = re.compile(r":Create(PlacementGroup|SecurityGroup|SecurityGroupRule|JobDefinition)$")
FREE_SCW_COLLECTIONS = {"security_groups", "rules", "placement_groups", "private_nics"}
SCW_ID = re.compile(r"/([A-Za-z_-]+)/([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})(?=/|$)")
FREE_SCW_KEYS = {"project", "project_id", "organization", "organization_id", "security_group", "security_group_id",
                 "placement_group", "placement_group_id", "private_network_id"}  # body fields that name nothing billed
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
FREE_RUNPOD = {"create-template", "create-secret", "create-registry", "describe-tool"}
FREE_RUNPOD_KEYS = {"templateId", "containerRegistryAuthId"}


def _named(obj, ids_of, free: set[str], key: str = "") -> list[str]:
    """The resource ids a request body names (`ids_of(key, text)`), outside the fields known to name nothing billed."""
    if isinstance(obj, dict):
        return [i for k, v in obj.items() if k not in free for i in _named(v, ids_of, free, k)]
    if isinstance(obj, list):
        return [i for v in obj for i in _named(v, ids_of, free, key)]
    return ids_of(key, obj) if isinstance(obj, str) else []


def _capped(ctx, provider: str) -> bool:
    return ctx.run.budget.caps.get(budget.CLOUD_CURRENCY[provider][0]) is not None


def _unmetered(provider: str, what: str) -> str:
    return (f"refused: under a {provider} budget a raw call goes through when it reads, creates what is known to bill "
            f"nothing or acts on the run's own resources, and {what} does none of these; scw_create_instance and "
            "rp_create_pod price what they create, reserve it and delete it at the end.")


def _foreign(ctx, ids: list[str]) -> str | None:
    owned = {x for r in ctx.run.resources for x in [r["id"], *r.get("volumes", []), *r.get("ips", [])]}
    if bad := [i for i in ids if i not in owned]:
        return (f"refused: this run did not create {', '.join(bad)}; raw calls only change the run's own resources "
                "(rp_delete_pod and scw_delete_instance take not_created_by_this_run=true when the task names one).")
    return None


def t_scaleway(ctx, method: str, path: str, body: dict | None = None, query: dict | None = None, raw_body: str = "",
               content_type: str = "") -> str:
    if method.upper() != "GET":
        if g := infra._need_spend(ctx, f"{method} {path}"):
            return g
        if _capped(ctx, "scaleway"):
            ids = [i for collection, i in SCW_ID.findall("/" + path.split("?", 1)[0].strip("/"))
                   if collection not in FREE_SCW_COLLECTIONS]
            try:  # the body too: a call on the run's own server can name another team's volume there
                sent = json.loads(raw_body) if raw_body else body
            except json.JSONDecodeError:
                sent = raw_body
            if g := _foreign(ctx, ids + _named(sent, lambda key, text: UUID.findall(text), FREE_SCW_KEYS)):
                return g
            op = catalog.operation(method, path)
            if method.upper() == "POST" and not ids and not FREE_SCW_CREATE.search(op):
                return _unmetered("scaleway", op or f"{method} {path}")
    code, text = scw_raw(method, path, raw_body or body, query, content_type)
    return f"HTTP {code}\n{text}"


def t_runpod_mcp(ctx, tool: str, arguments: dict | None = None) -> str:
    if tool not in catalog.read_only_mcp_tools() and tool != "describe-tool":
        if g := infra._need_spend(ctx, f"Runpod MCP tool {tool}"):
            return g
        if _capped(ctx, "runpod") and tool not in FREE_RUNPOD and not tool.endswith("-template"):
            ids = _named(arguments or {}, lambda key, text: [text] if key == "id" or key.endswith("Id") else [],
                         FREE_RUNPOD_KEYS)
            if tool.startswith(("create-", "deploy-")) or not ids:  # a creation, or a tool the catalog does not know
                return _unmetered("runpod", f"Runpod MCP tool {tool}")
            if g := _foreign(ctx, ids):
                return g
    ok, text = mcp_call(tool, arguments)
    return text if ok else "ERROR: " + text


REGISTRY: dict[str, Tool] = {}


def register(name: str, description: str, parameters: dict, fn: Callable) -> None:
    REGISTRY[name] = Tool(name, description, parameters, fn)


register("shell", "Run a bash command in the workspace; returns exit code, stdout and stderr.",
         _obj({"command": S, "timeout_s": I}, ["command"]), t_shell)
register("read_file", "Read a workspace text file with line numbers (offset, limit in lines).",
         _obj({"path": S, "offset": I, "limit": I}, ["path"]), t_read_file)
register("write_file", "Create or overwrite a workspace file.", _obj({"path": S, "content": S}, ["path", "content"]), t_write_file)
register("edit_file", "Replace the unique occurrence of `old` by `new` in a workspace file.",
         _obj({"path": S, "old": S, "new": S}, ["path", "old", "new"]), t_edit_file)
register("http_request", "Generic HTTP request; returns status and body.",
         _obj({"method": S, "url": S, "headers": J, "body": J}, ["method", "url"]), t_http_request)

register("scw_offers", "Scaleway Instance types of a zone with real price (EUR/hour, from the Product Catalog), GPU, "
         "memory and stock, cheapest first. zone: one zone (default fr-par-2) or 'all'. GPU types only, available or "
         "scarce only, unless told otherwise.",
         _obj({"zone": S, "min_gpu_memory_gb": N, "gpu_only": B, "include_unavailable": B}), infra.scw_offers)
register("scw_create_instance", "Create a Scaleway Instance (GPU types get the Ubuntu GPU OS image with NVIDIA driver and "
         "Docker), optional cloud-init script, power on, wait until running; returns id, state, public IPs, price. "
         "max_hours: deleted by the framework after that; the cloud budget reserves price x max_hours. "
         "Recorded in the run ledger. Needs spending allowed.",
         _obj({"name": S, "commercial_type": S, "max_hours": N, "zone": S, "image_label": S, "cloud_init": S, "tags": SL,
               "wait_running": B}, ["name", "commercial_type", "max_hours"]), infra.scw_create_instance)
register("scw_instance", "State, type, public IPs and volumes of a Scaleway Instance.",
         _obj({"server_id": S, "zone": S}, ["server_id"]), infra.scw_instance)
register("scw_list_instances", "Scaleway Instances of a zone carrying a tag (default 'frontier').",
         _obj({"zone": S, "tag": S}), infra.scw_list_instances)
register("scw_delete_instance", "Delete a Scaleway Instance AND its Block Storage volumes and flexible IPs (a plain "
         "terminate leaves the volumes billing). Only resources of this run unless not_created_by_this_run=true.",
         _obj({"server_id": S, "zone": S, "not_created_by_this_run": B}, ["server_id"]), infra.scw_delete_instance)
register("rp_offers", "Runpod GPU types for pods with USD/hour per GPU, memory, stock and data centers, cheapest first.",
         _obj({"min_memory_gb": N, "cloud": {"type": "string", "enum": ["SECURE", "COMMUNITY"]}, "gpu_count": I,
               "include_unavailable": B}), infra.rp_offers)
register("rp_create_pod", "Create a Runpod pod (billed per second from creation). gpu_type_id from rp_offers; command: "
         "container command as one shell string; ports like ['22/tcp','8888/http']. max_hours: deleted by the framework "
         "after that; the cloud budget reserves price x max_hours. Recorded in the run ledger. Needs spending allowed.",
         _obj({"name": S, "gpu_type_id": S, "max_hours": N, "image": S, "gpu_count": I, "cloud": S, "disk_gb": I,
               "ports": SL, "env": J, "command": S, "start_ssh": B}, ["name", "gpu_type_id", "max_hours"]), infra.rp_create_pod)
register("rp_pod", "Details and state of a Runpod pod.", _obj({"pod_id": S}, ["pod_id"]), infra.rp_pod)
register("rp_list_pods", "All Runpod pods of the account.", _obj({}), infra.rp_list_pods)
register("rp_exec", "Run a shell command inside a RUNNING pod (no SSH); waits at most 25 s; a longer command keeps "
         "running: start it with nohup … & and poll its output file.",
         _obj({"pod_id": S, "command": S, "timeout_s": I}, ["pod_id", "command"]), infra.rp_exec)
register("rp_upload", "Copy a workspace file or directory (without .git) into a RUNNING pod under dest (default "
         "/workspace): compressed, sent in chunks, its SHA-256 checked on the pod. Up to 2 MB compressed.",
         _obj({"pod_id": S, "path": S, "dest": S}, ["pod_id", "path"]),
         lambda ctx, pod_id, path, dest="/workspace": infra.rp_upload(ctx, pod_id, _path(ctx, path), dest))
register("rp_logs", "Last lines of a pod's container and system logs.", _obj({"pod_id": S, "tail": I}, ["pod_id"]), infra.rp_logs)
register("rp_delete_pod", "Delete a Runpod pod (stops billing). Only pods of this run unless not_created_by_this_run=true.",
         _obj({"pod_id": S, "not_created_by_this_run": B}, ["pod_id"]), infra.rp_delete_pod)
register("infra_resources", "Cloud resources created by this run (all agents), with state and hourly price.",
         _obj({}), infra.infra_resources)
register("infra_cleanup", "Delete every resource of this run that is still alive.", _obj({}), infra.infra_cleanup)
from .status import mission_status

register("mission_status", "Everything the race dashboard shows about this mission, for planning: LLM and cloud budgets "
         "(spent, in flight, cap, left), the rate of spend over the last 10 minutes and the hours until each cap at "
         "that rate (compared with the hours left before the deadline), tokens by model, sub-agents (model, effort, "
         "time, cost, status), machines (cost so far, committed until max_hours, last log lines), failed tool calls, "
         "sessions. This mission only.", _obj({}), mission_status)
register("budget_status", "Budgets and spend so far: LLM (USD, by model and mode, every agent of the run including "
         "metered harness traffic), Scaleway (EUR) and Runpod (USD) spent and committed by alive resources, elapsed "
         "time and hours left before the deadline.", _obj({}), infra.budget_status)

register("hf_whoami", "Hugging Face account of the token: user name, organisations, token role.", _obj({}), hf.hf_whoami)
register("hf_publish", "Create (if needed) a Hugging Face repository (model, dataset or static space) and upload a "
         "workspace folder to it. The folder must hold a card: README.md with YAML metadata (license for models and "
         "datasets) and a real description. Visibility is set by the mission. Recorded in the run's publications.",
         _obj({"repo_name": S, "folder": S, "repo_type": {"type": "string", "enum": ["model", "dataset", "space"]},
               "namespace": S, "commit_message": S, "space_sdk": S}, ["repo_name", "folder"]), hf.hf_publish)

register("sympy", "Symbolic algebra and calculus: run Python code with SymPy (simplify, solve, linear algebra, diff, "
         "integrate, limit, series, dsolve, exact and arbitrary-precision numbers) in a process of its own, killed with "
         "the processes it started after timeout_s (default 120, at least 1). Already defined, as in isympy: `from sympy "
         "import *`, symbols x y z t, integers k m n, functions f g h. Returns what the code prints, then the value of "
         "a last expression, with its LaTeX form when it is a SymPy object. `a.equals(b)` tests an identity: True, "
         "False, or None when SymPy cannot decide. A run may print, or write to one file, 10 MB at most.",
         _obj({"code": S, "timeout_s": I}, ["code"]), t_sympy)

def t_deliver(ctx, summary: str = "", force: bool = False) -> str:
    from .mission import deliver  # late import: mission builds on the agent and harness modules

    return deliver(ctx, summary, force)


register("deliver", "End the mission: checks DELIVERY.md, COMPLIANCE.md and DECISIONS.md, deletes the cloud resources "
         "still alive, writes COSTS.md from the ledgers, commits and tags the repository, and freezes clock and spend "
         "(delivery time is measured here). force=true delivers with missing files, recorded as missing.",
         _obj({"summary": S, "force": B}), t_deliver)

register("api_docs", "Catalog of the Scaleway and Runpod APIs, one compact signature per operation (like a Swagger "
         "index): action=list; action=search query='create gpu instance' [doc='scaleway/instance' or 'runpod/mcp']; "
         "action=show id='scaleway/instance:CreateServer' (params, body fields, return). Guides: id='scaleway/guide', "
         "'runpod/guide'.",
         _obj({"action": {"type": "string", "enum": ["list", "search", "show"]}, "query": S, "id": S, "doc": S}, ["action"]),
         t_api_docs)
register("scaleway", "Raw Scaleway API call (https://api.scaleway.com, auth handled). path like "
         "'/instance/v1/zones/{zone}/servers' ({zone}, {region}, {project_id} filled). raw_body: text body (with "
         "content_type). Non-GET calls need spending allowed.",
         _obj({"method": S, "path": S, "body": J, "query": J, "raw_body": S, "content_type": S}, ["method", "path"]), t_scaleway)
register("runpod_mcp", "Raw call of a Runpod MCP server tool (125 tools: pods, serverless endpoints and jobs, templates, "
         "volumes, billing). Signatures: api_docs doc='runpod/mcp'. Non-read-only tools need spending allowed.",
         _obj({"tool": S, "arguments": J}, ["tool"]), t_runpod_mcp)

BASE_TOOLS = ["shell", "read_file", "write_file", "edit_file", "http_request", "spawn_agent"]
INFRA_TOOLS = ["scw_offers", "scw_create_instance", "scw_instance", "scw_list_instances", "scw_delete_instance",
               "rp_offers", "rp_create_pod", "rp_pod", "rp_list_pods", "rp_exec", "rp_upload", "rp_logs", "rp_delete_pod",
               "infra_resources", "infra_cleanup", "api_docs", "scaleway", "runpod_mcp"]
GROUPS = {"base": BASE_TOOLS, "infra": INFRA_TOOLS, "budget": ["mission_status", "budget_status"], "hf": ["hf_whoami", "hf_publish"],
          "math": ["sympy"], "mission": ["deliver"]}


def all_tools() -> list[str]:
    """Every tool, in group order: what the main agent gets by default."""
    out: list[str] = []
    for names in GROUPS.values():
        out += [n for n in names if n not in out]
    return out
