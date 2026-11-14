# Runpod: quick guide for agents

Written for this framework on 2026-11-14 and checked with read-only calls that day. Full references:
`runpod/rest-v1` (OpenAPI of rest.runpod.io/v1), `runpod/mcp` (the 125 tools of the Runpod MCP server, REST v2
surface, with input schemas) and `runpod/pages` (official doc pages).

## Three ways in

| Tool | Endpoint | Auth | Use for |
|---|---|---|---|
| `runpod_mcp` | `https://mcp.runpod.io/` (JSON-RPC `tools/call`) | `Authorization: Bearer <RUNPOD_API_KEY>`, or injected by the network proxy | everything: GPU stock and prices (`list-gpu-types`, `get-capacity`), pods (`create-pod`, `get-pod`, `pod-action`, `delete-pod`), endpoints and jobs, billing (`list-pod-billing`), guides (`list-guides`, `read-guide`) |
| `runpod` | `https://rest.runpod.io/v1` | `Authorization: Bearer <RUNPOD_API_KEY>` | REST v1: `/pods`, `/endpoints`, `/templates`, `/networkvolumes`, `/billing/*` |
| `runpod` with a full URL | `https://api.runpod.ai/v2/{endpoint_id}/...` | same key | serverless jobs: `run`, `runsync`, `status/{job_id}`, `stream/{job_id}`, `cancel/{job_id}`, `retry/{job_id}`, `purge-queue`, `health` (page `runpod/page:serverless__endpoints__operation-reference`) |

Prefer `runpod_mcp` when no `RUNPOD_API_KEY` is set: in a Claude Code cloud session the proxy authenticates
`mcp.runpod.io` but not `rest.runpod.io`.

## A GPU pod: price, create, use, delete

1. Price and stock: `runpod_mcp list-gpu-types` returns per GPU `id` (e.g. `"NVIDIA GeForce RTX 4090"`),
   `memory` (GB), `price.secure` / `price.community` ($/hour) and `maxCount`; `get-capacity` gives stock by CUDA version.
2. Create: `runpod_mcp create-pod` (read its schema with `api_docs action=show id=runpod/mcp:create-pod`) or
   `runpod POST /pods` with e.g.
   `{"name": "frontier-<run>", "imageName": "runpod/pytorch:...", "gpuTypeIds": ["NVIDIA GeForce RTX 4090"], "gpuCount": 1,
   "cloudType": "SECURE", "containerDiskInGb": 20, "volumeInGb": 0, "ports": ["22/tcp"], "env": {...}}`.
   A pod bills from creation, per second, GPU price plus disk.
3. Run work: pods have no exec API in REST v1; pass the work as `dockerStartCmd` (or `dockerEntrypoint`), or use
   the MCP tool `exec-pod`, or SSH (`ports` `22/tcp`, public key in the account settings).
   Logs: `runpod_mcp stream-pod-logs`.
4. Stop or delete: `pod-action` (`stop` keeps the volume disk, which still bills) or `delete-pod`
   (`DELETE /pods/{podId}`): delete what you created as soon as its result is saved.

## Serverless

An endpoint runs a worker image that scales from zero; you pay for worker seconds. Submit a job with `run`
(asynchronous, returns `id`) then poll `status/{id}`, or `runsync` for short jobs. Expect minutes of cold start for
the first job. Read `runpod/page:serverless__pricing` and `runpod/page:serverless__endpoints__send-requests`.
