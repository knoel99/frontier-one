"""Tools an agent can be given: a JSON-schema function spec plus a Python function fn(ctx, **arguments) -> str.

Two sets:
  BASE_TOOLS   every agent: workspace, HTTP, spawn_agent (registered in agent.py)
  INFRA_TOOLS  the `infra` role: ready-made Scaleway/Runpod functions, the API catalog, raw API calls
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from . import catalog, hf, infra
from .http import mcp_call, request, scw_raw


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


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


# ---------------------------------------------------------------- workspace and HTTP


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


def t_http_request(ctx, method: str, url: str, headers: dict | None = None, body=None) -> str:
    code, text = request(method, url, headers, body)
    return f"HTTP {code}\n{text}"


# ---------------------------------------------------------------- catalog and raw API calls


def t_api_docs(ctx, action: str, query: str = "", id: str = "", doc: str = "") -> str:  # noqa: A002
    if action == "list":
        return catalog.overview()
    if action == "search":
        return catalog.search(query, doc)
    if action == "show":
        return catalog.show(id or query)
    return "action must be list, search or show"


def t_scaleway(ctx, method: str, path: str, body: dict | None = None, query: dict | None = None, raw_body: str = "",
               content_type: str = "") -> str:
    if method.upper() != "GET" and (g := infra._need_spend(ctx, f"{method} {path}")):
        return g
    code, text = scw_raw(method, path, raw_body or body, query, content_type)
    return f"HTTP {code}\n{text}"


def t_runpod_mcp(ctx, tool: str, arguments: dict | None = None) -> str:
    if tool not in catalog.read_only_mcp_tools() and tool != "describe-tool" and (g := infra._need_spend(ctx, f"Runpod MCP tool {tool}")):
        return g
    ok, text = mcp_call(tool, arguments)
    return text if ok else "ERROR: " + text


# ---------------------------------------------------------------- registry

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

# Ready-made infrastructure functions: prefer them to raw calls.
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
register("rp_logs", "Last lines of a pod's container and system logs.", _obj({"pod_id": S, "tail": I}, ["pod_id"]), infra.rp_logs)
register("rp_delete_pod", "Delete a Runpod pod (stops billing). Only pods of this run unless not_created_by_this_run=true.",
         _obj({"pod_id": S, "not_created_by_this_run": B}, ["pod_id"]), infra.rp_delete_pod)
register("infra_resources", "Cloud resources created by this run (all agents), with state and hourly price.",
         _obj({}), infra.infra_resources)
register("infra_cleanup", "Delete every resource of this run that is still alive.", _obj({}), infra.infra_cleanup)
from .status import mission_status  # noqa: E402

register("mission_status", "Everything the race dashboard shows about this mission, for planning: LLM and cloud budgets "
         "(spent, in flight, cap, left), the rate of spend over the last 10 minutes and the hours until each cap at "
         "that rate (compared with the hours left before the deadline), tokens by model, sub-agents (model, effort, "
         "time, cost, status), machines (cost so far, committed until max_hours, last log lines), failed tool calls, "
         "sessions. This mission only.", _obj({}), mission_status)
register("budget_status", "Budgets and spend so far: LLM (USD, by model and mode, every agent of the run including "
         "metered harness traffic), Scaleway (EUR) and Runpod (USD) spent and committed by alive resources, elapsed "
         "time and hours left before the deadline.", _obj({}), infra.budget_status)

# Hugging Face Hub
register("hf_whoami", "Hugging Face account of the token: user name, organisations, token role.", _obj({}), hf.hf_whoami)
register("hf_publish", "Create (if needed) a Hugging Face repository (model, dataset or static space) and upload a "
         "workspace folder to it. The folder must hold a card: README.md with YAML metadata (license for models and "
         "datasets) and a real description. Visibility is set by the mission. Recorded in the run's publications.",
         _obj({"repo_name": S, "folder": S, "repo_type": {"type": "string", "enum": ["model", "dataset", "space"]},
               "namespace": S, "commit_message": S, "space_sdk": S}, ["repo_name", "folder"]), hf.hf_publish)

# Mission
def t_deliver(ctx, summary: str = "", force: bool = False) -> str:
    from .mission import deliver  # late import: mission builds on the agent and harness modules

    return deliver(ctx, summary, force)


register("deliver", "End the mission: checks DELIVERY.md, COMPLIANCE.md and DECISIONS.md, deletes the cloud resources "
         "still alive, writes COSTS.md from the ledgers, commits and tags the repository, and freezes clock and spend "
         "(delivery time is measured here). force=true delivers with missing files, recorded as missing.",
         _obj({"summary": S, "force": B}), t_deliver)

# Catalog and raw calls, for anything the functions above do not cover.
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
               "rp_offers", "rp_create_pod", "rp_pod", "rp_list_pods", "rp_exec", "rp_logs", "rp_delete_pod",
               "infra_resources", "infra_cleanup", "api_docs", "scaleway", "runpod_mcp"]
GROUPS = {"base": BASE_TOOLS, "infra": INFRA_TOOLS, "budget": ["mission_status", "budget_status"], "hf": ["hf_whoami", "hf_publish"],
          "mission": ["deliver"]}


def all_tools() -> list[str]:
    """Every tool, in group order: what the main agent gets by default."""
    out: list[str] = []
    for names in GROUPS.values():
        out += [n for n in names if n not in out]
    return out


DEFAULT_TOOLS = BASE_TOOLS  # kept for callers that want the workspace tools only
