# Test results, 2026-11-14

Plan: [TEST-PLAN.md](TEST-PLAN.md). Keys: the real keys of a Claude Code cloud session, injected by its proxy
(`[secrets]` set to `proxy`); none was typed or written down. Harness: Codex CLI 0.169 on OpenAI.

## Verdict

**Frontier is usable.** A mission file and one command gave a delivered audit in 12 minutes for 0.08 USD of LLM and
0.01 USD of GPU, with one harness session, a Runpod runner created, used and deleted by the AI, an independent
sub-agent on DeepSeek, intact ledgers and nothing left behind. The campaign found **seven defects in the framework**.
Each was reproduced, fixed at its root with a regression test, and checked again on the same interface.

## Defects found and fixed

| # | Found by | Defect | Fix |
|---|---|---|---|
| 1 | level 2 (F6) | The launcher restarted sessions while the gateway refused every call: it stopped only once the spend reached the cap, not when what was left could not cover one call | `cead016`: the gateway records `llm_refused`; the launcher stops after a refused session |
| 2 | level 3, first run | Codex starts MCP servers without `HTTPS_PROXY`: keys injected by a proxy never reached the frontier server (Runpod 401, sub-agents without keys) | `74a828a`: `env_vars` in `.codex/config.toml` |
| 3 | level 3, first run | The AI ran the framework's own tests inside its mission; with `FRONTIER_STATE` set they wrote a fake pod into the mission's real ledger | `74a828a`: `tests/conftest.py` clears every `FRONTIER_*` variable |
| 4 | level 4 | Codex (approval policy never) refused every non-read-only frontier MCP tool; the AI fell back to `frontier call` | `f06b853`: `default_tools_approval_mode = "approve"` |
| 5 | level 4 | A tool call that raised was answered but not logged, so the error rate read 0 | `f06b853`: logged with `error: true` |
| 6 | level 4 | Spend after `deliver` (the harness finishing its turn) was missing from `COSTS.md` and `delivery.json` | `f06b853`: `final_costs` at the end of a delivered mission |
| 7 | level 4 | `COSTS.md` printed "of None EUR" for an unused service | `f06b853`: "not used" |

The checks themselves also had four defects (wrong labels, a wrong import, a cleanup that only looked): they were
fixed in `tools/verify_live.py`. One of them left a Scaleway VM alive for about 20 minutes; it was deleted and read back.

## Feature verdicts (after the fixes)

| # | Feature | Verdict | Evidence |
|---|---|---|---|
| F1 | Mission preparation | PASS | level 1; every workspace and state file present in the level 3 run |
| F2 | Credentials from declared sources | PASS | `proxy`, `env`, `file` and `cmd` resolved; no test value in any file; real Scaleway VM (`file:`) and Runpod pod (`env:` from a Runpod secret) earlier the same day |
| F3 | Key check | PASS | four providers, priced `launcher` entries |
| F4 | Session loop, headless harness | PASS | one session, exit 0, committed |
| F5 | Gateway metering | PASS | the 40 gateway entries of the level 3 run add up exactly to Codex's own usage report |
| F6 | LLM hard cap | PASS | 402 without forwarding; stop reason "LLM budget" (one live run, through `_stop_reason`) |
| F7 | Deadline | PASS, partial | exit 124 after 5 s on a test command; the whole Codex process group (sandbox and MCP server included) ended on a real interruption |
| F8 | MCP tools served to the harness | PASS | after fix 4: every spending tool completed over MCP in a live Codex session |
| F9 | Shell fallback | PASS | same answer as MCP; the AI used it when MCP refused (before fix 4) |
| F10 | Sub-agents on four providers | PASS | openai, google, deepseek, zhipu and the `reviewer` role; DeepSeek used by the AI |
| F11 | Speed mode (flex) | PASS | about × 0.5 for the same tokens |
| F12 | Runpod runner | PASS | three pods created, used and deleted (`get-pod` 404, `list-pods` empty) |
| F13 | Scaleway runner | PASS | DEV1-S at Amsterdam: result read from its metadata, then deleted with its volume |
| F14 | Cloud budget cap | PASS, partial | creations over the cap refused on both clouds; parallel creations checked by a unit test only |
| F15 | Watchdog | PASS | a pod past `max_hours` deleted by the watchdog; `frontier reap` not run |
| F16 | Hugging Face publishing | PASS | bad card refused; private dataset created, then deleted |
| F17 | Delivery | PASS | `delivery.json`, `COSTS.md`, frozen spend, tag; the commit inside `deliver` fails in Codex's sandbox (`.git` read-only), and the launcher's own commit makes it |
| F18 | Tamper-evident ledgers | PASS | an edited copy is detected; every ledger of the campaign intact |
| F19 | Dashboard | PASS | live in the app's preview pane during the run; a snapshot published as a private claude.ai page |
| F20 | Runner activity | PASS | `state/runners/<id>.log`: every command, output, exit code and the pod's logs before deletion |
| F21 | API docs and raw calls | PASS | catalog search, read-only Scaleway and Runpod calls |
| F22 | Files for 23 harnesses | PASS | `sync_harnesses --check`; only Codex was run live |
| F23 | OpenShell sandbox | BLOCKED | no Docker daemon in the session |
| F24 | Coverage of a real race | PASS, partial | statement coverage (no branches); the dashboard process was still running, so its share is missing |

## Usability, measured on the level 3 run

- Owner effort: one command after writing the mission file.
- First tool call 16 s after the session started; delivered after 12 min (0.20 h of a 2 h deadline).
- 16 tool calls; the errors that were then unlogged (fix 5) were a 422 and a 409, both handled by the AI.
- LLM 0.077 USD of 1.15 at delivery; 0.004 USD more after it (fix 6). GPU 0.014 USD of 1.90.
- Cost rate: Codex on `gpt-6.1-sol` spent 0.27 USD in 90 s of a first run (about 10 USD/h, its context resent at
  every call); on `gpt-6-luna` the whole mission cost 0.08 USD. The model of the main thread decides the budget.

## What is useful in the repository

The AI's audit (DELIVERY.md of the run) keeps every module and recommends **removing `old/`** from the active tree
(about 33,360 lines, kept in git history), then more tests for the CLI, `http.py`, the launcher's lifecycle and OpenShell.
The coverage of the live race adds what unit tests cannot: the code a real race executes.

| Module | Executed by a real race | Reading |
|---|---:|---|
| `agent.py`, `budget.py`, `mission.py`, `config.py`, `credentials.py` | 74-100 % | core of every race |
| `http.py`, `tools.py`, `gateway.py`, `llm.py`, `harnesses.py` | 61-79 % | core; the rest is other providers and harnesses |
| `infra.py`, `launcher.py`, `mcp.py`, `__main__.py` | 52-53 % | core; Scaleway paths and the sandbox branch unused here |
| `catalog.py`, `hf.py` | 22 % | optional: offline API docs, Hub publishing |
| `dashboard.py` | 0 % (process still running) | organizer side, not the race |
| `sandbox.py` | 0 % | not used without Docker |

## Cost of the campaign

LLM 0.91 USD as the ledgers count it, which overstates the bill: one call interrupted on purpose was charged its
worst case (0.53 USD). Runpod 0.02 USD, Scaleway 0.002 EUR. Budget: 2 EUR of LLM and 2 EUR of compute.

## Not done

- OpenShell end to end (no Docker), and every harness other than Codex.
- Two parallel cloud creations live, `frontier reap`, and the deadline on a real harness session.
- Branch coverage of the live race.
