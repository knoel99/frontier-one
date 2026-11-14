# Frontier

A provider-agnostic agent framework for the hackathon: an agent is a loop over a model **API** (no vendor
harness), it has a fixed toolbox, and it can start **sub-agents** that also run on an API, each with the model and
the reasoning level the parent chooses for the sub-task. Cloud compute (Scaleway, Runpod) is handled by one
dedicated sub-agent, `infra`, with ready-made functions so that no agent has to rediscover the APIs. The previous
race framework is kept, untouched, in [`old/`](old/).

```bash
python3 -m frontier plan                     # model and reasoning value of every provider × tier × level
python3 -m frontier ping [--provider p]      # tiny tool-calling request per model: checks ids, effort values, tools
python3 -m frontier run --provider openai --tier frontier --reasoning high --workspace ../milady-2027 "task…"
python3 -m frontier run --role infra --allow-spend --task-file task.md
python3 -m frontier tools                    # every tool, and which ones only the infra role holds
python3 -m frontier docs search create gpu instance      # the API catalog the infra agent reads
python3 -m frontier cleanup runs/<id>        # delete what a crashed run left alive
python3 -m pytest && python3 -m ruff check .              # offline tests, lint
```

Python ≥ 3.11, standard library only (PyYAML only to rebuild the catalog).

## Layout

| Path | What it holds |
|---|---|
| `config/providers.toml` | the only provider-specific part: per provider its API style, base URL, key variable, model per **tier** (`frontier`, `standard`, `light`) and value of its reasoning parameter per **level** (`none`, `low`, `medium`, `high`, `max`); limits of a run |
| `frontier/llm.py` | one `chat()` over four wire formats: `openai-chat` (Mistral, DeepSeek, xAI, Moonshot, Qwen, GLM, …), `openai-responses` (OpenAI), `gemini`, `anthropic`; tool calls normalised; reasoning state replayed between turns |
| `frontier/agent.py` | the agent loop, the run (token budget, sub-agent count and depth shared by the tree, cloud resource ledger, JSONL log) and the `spawn_agent` tool |
| `frontier/tools.py` | the toolbox. Every agent: `shell`, `read_file`, `write_file`, `edit_file`, `http_request`, `spawn_agent`. The `infra` role only: the ready-made cloud functions, `api_docs`, raw `scaleway` and `runpod_mcp` calls |
| `frontier/infra.py` | ready-made cloud functions, typed like MCP tools: `scw_offers`, `scw_create_instance`, `scw_instance`, `scw_list_instances`, `scw_delete_instance`, `rp_offers`, `rp_create_pod`, `rp_pod`, `rp_list_pods`, `rp_exec`, `rp_logs`, `rp_delete_pod`, `infra_resources`, `infra_cleanup` |
| `catalog/` | the API catalog, like a Swagger index: one compact signature per operation (params, body fields, return) for 311 Scaleway operations (13 APIs) and the 125 Runpod MCP tools, plus a short guide per provider; built by `tools/build_catalog.py` from the official specs (`catalog/SOURCES.json`) |
| `prompts/` | system prompt of every agent, the model-choice rules for sub-agents, the cloud rules of the infra agent |
| `roles/` | presets for `spawn_agent` / `--role`: `infra`, `worker`, `reviewer`, `scout` (tier, reasoning, tools, instructions) |
| `runs/<id>/` | git-ignored: `events.jsonl` (every model call with model, effort and tokens, every tool call), `resources.json` (cloud ledger), `answer.md`, `summary.json` |

## How an agent chooses a sub-agent

`spawn_agent(task, tier, reasoning, provider?, role?, tools?, max_turns?)`. The system prompt gives the rule
(mechanical → `light`/`low`, ordinary work → `standard`/`medium`, design or hard debugging → `frontier`/`high`,
decisive check → `frontier`/`high|max`); the parent may pick another provider for an independent check. A sub-agent
gets a fresh context and cannot spawn beyond `limits.max_depth`. Several `spawn_agent` calls in one turn run in
parallel. All agents of a run share `max_tokens_per_run` and `max_agents`.

## Infrastructure: one dedicated sub-agent

Agents do not rediscover the cloud APIs. Three layers, from the most used to the least:

1. **Ready-made functions** (`frontier/infra.py`) for the usual flows: real prices and stock (`scw_offers` reads the
   Scaleway Product Catalog and converts per-minute GPU prices; `rp_offers`), create a machine (image, cloud-init,
   power-on, wait), run a command in it (`rp_exec`), delete it with what it leaves behind (Scaleway Block volumes
   and IPs).
2. **The catalog** (`api_docs`): a compact signature per operation, for everything else.
3. **Raw calls** (`scaleway`, `runpod_mcp`), authentication handled.

Only the **`infra` role** holds these tools. Any agent asks it for compute with
`spawn_agent(role="infra", task="run X on a ≥ 24 GB GPU, budget …, bring back …")`; it prices, creates, runs,
collects and deletes. Spending stays in one place: every resource created is recorded in the run's ledger, the
functions refuse to delete what the run did not create, creation needs `--allow-spend`, and whatever is still
alive when the run ends is deleted (unless `--keep-resources`).

## Keys

Each provider reads its key from `key_env` (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `SCW_SECRET_KEY`,
`RUNPOD_API_KEY`, …). In a Claude Code cloud session whose network proxy injects the credentials, no variable is
needed: the framework sends the placeholder `proxy-injected` and the proxy replaces it.

## Checked on 2026-11-14 (from a Claude Code cloud session)

- `ping`: tool calls with a reasoning value work for every tier of openai, google, xai, mistral, deepseek,
  moonshot, alibaba and zhipu. anthropic is configured but not checked (no key in that session).
- Read-only cloud functions against the live APIs: `scw_offers` (cheapest ≥ 24 GB: L4-1-24G, 0.7875 €/h) and
  `rp_offers` (RTX 3090 secure, 0.50 $/h). Creation and deletion are tested against mocked APIs only.
- Facts the guides record: OpenAI needs the Responses API for tools with reasoning; Mistral Medium 3.5 and Small
  accept only `none`/`high`; Scaleway `hourly_price` is 0 and GPU prices are per minute in the Product Catalog;
  a Scaleway `terminate` leaves Block Storage volumes billing.
