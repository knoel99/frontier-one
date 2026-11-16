---
name: frontier
description: Frontier tools - sub-agents on any model API with a chosen tier, reasoning level and speed mode; Scaleway and Runpod GPU/CPU machines under a budget; budget and time status; Hugging Face publishing; symbolic algebra and calculus (SymPy); mission delivery. Use it when you need compute, another model, an exact computation, the spend so far, or to publish or deliver.
---

# Frontier tools

They are served by the `frontier` MCP server of this repository. When the harness has no MCP client, or the server
is not connected, call any of them from the shell with the same arguments:

```bash
frontier call <tool> '<json arguments>'          # bin/frontier of the Frontier repository; `frontier call list` lists them
```

- `scw_offers`: Scaleway Instance types of a zone with real price (EUR/hour, from the Product Catalog), GPU, memory and stock, cheapest first.
- `scw_create_instance`: Create a Scaleway Instance (GPU types get the Ubuntu GPU OS image with NVIDIA driver and Docker), optional cloud-init script, power on, wait until running; returns id, state, public IPs, price.
- `scw_instance`: State, type, public IPs and volumes of a Scaleway Instance.
- `scw_list_instances`: Scaleway Instances of a zone carrying a tag (default 'frontier').
- `scw_delete_instance`: Delete a Scaleway Instance AND its Block Storage volumes and flexible IPs (a plain terminate leaves the volumes billing).
- `rp_offers`: Runpod GPU types for pods with USD/hour per GPU, memory, stock and data centers, cheapest first.
- `rp_create_pod`: Create a Runpod pod (billed per second from creation).
- `rp_pod`: Details and state of a Runpod pod.
- `rp_list_pods`: All Runpod pods of the account.
- `rp_exec`: Run a shell command inside a RUNNING pod (no SSH); waits at most 25 s; a longer command keeps running: start it with nohup … & and poll its output file.
- `rp_upload`: Copy a workspace file or directory (without .git) into a RUNNING pod under dest (default /workspace): compressed, sent in chunks, its SHA-256 checked on the pod.
- `rp_logs`: Last lines of a pod's container and system logs.
- `rp_delete_pod`: Delete a Runpod pod (stops billing).
- `infra_resources`: Cloud resources created by this run (all agents), with state and hourly price.
- `infra_cleanup`: Delete every resource of this run that is still alive.
- `mission_status`: Everything the race dashboard shows about this mission, for planning: LLM and cloud budgets (spent, in flight, cap, left), the rate of spend over the last 10 minutes and the hours until each cap at that rate (compared with the hours left before the deadline), tokens by model, sub-agents (model, effort, time, cost, status), machines (cost so far, committed until max_hours, last log lines), failed tool calls, sessions.
- `budget_status`: Budgets and spend so far: LLM (USD, by model and mode, every agent of the run including metered harness traffic), Scaleway (EUR) and Runpod (USD) spent and committed by alive resources, elapsed time and hours left before the deadline.
- `hf_whoami`: Hugging Face account of the token: user name, organisations, token role.
- `hf_publish`: Create (if needed) a Hugging Face repository (model, dataset or static space) and upload a workspace folder to it.
- `sympy`: Symbolic algebra and calculus: run Python code with SymPy (simplify, solve, linear algebra, diff, integrate, limit, series, dsolve, exact and arbitrary-precision numbers) in a process of its own, killed after timeout_s (default 120).
- `deliver`: End the mission: checks DELIVERY.md, COMPLIANCE.md and DECISIONS.md, deletes the cloud resources still alive, writes COSTS.md from the ledgers, commits and tags the repository, and freezes clock and spend (delivery time is measured here).
- `api_docs`: Catalog of the Scaleway and Runpod APIs, one compact signature per operation (like a Swagger index): action=list; action=search query='create gpu instance' [doc='scaleway/instance' or 'runpod/mcp']; action=show id='scaleway/instance:CreateServer' (params, body fields, return).
- `scaleway`: Raw Scaleway API call (https://api.scaleway.com, auth handled).
- `runpod_mcp`: Raw call of a Runpod MCP server tool (125 tools: pods, serverless endpoints and jobs, templates, volumes, billing).
- `spawn_agent`: Start a sub-agent on a model API with its own context, provider, model tier, reasoning level and speed mode; give it a self-contained task and get its final answer.

Prices, models, speed modes, compute offers and budgets of the current mission: TOOLS.md at the workspace root.
