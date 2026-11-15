# Test plan: is Frontier usable, and does every feature work?

Status: run on 2026-11-14, and again on 2026-11-15 with the features F25 to F31 added since; results in [TEST-RESULTS.md](TEST-RESULTS.md). Method: pstack's verification approach. Every feature has a way a user reaches
it, a way an agent drives it, and an **observable state that proves it works**. Each step ends with a check of the
real artifact; "the build passed" is not evidence. Each feature gets a verdict, **PASS**, **ISSUES** or **BLOCKED**,
with its evidence. A fresh agent then re-checks the verdicts against the evidence. A green run is ready for a
decision, not approved.

## Scope and budget

- Subject of the live mission: **sort what is useful and what is not in `frontier-one`**. The AI team audits this
  repository, and the run itself measures which code a real race executes.
- Keys: the real keys of this cloud session, which its proxy injects (`[secrets]` set to `proxy`). The race host is
  this session; the runners (Runpod pods) get no LLM key.
- Harness: Codex CLI 0.169 on OpenAI. It is the only harness run live, the simplest path; the other 23 are covered by
  the file checks of level 0 only.
- Budget for the whole plan: **2 € of LLM** (`llm_usd = 2.0`, which is under 2 €) and **2 € of simulation**
  (`runpod_usd = 2.0`, plus at most 0.20 € of Scaleway at level 1). Deadline: 2 h for the mission.

## Levels (each one must pass before the next)

| Level | What | Cost | Proof |
|---|---|---|---|
| 0 | Static: `ruff`, `pytest`, `sync_harnesses --check` | 0 | exit codes |
| 1 | Feature checks: one scripted live check per feature, on real services, with tiny budgets (`tools/verify_live.py`) | ≤ 0.20 € LLM, ≤ 0.40 € compute | the observable state of each feature (table below) |
| 2 | Adversarial checks: caps, deadline, watchdog, tampering, key leaks | ≤ 0.10 € | refusals, kills and detections observed in the ledgers |
| 3 | Live mission: Codex audits `frontier-one` under the remaining budget, measured under `coverage` | the rest, ≤ 2 € + 2 € in total | deliverables, ledgers, dashboard, coverage report, usability metrics |
| 4 | Fresh-agent verification of every verdict, then cleanup audit | ≤ 0.10 € LLM | PASS/ISSUES/BLOCKED per feature, no resource left |

## Feature map

