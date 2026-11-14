# Frontier

A provider-agnostic agent framework for the hackathon: an agent is a loop over a model **API** (no vendor
harness), it has a fixed toolbox, and it can start **sub-agents** that also run on an API, each with the model and
the reasoning level the parent chooses for the sub-task. The previous race framework is kept, untouched, in
[`old/`](old/).

```bash
python3 -m frontier plan                     # model and reasoning value of every provider × tier × level
python3 -m frontier ping [--provider p]      # tiny tool-calling request per model: checks ids, effort values, tools
python3 -m frontier run --provider openai --tier frontier --reasoning high --workspace ../milady-2027 "task…"
python3 -m frontier run --role cloud-ops --allow-spend --task-file task.md
python3 -m frontier docs search create gpu instance      # the API docs the agents read
python3 -m pytest && python3 -m ruff check .              # offline tests, lint
```

Python ≥ 3.11, standard library only (PyYAML only to refresh the docs).

## Layout

| Path | What it holds |
|---|---|
| `config/providers.toml` | the only provider-specific part: per provider its API style, base URL, key variable, model per **tier** (`frontier`, `standard`, `light`) and value of its reasoning parameter per **level** (`none`, `low`, `medium`, `high`, `max`); limits of a run |
| `frontier/llm.py` | one `chat()` over four wire formats: `openai-chat` (Mistral, DeepSeek, xAI, Moonshot, Qwen, GLM, …), `openai-responses` (OpenAI), `gemini`, `anthropic`; tool calls normalised; reasoning state replayed between turns |
| `frontier/agent.py` | the agent loop, the run (token budget, sub-agent count and depth shared by the tree, JSONL log) and the `spawn_agent` tool |
| `frontier/tools.py` | the toolbox: `shell`, `read_file`, `write_file`, `edit_file`, `http_request`, `api_docs`, `scaleway`, `runpod`, `runpod_mcp`, `spawn_agent` |
| `frontier/apidocs.py` | offline search over `apidocs/` |
| `apidocs/` | official API docs: 13 Scaleway OpenAPI specs (Instance, Block, Marketplace, Billing, IAM, VPC, IPAM, Secret Manager, Registry, Serverless Jobs, Inference, Generative APIs, File Storage), Runpod REST v1 OpenAPI, the 125 Runpod MCP tools with their schemas, doc pages, and a hand-written `GUIDE.md` per provider (auth, flows, pitfalls); refreshed by `tools/fetch_api_docs.py` (sources and dates in `apidocs/SOURCES.json`) |
| `prompts/` | system prompt of every agent and the model-choice rules for sub-agents |
| `roles/` | optional presets for `spawn_agent` / `--role`: `worker`, `reviewer`, `cloud-ops`, `scout` (tier, reasoning, tools, instructions) |
| `runs/<id>/` | git-ignored: `events.jsonl` (every model call with model, effort and tokens, every tool call), `answer.md`, `summary.json` |

## How an agent chooses a sub-agent

`spawn_agent(task, tier, reasoning, provider?, role?, tools?, max_turns?)`. The system prompt gives the rule
(mechanical → `light`/`low`, ordinary work → `standard`/`medium`, design or hard debugging → `frontier`/`high`,
decisive check → `frontier`/`high|max`); the parent may pick another provider for an independent check. A sub-agent
gets a fresh context, only a subset of its parent's tools, and cannot spawn beyond `limits.max_depth`. Several
`spawn_agent` calls in one turn run in parallel. All agents of a run share `max_tokens_per_run` and `max_agents`.

## Cloud tools and spending

`api_docs` (list / search / show) returns an operation with its parameters and its request and response schemas
resolved, so agents never look for documentation on the web. `scaleway` and `runpod` call the REST APIs with
authentication; `runpod_mcp` calls the Runpod MCP server. Every call that creates, changes or deletes a resource
is refused unless the run is started with `--allow-spend`.

## Keys

Each provider reads its key from `key_env` (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `SCW_SECRET_KEY`,
`RUNPOD_API_KEY`, …). In a Claude Code cloud session whose network proxy injects the credentials, no variable is
needed: the framework sends the placeholder `proxy-injected` and the proxy replaces it.

## Checked on 2026-11-14 (`ping`, from a Claude Code cloud session)

Tool calls with a reasoning value work for every tier of openai, google, xai, mistral, deepseek, moonshot, alibaba
and zhipu. anthropic is configured but not checked (no key in that session). End-to-end: a `gpt-6.1-sol`/medium
agent priced the cheapest ≥ 24 GB GPU on Scaleway itself (bundled docs → Instance and Product Catalog APIs) and
delegated the Runpod side to a `glm-5.3-flash`/low sub-agent (docs → `runpod_mcp`), 127 k tokens in total.
Known facts the guides record: OpenAI needs the Responses API for tools with reasoning; Mistral Medium 3.5 and
Small accept only `none`/`high`; Scaleway `hourly_price` is 0 and GPU prices are per minute in the Product Catalog;
`rest.runpod.io` needs `RUNPOD_API_KEY` while `mcp.runpod.io` is proxy-authenticated.
