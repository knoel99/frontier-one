# Runpod: quick guide for agents

Written for this framework on 2026-11-14 and checked with read-only calls that day. The ready-made functions
(`rp_offers`, `rp_create_pod`, `rp_pod`, `rp_list_pods`, `rp_exec`, `rp_upload`, `rp_logs`, `rp_delete_pod`) implement the flows
below; for anything else call a tool of the Runpod MCP server with `runpod_mcp` (signatures: `api_docs doc=runpod/mcp`).

## Access

- Everything goes through the Runpod MCP server `https://mcp.runpod.io/` (JSON-RPC `tools/call`; 125 tools covering
  the REST v2 surface: GPU catalogue and stock, pods, serverless endpoints and jobs, templates, volumes, billing, Hub).
- Auth: `Authorization: Bearer <RUNPOD_API_KEY>`, or injected by the network proxy in a Claude Code cloud session.
- Tools whose catalog entry has `read_only: true` are free; every other tool needs `--allow-spend`.
- Some tools list their schema "on demand": the catalog already holds the full schema (from `describe-tool`).

## A GPU pod: price, create, use, delete

1. Price and stock: `list-gpu-types` with `include=["AVAILABILITY"]`, `product=["POD"]`, `cloud` gives per GPU `id`
   (e.g. `"NVIDIA A40"`), `memory` (GB), `price.secure` / `price.community` ($/hour per GPU), `availability`
   (`LOW`, `MEDIUM`, `HIGH`, ...), `dataCenters`. `get-capacity` gives stock per CUDA version.
2. Create: `create-pod` with `body = {name, image, gpu: {id, count}, cloud, disk (GB), ports ["22/tcp"], env, args}`.
   Billed per second from creation (GPU and disk).
3. Use: `exec-pod` runs an argv command inside the running container (at most 25 s per call; start long jobs with
   `nohup … &` and poll); `stream-pod-logs` reads the logs; SSH needs `startSsh` and `22/tcp`.
4. Stop or delete: `pod-action` `stop` keeps the disk (still billed); `delete-pod` ends all billing.

## Serverless

An endpoint runs a worker image that scales from zero; you pay for worker seconds. `run-endpoint` submits a job
(returns an id), `get-job-status` polls it (with a server-side wait), `runsync-endpoint` for short jobs on a warm
endpoint. Expect minutes of cold start for the first job.