| # | Feature | User reaches it by | Agent drives it by | Observable proof | Level |
|---|---|---|---|---|---|
| F1 | Mission preparation | `frontier mission start m.toml --prepare-only` | the launcher | workspace git repo with `AGENTS.md`, `MISSION.md`, `TOOLS.md`, `subject/`, `.codex/config.toml`; `budgets.json` with the caps | 1 |
| F2 | Credentials from declared sources | `[secrets]`: `proxy`, `env:`, `file:` and `cmd:` | `credentials.load` | origins in `mission.json`; no value in any file of the state or the workspace | 1, 2 |
| F3 | Key check | preparation with ping | `_ping` | "key checked" for each provider in `TOOLS.md`; `launcher` entries in `llm.jsonl` | 1 |
| F4 | Session loop and headless harness | `mission start` | `run_one`, `run_until` | `sessions/NN.log`; `session_start` and `session_end` events; one commit per session | 3 |
| F5 | Gateway metering | the harness routed to the gateway | `gateway.py` | `gateway` entries in `llm.jsonl` that match the harness's own report | 1, 3 |
| F6 | LLM hard cap | `budget_usd` | reservations, 402 | a mission with `budget_usd = 0.01`: a 402 at the gateway, then the stop reason "LLM budget" | 2 |
| F7 | Deadline | `duration_hours` | `run_until`, `_stop_reason` | a mission of 3 minutes: the session is killed with code 124, `stopped: deadline`, no child process left | 2 |
| F8 | MCP tools served to the harness | `.codex/config.toml` | `bin/frontier-mcp` | `mcp_call` events for `budget_status`, `spawn_agent`, `rp_*` and `deliver` | 3 |
| F9 | Shell fallback | `frontier call <tool>` | — | the same tool output as through MCP | 1 |
| F10 | Sub-agents on any provider | `spawn_agent` (provider, tier, reasoning, role) | `agent.py`, `llm.py` | one sub-agent each on openai, google, deepseek and zhipu; roles `infra` and `reviewer`; `spawn_agent` entries priced in `llm.jsonl` | 1, 3 |
| F11 | Speed modes | `spawn_agent(mode="flex")` | `requested_price_x`, `charge_llm` | an entry with `frontier.mode = flex` and price × 0.5 | 1 |
| F12 | Runpod runner | `rp_offers`, `rp_create_pod`, `rp_exec`, `rp_logs`, `rp_delete_pod` | `infra.py` | a CPU pod created and priced, a command's output returned, logs read, the pod deleted (`get-pod` 404) | 1, 3 |
| F13 | Scaleway runner | `scw_offers`, `scw_create_instance` with cloud-init, `scw_delete_instance` | `infra.py` | VM created at Amsterdam (the Paris quotas are 0); result read back through its metadata; VM, volume and IP gone | 1 |
| F14 | Cloud budget cap | `budget_eur`, `budget_usd` | `cloud_allowed`, `cloud_lock` | a creation over the cap is refused, and so are two parallel creations that would together go over it | 2 |
| F15 | Watchdog and reap | `max_hours` | `infra.enforce`, `frontier reap` | a pod with `max_hours = 0.05` deleted by the watchdog; `watchdog_delete` event | 2 |
| F16 | Hugging Face publishing | `hf_publish` | `hf.py` | the card check refuses a bad card; a private test repo is created and uploaded, then deleted by the cleanup | 1 |
| F17 | Delivery | `deliver` | `mission.deliver` | `delivery.json`, `COSTS.md`, frozen spend, git tag `delivery`, no resource left | 3 |
| F18 | Ledger integrity | — | `verify_chain` | `ledgers_intact` true; one edited line in a copy is detected | 2 |
| F19 | Dashboard | `frontier dashboard` | `dashboard.py` | live page in the app's preview pane; a snapshot (`--once`) published as a private claude.ai page; spend, machines, sub-agents, Gantt and actions match the ledgers | 3 |
| F20 | Runner activity | ledgers + runner logs | `rp_logs` | each runner's commands, output, duration and cost can be recovered after the run | 3 |
| F21 | API docs and raw tools | `api_docs`, `scaleway`, `runpod_mcp` | `catalog.py`, `tools.py` | a catalog search, and one read-only raw call per cloud | 1 |
| F22 | Harness files for 24 harnesses | `frontier harnesses` | `sync_harnesses.py` | `--check` passes; every file parses (existing tests) | 0 |
| F23 | OpenShell sandbox | `[sandbox] kind = "openshell"` | `sandbox.py` | **BLOCKED here**: no Docker daemon in this session; only the dry run is checked | 0 |
| F24 | Code coverage of a real race | — | `coverage` across the launcher, gateway and MCP processes | a per-module coverage report of `frontier/` from the level 3 run | 3 |
| F25 | Cerebras as a provider | `[llm] provider = "cerebras"`, `spawn_agent(provider="cerebras")`, a jury judge | `llm.py` (openai-chat), `strip_message_fields` | `frontier ping --provider cerebras` OK; a metered call priced in `llm.jsonl` (verify_live F25); a judge's grades in the jury (F29) | 1 |
| F26 | Budget milestone notices | `budget_usd` of each service | `milestones.py`, the gateway's `inject` | a ledger at 12 % of its cap: one `budget_milestone` LLM 10 % event, and a real model repeats the notice the gateway added to its request (verify_live F26); events in the level 3 run | 1, 3 |
| F27 | Snapshots at time and spend milestones | `[snapshots]` | `snapshots.py`: the gateway's notices, the watchdog's `take` | a silent early snapshot taken at once; a later notice (recorded by the watchdog first) seen by the model; nothing taken within the grace, then a commit tagged `snapshot-<label>`; milestones within one grace merged (verify_live F27); `snapshot_notice` and `snapshot` events and tags in the level 3 run | 1, 3 |
| F28 | Local CPU meter | `local_cpu_usd_per_core_hour` | `CpuMeter` in the launcher's watchdog | a 3 s CPU burn by a reaped child of a mission process measured as about 3 core-seconds; a new meter on the same state starts from it (verify_live F28); `cpu_usage` events of the level 3 run against the harness's own CPU | 1, 3 |
| F29 | Jury: anonymity and leak check | `frontier jury --root --subject --context --out --judge ...` | `jury.py` | two teams whose files name their provider, model and harness: names replaced by `[redacted]`, none left in the judges' files, no judge on its own provider; with redaction off, every file refused with no call made; the jury's ledger under `--cap-usd` (verify_live F29) | 1 |
| F30 | Scoring v3 and `mission red-flag` | `frontier mission red-flag <state> --minutes --reason` | `scoring.py`, `jury.facts` | `frontier jury` writes `jury.json`: every team's quality, code and data, efficiency and final recomputed independently from the judges' grades; a red flag of 10 min takes 10 min off the counted time; a bad red flag exits 2 (verify_live F29) | 1 |
| F31 | The fixes of PRs #10 to #16 | — | each fix's regression test | every fix has a regression test that passes, and the live checks that reach it (sub-agents with `tools=[]`, metered jury calls, streamed calls, raw cloud calls refused) pass | 0, 1, 2 |

