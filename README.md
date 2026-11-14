# Frontier

A framework that hands a research mission to an AI and lets it run it end to end. The owner gives:

- **a harness**: the AI provider's own coding agent (Claude Code, Codex CLI, Antigravity, Grok Build, Kimi Code,
  Qwen Code, Mistral Vibe, DeepSeek dsh), or a community one (OpenCode by default, Hermes, Goose, Crush, …);
- **an LLM API key with a budget**;
- **Scaleway and Runpod API keys with budgets**, and optionally a **Hugging Face token**;
- **the subject** (here an M2 internship subject) and a deadline.

The AI decides its own strategy: models, reasoning levels, speed modes, sub-agents, machines, scope. The framework
gives the main thread a complete inventory of its tools (`TOOLS.md`), enforces the budgets, measures cost and time,
and checks the deliverables (report, code, models or data on the Hub, a compliance matrix of the subject's
instructions, a decision log). Evaluation: scientific quality, cost control, delivery time, compliance with the
subject (departures justified). The previous race framework is in git history: [`old/` at 7223bd6](https://github.com/knoel99/frontier-one/tree/7223bd6f39eaddce278457a0d0d32f3598f600b3/old).

A race is N such missions run in parallel, one per provider and harness, on the same subject with the same caps:

![A Frontier One race](docs/diagrams/race.png)

Interactive version: [`docs/diagrams/race.html`](docs/diagrams/race.html) (download and open it in a browser; every
box links to the code it describes at commit `7100fc2`). Source `docs/diagrams/race.archify.json`, rendered with
[archify](https://github.com/tt-a1i/archify) 3.2 at commit `7e45f36`:
`node <archify>/bin/archify.mjs finalize workflow docs/diagrams/race.archify.json docs/diagrams/race.html --repo-root . --quality showcase`.

How a race runs, step by step (one team; every box links to its code at commit `6d76c7e`):

1. [Run order](docs/diagrams/run-order.html) ([PNG](docs/diagrams/run-order.png)): the owner's one command, `mission.prepare`,
   the gateway and watchdog threads, the session loop and its three stop conditions, delivery or cleanup.
2. [Processes](docs/diagrams/processes.html) ([PNG](docs/diagrams/processes.png)): the processes alive during a race,
   the code each one executes, and the workspace, state and Frontier files each one reads or writes.
3. [One session](docs/diagrams/session.html) ([PNG](docs/diagrams/session.png)): the harness reads its instructions and
   starts the MCP server; then one model call through the gateway, `scw_create_instance`, `spawn_agent`, `deliver`,
   and the end of the session.

Render each with `node <archify>/bin/archify.mjs finalize <workflow|architecture|sequence> docs/diagrams/<name>.archify.json docs/diagrams/<name>.html --repo-root . --quality showcase`.

```bash
cp config/mission.example.toml my-mission.toml          # fill in: harness, budgets, subject, [secrets] sources
python3 -m frontier mission start my-mission.toml --prepare-only   # write the workspace, check the key
python3 -m frontier mission start my-mission.toml       # launch the harness headless until delivery
python3 -m frontier mission status my-mission.toml      # spend, commitments, time left
frontier reap ~/.frontier/missions/<name>               # dead-man sweep, from cron on the host
```

Other commands: `plan` (models and reasoning values per provider), `ping` (check ids and efforts with tiny calls),
`run` (an API agent on a task, without a harness), `tools`, `docs search …` (the cloud API catalog), `harnesses`,
`call <tool> '<json>'` (any tool from the shell). Python ≥ 3.11, standard library only (`huggingface_hub` for
publishing; PyYAML to rebuild the catalog). Tests: `python3 -m pytest`, `ruff check .`,
`python3 tools/sync_harnesses.py --check`.

## What the AI gets

| | |
|---|---|
| `MISSION.md` | the brief (`prompts/mission.md`): the task, the evaluation, the budgets and deadline, the deliverables, the rules; it states that the strategy is the AI's |
| `TOOLS.md` | generated inventory: the harness and its model, every Frontier tool, every model usable through `spawn_agent` with its price per million tokens, reasoning values and speed modes, a snapshot of GPU offers with price and stock on Scaleway and Runpod, the billing facts, the budgets |
| the `frontier` MCP server | `spawn_agent` (a sub-agent on any model API of the mission's providers, with tier, reasoning level, speed mode, its own instructions and tools), the cloud functions (`scw_*`, `rp_*`: offers with real prices, create with `max_hours`, exec, logs, delete with leftovers), `budget_status`, `hf_publish`, `deliver`, the API catalog and raw calls |
| the harness's own files | instructions, MCP config, sub-agents and skills in the format of the chosen harness |

The presets in `roles/` are only two: `infra` (a cloud operator) and `reviewer` (tries to refute a result); the AI
can define any other sub-agent with `spawn_agent(instructions=…)`.

## Budgets, cost and time

- **LLM**: every model call of the mission is priced from `config/prices.toml` (list prices, refreshed by
  `tools/update_prices.py` from OpenRouter's public list) and written to a hash-chained ledger. Sub-agents are charged
  directly; the harness's own traffic goes through a local pass-through **metering gateway** (`frontier/gateway.py`)
  where the harness lets its base URL be set (`config/harnesses.toml`), otherwise it is read from the harness's usage
  report after each session. The cap is hard: each call reserves its worst case before it starts.
- **Cloud**: every machine is created with a `max_hours`; the budget reserves price × max_hours; spend is price ×
  lifetime from the ledger; a watchdog deletes what overruns; leftovers are deleted at the end.
- **Time**: measured from the start of the mission to `deliver`, which freezes clock and spend and writes `COSTS.md`.
- **Second line**: set provider-side caps too (OpenAI project hard limit, Anthropic workspace limit, OpenRouter key
  limit, a prepaid Runpod balance with auto-pay off); Scaleway only has alerts.

The mission's state lives outside the AI's workspace (`~/.frontier/missions/<name>/`) and is written by the
framework only.

## Keys, from anywhere

No key is typed by hand and none is written down. What each key opens is declared in `config/providers.toml` (a
model provider's `key_env` and `base_url`; a service's `env`, `hosts`, `header` and non-secret `settings` under
`[services.*]`), so a new provider or cloud is a config entry. Where each value comes from is declared by the mission's
`[secrets]`: `env:NAME`, `file:PATH` (Docker, Kubernetes or systemd secrets), `cmd:COMMAND` (any secret manager's CLI)
or `proxy` (an outer proxy injects it, as in a Claude Code cloud session). The launcher resolves them once, on the host,
under their standard names (`frontier/credentials.py`); `mission.json` records only where each came from. Checked live
on 2026-11-14 with every key left to the session's proxy: key checks, `spawn_agent` and gateway-metered calls on
openai, google, deepseek and zhipu (12 calls, all priced exactly, ledgers intact, no key on disk).

## Sandbox and network lists (NVIDIA OpenShell)

With `[sandbox] kind = "openshell"` in the mission file, each team's harness runs in an
[OpenShell](https://docs.nvidia.com/openshell/) sandbox (`frontier/sandbox.py`, written from the 0.1.8 docs):

- **Keys never enter the sandbox.** Each key the mission uses becomes an OpenShell provider
  backed by a profile of ours; inside, its variable holds a placeholder that OpenShell's proxy replaces with the real key
  only on the way to that service's hosts, in that service's header.
- **Network denied by default.** Allowed: the providers' hosts, package registries, Hugging Face storage, and the
  mission's `[network] allow` list (read-only, L7-enforced); `[network] deny` removes hosts from it (a wildcard that
  covers a denied host is dropped, with a warning). Landlock (required, not best effort) and seccomp confine files and
  processes; the workload runs as the `sandbox` user.
- **Flow.** The host launcher imports the profiles, creates the providers and the sandbox (image built from a generated
  Dockerfile with the harness and this repository), uploads the workspace and the state, runs each session with
  `openshell sandbox exec … frontier mission session`, downloads workspace and ledgers after each session (the host's
  dashboard, watchdog and commits read them), and deletes sandbox and providers at the end.
  `mission start <file> --dry-run` prints every command without running it.
- **To check on the race host before a real mission** (no Docker daemon in the session that wrote it): one short
  session; that loopback traffic from the harness to the metering gateway inside the sandbox is allowed; the
  `binaries` globs against the image's real paths (add a harness's native binary with `sandbox_binaries` in
  `config/harnesses.toml`).

## Layout

| Path | What it holds |
|---|---|
| `frontier/` | `llm.py` (one chat API over OpenAI Responses, Chat Completions, Gemini, Anthropic), `agent.py` (agent loop, runs, `spawn_agent`), `tools.py`, `infra.py` (cloud functions, watchdog), `budget.py`, `gateway.py`, `mcp.py` (stdio MCP server), `mission.py` and `launcher.py`, `harnesses.py` (files per harness), `catalog.py`, `hf.py` |
| `config/` | `providers.toml` (models per tier, reasoning values, speed modes, limits), `prices.toml`, `harnesses.toml` (launch recipes), `mission.example.toml` |
| `catalog/` | the Scaleway and Runpod API catalog (one signature per operation) and a guide per provider |
| `prompts/`, `roles/` | the brief, the system prompts, the two role presets |
| `docs/` | `HARNESSES.md` (what each harness reads and gets), `STANDARDS.md` (industry standards and where Frontier stands), `diagrams/race.*` (a race, archify source, HTML and PNG) |
| `bin/` | `frontier`, `frontier-mcp`: run from anywhere |
| harness files | `CLAUDE.md`, `.mcp.json`, `.codex/`, `.agents/`, `.claude/`, `.gemini/`, `.grok/`, `.kimi-code/`, `.qwen/`, `.vibe/`, `.dsh/`, `.github/`, `.cursor/`, `opencode.json`, `.opencode/`, `.crushrc`, `.kilo/`, `.devin/`, `.amp/`, `.continue/`, `.zed/`, `.clinerules/`, `.aider.conf.yml`: generated by `tools/sync_harnesses.py`, so this repository works in any of the 23 harnesses |

## Checked on 2026-11-14

- `ping`: tool calls with a reasoning value work for every tier of openai, google, xai, mistral, deepseek,
  moonshot, alibaba, zhipu; anthropic is configured, not checked (no key in that session).
- The gateway meters OpenAI Responses, Chat Completions (GLM) and Gemini, streamed and not, and answers 402 at the cap.
- Real missions with Codex CLI 0.169 on `gpt-6-luna`: the AI read `MISSION.md` and `TOOLS.md`, did the task, wrote
  the deliverables and called `budget_status` (through the MCP server) and `deliver`, in 30 to 40 s; its own model
  calls went through the gateway (6 calls, $0.004) and the launcher committed and tagged the workspace.
- Not yet run for real: other harnesses' recipes (from their docs, see docs/HARNESSES.md), creating and deleting
  cloud machines (tested against mocked APIs), Hugging Face uploads.

## Speed and price modes

`spawn_agent(mode=…)` and the inventory list the documented ones (config/providers.toml, read 2026-11-14): OpenAI
`flex` (×0.5, slower), `fast` (×2, up to 2.5× faster), `ultrafast` (×6); Anthropic `fast` (×2, Fable 5.2); Gemini
`flex` (×0.5) and `priority` (×1.8); xAI `priority` (×2); Kimi `highspeed` and GLM `flashx` (faster sibling models).
DeepSeek is half price outside its peak hours, which the budget applies by itself. The gateway bills the tier the
response says was served (a request for a fast tier can be served at standard).