## Usability: what "usable" means, measured at level 3

- **Owner effort**: the number of manual steps from a written mission file to a running race. Target: one command.
- **Time to first useful action**: from `mission start` to the first tool call of the harness.
- **The AI understood its brief**: it used `budget_status`, `spawn_agent` and the runner tools without a wrong
  argument, and it called `deliver`. Measured as the error rate of the `mcp_call` events.
- **Within caps**: LLM and compute spend under their caps; no 402 or refusal except in the level 2 tests.
- **Readable afterwards**: the dashboard and `COSTS.md` explain where the money and the time went without opening
  the raw ledgers.
- **Nothing left**: no pod, VM, volume, IP, secret or Hugging Face repo from the test remains.

## What the live mission returns (level 3)

1. The measured part: coverage per module of `frontier/` during the run, and the list of functions never executed.
2. The AI's audit: for each module, keep, replace by instructions, or delete, with its evidence. The audit is
   written in `DELIVERY.md`, with `COMPLIANCE.md`, `DECISIONS.md` and the framework's `COSTS.md`.
3. Runner activity: commands, outputs, durations and costs of each pod.

## Rules while testing

- Check the result of each step before the next one (pstack: verifiable units).
- A defect found during the run: reproduce it first, then fix its root cause, then re-run the same check on the same
  interface. Fixes go to a branch and a PR, never into the running mission.
- The cleanup deletes only what the test created, read back after each delete.
- Every number in the final report comes from a ledger or an API read made during the test.

## Work needed before running

1. Merge PR #4 (credentials from declared sources).
2. Keep runner logs: `rp_logs` and full `rp_exec` output into `state/runners/<id>.log`, with a "Runners" section in
   the dashboard (F20).
3. Write `tools/verify_live.py`: levels 1 and 2 as one script that prints a verdict and its evidence for each
   feature, and cleans up what it created.
4. Coverage across processes: a `sitecustomize.py` started through `COVERAGE_PROCESS_START`, used only for the test
   (`coverage` is a test dependency, not a runtime one).
5. Install the test tools in the session: Codex CLI 0.169 (npm), `coverage` and `huggingface_hub` (pip).
