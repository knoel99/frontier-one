# Test results

Plan: [TEST-PLAN.md](TEST-PLAN.md). Two campaigns: 2026-11-15 (below, the features added since the first one and a
second pass on the others) and 2026-11-14 (further down).

# Campaign of 2026-11-15

Method: pstack's verification skills from their source, cursor/plugins `pstack/skills` (MIT, Lauren Tan) at commit
`bf0c7cd`: `principle-prove-it-works` (check the real artifact, script the check), `principle-sequence-verifiable-units`
(each level green before the next; failing test, then fix), and `create-verification-skill` /
`maintain-verification-skill` (a feature map with a user path, a driven path and an observable end state). The
skills could not be installed in this session's `~/.claude/skills` (refused by its permission policy); their SKILL.md
files were read and followed by hand. Keys: the proxy of a Claude Code cloud session (`[secrets]` = `proxy`).
Harness of the live mission: Codex CLI 0.169.0 on OpenAI `gpt-6-luna`.

## Verdict

**Frontier is usable, and seven more framework defects were found and fixed.** Levels 0 to 4 were run in order. Level 1 and 2
(`tools/verify_live.py`, now with checks F25 to F29 for the new features) passed on every feature, F27 after its fixes.
The live mission delivered in 12 minutes (0.198 h of 0.75) for 0.111 USD of LLM and 0.012 USD of GPU: one Codex
session, three snapshots tagged in its repository, one budget milestone, a Runpod runner that ran the framework's 110
tests, a DeepSeek review, intact ledgers and nothing left behind. A fresh agent then re-checked every verdict against
the raw artifacts (level 4): it refuted or downgraded four (F5, F8, F29's missing results file, F30) and found
D5 to D7; each was settled by a new check or a fix before the verdicts below.

## Defects found and fixed

| # | Found by | Defect | Fix |
|---|---|---|---|
| D1 | reading `snapshots.py`, then level 1 (F27) | The watchdog records milestones every 10 s; when it recorded one first, the gateway saw it as already seen and never told the harness: no "bring the report up to date" before the snapshot. Live: the model did not see the notice on the old code (F27 ISSUES), did on the new | PR #17: the gateway tells every pending milestone it has not told, whoever wrote its event |
| D2 | reading `snapshots.py`, reproduced with a locked git index | A snapshot taken while git was busy (the harness or the launcher committing) failed its commit silently; its event and tag pointed at a commit without the team's latest work | PR #18: `commit()` says whether the workspace is committed and moves tags only then; the snapshot waits for the next tick |
| D3 | checking the PR #14 fix | Under a cloud budget, raw `runpod_mcp` and `scaleway` calls could change or delete resources the run did not create (teams share the accounts; `prompts/cloud.md` says they are not touched). Live: a pod outside the mission's ledger was deleted by a raw `delete-pod` | PR #20: a raw call that changes, runs on or deletes a billed resource needs it in the run's ledger; re-checked live: refused |
| D4 | level 3 (6 failed `rp_exec` calls) | Runpod's 429 rate-limit refusals came back as errors, for the AI's calls and for the framework's own deletes. Live burst of 30 parallel `rp_exec`: 17 refused | PR #21: a 429 (no work done) is retried up to 4 times; re-checked live: 30 of 30 |
| D5 | level 4 (F5) | OpenAI Responses usage reports `input_tokens_details.cache_write_tokens` (read live); the parser dropped it, so cache writes were billed at the input price: about 5 % of the level 3 harness's spend unbooked | PR #22: the field is read; re-checked live through the gateway (6,006 written tokens booked) |
| D6 | level 4 (D2) | With the index locked only during `git add`, the add failed silently and the snapshot was still tagged on the old tree | PR #23: a failed add leaves the snapshot pending |
| D7 | level 4 (D3 scope) | Under a Runpod cap, raw `create-cluster` and `create-global-volume` were forwarded: the guard knew the catalog's creators only, the live server has 125 tools | PR #24: both refused; the other creators of the live server bill no compute |

The checks had three defects of their own (F25 spawned a sub-agent on a provider the mission does not allow; F28
compared a rounded value; F27 crossed two milestones at once and its grace was shorter than a model call); they were
fixed in `tools/verify_live.py` (PR #19) before the verdicts below.

## Feature verdicts (after the fixes)

| # | Feature | Verdict | Evidence |
|---|---|---|---|
| F1 | Mission preparation | PASS | level 1; every workspace and state file present in the level 3 run |
| F2 | Credentials from declared sources | PASS | `proxy`, `env`, `file` and `cmd` resolved; no test value in any state file (the check loads the sources without preparing a mission with them) |
| F3 | Key check | PASS | four providers at level 1, two in the level 3 run, priced `launcher` entries |
| F4 | Session loop, headless harness | PASS | one session, exit 0 after 705 s, committed; a second session was not needed |
| F5 | Gateway metering | PASS after D5 | the 59 gateway entries match Codex's report on input, cached, output and reasoning tokens, but carried no cache writes (201,708 tokens, about 5 % of the spend): fixed, re-checked live |
| F6 | LLM hard cap | PASS | 402 without forwarding; stop reason "LLM budget" |
| F7 | Deadline | PASS, partial | exit 124 after 5 s on a test command, not on a real harness session |
| F8 | MCP tools served to the harness | PASS | Codex called `budget_status` as an MCP tool through the launcher's own command and the gateway (`mcp_tool_call` item served by `frontier`); in the level 3 run the AI chose the shell fallback for every call |
| F9 | Shell fallback | PASS | same answer as MCP at level 1; all 146 tool calls of the level 3 run |
| F10 | Sub-agents on four providers and a role | PASS | openai, google, deepseek, zhipu and `reviewer` (streamed Responses and Gemini calls); in the level 3 run two of four DeepSeek reviews ended at their turn limit with an empty answer |
| F11 | Speed mode (flex) | PASS | 1.3e-05 USD against 2.7e-05 at standard |
| F12 | Runpod runner | PASS | a pod at level 1; in the level 3 run the AI's pod ran the framework's 110 tests (`110 passed in 11.23s`) and was deleted |
| F13 | Scaleway runner | PASS | DEV1-S at Amsterdam: result read from its metadata, deleted with its volume, GET 404 |
| F14 | Cloud budget cap | PASS, partial | creations over the cap refused on both clouds; parallel creations by a unit test only |
| F15 | Watchdog | PASS | a pod past `max_hours` deleted by `infra.enforce` (called by the check, not by the launcher's thread) |
| F16 | Hugging Face publishing | PASS | bad card refused; private dataset created, then deleted and read back |
| F17 | Delivery | PASS | `delivery.json`, `COSTS.md`, tag `delivery`, final costs; one `deliver` refused by design (report naming repository files) before the accepted one |
| F18 | Tamper-evident ledgers | PASS, partial | an edited line is detected and every ledger of the campaign is intact; a ledger cut at its end still verifies (the chain has no anchored head) |
| F19 | Dashboard | PASS | live page served (HTTP 200) during the run; the static snapshot renders without page errors and its figures equal the ledgers |
| F20 | Runner activity | PASS | `runners/<id>.log`: creation, every command and output, logs before deletion |
| F21 | API docs and raw calls | PASS | catalog search, read-only Scaleway and Runpod calls |
| F22 | Files for 24 harnesses | PASS | `sync_harnesses --check`; only Codex was run live |
| F23 | OpenShell sandbox | BLOCKED | no Docker daemon running in the session |
| F24 | Coverage of a real race | PASS, partial | statement coverage, 57 %; includes the dashboard and one check script |
| F25 | Cerebras | PASS | `ping` OK; a metered call priced; a Cerebras judge graded both teams |
| F26 | Budget milestone notices | PASS | the model repeated the notice the gateway added; `budget_milestone` LLM 10 % in the level 3 run |
| F27 | Snapshots | PASS after D1, D2, D6 | silent early snapshot, notice seen by the model although the watchdog recorded it (ISSUES on the old code), grace, tags, merging; three snapshots tagged in the level 3 run, the report absent at the two silent ones and present at 8 min |
| F28 | Local CPU meter | PASS | 3.0 core-seconds for a 3 s burn by a reaped child; 54.8 core-seconds over the level 3 run |
| F29 | Jury: anonymity and leak check | PASS | names replaced, none left in the judges' files, no judge on its own provider; with redaction off, every file refused and no call made |
| F30 | Scoring v3 and `mission red-flag` | PASS | the CLI's `jury.json`: quality, code and data, efficiency and final of both teams recomputed independently from the judges' grades, equal; a red flag took 10 min off; a bad one exits 2 |
| F31 | Fixes of PRs #10 to #16 | PASS | each has a regression assertion that passes, except `4c8df21` (no test; checked by hand); D3 and D7 were found in the scope of the #14 fix |

## Usability, measured on the level 3 run

- Owner effort: one command after writing the mission file.
- First tool call 7 s after the session started (`budget_status`); delivered after 0.198 h of a 0.75 h deadline.
- 146 tool calls, 9 errors: 7 `rp_exec` (6 Runpod 429, fixed by D4; one command of 60,066 characters over Runpod's
  4,096 limit), 1 `rp_offers` with a wrong argument name (`cloud_type` for `cloud`), and 1 `deliver` refused by design
  because the report named repository files; the AI moved them to DELIVERY.md and delivered 34 s later.
- With no file transfer to a runner, the AI pushed the repository to the pod as base64 in about 110 `rp_exec` calls
  of 2,000 characters: it works, but it is the slow path.
- LLM 0.107 USD of 0.6 at delivery, 0.004 USD after it; GPU 0.012 USD of 0.30. The gateway's 59 entries add up to
  Codex's own usage report token for token (5,354,716 input of which 5,152,831 cached, 51,822 output).
- Readable afterwards: the dashboard (no page error) and COSTS.md give the spend, the runner, its 130 commands and
  the four sub-agents as in the ledgers.
- Nothing left: no pod, Scaleway server, IP or volume, and no Hugging Face test repository.

## Coverage of the live race (statements)

57 % of `frontier/` overall. `snapshots.py` 96 %, `agent.py` 93 %, `budget.py` 89 %, `mission.py` 81 %,
`milestones.py` 73 %, `gateway.py` 64 %, `llm.py` 57 %, `launcher.py` 56 %, `infra.py` 53 %. Not executed by a race,
as expected: `jury.py` and `scoring.py` (run after the race, checked at level 1), `sandbox.py` (no Docker), `egress.py`
(no deny list). The figure includes the dashboard and one check script that ran while coverage was on.

## Cost of the campaign

From the ledgers: LLM 0.168 USD (0.228 USD in the ledgers, of which 0.06 is the synthetic entry F26 writes to put a
ledger at 12 % of its cap): 0.141 USD up to level 3, 0.027 USD at level 4 (the jury CLI run, the MCP and
cache-write probes). The level 3 mission cost 0.111 USD and the two jury checks 0.028 and 0.024 USD. Outside any
ledger, on purpose: two raw OpenAI calls reading the usage block, one Codex probe without the gateway and the
`frontier ping` of Cerebras, under 0.02 USD together. Runpod 0.028 USD in the ledgers, plus two pods of the D3 check
created outside any ledger (about a minute each at 0.27 USD/h, under 0.01 USD). Scaleway 0.0001 EUR.
Budget: 2 EUR of LLM and 2 EUR of compute.

## Not done or still open

- F23 OpenShell: BLOCKED, no Docker daemon in the session.
- A snapshot milestone crossed in the same instant as a silent earlier one (only possible when a launch resumes across
  `notice_from_minutes`) is merged into the silent snapshot and taken without its grace. Not fixed: minor, and not
  reachable in a normal run.
- A judge's "Repository listing" gives file sizes from before redaction: the difference tells the length of the
  names removed. Weak, not fixed.
- Under a cloud budget, a raw call still reaches a resource named only in its body, and Scaleway job definitions and
  runs are not covered by the ownership rule (D3); without a cloud budget the raw tools stay open by design.
- Commit `4c8df21` (COSTS.md of an undelivered mission lists its publications) has no regression test; its behaviour
  was checked by hand and holds.
- Only Codex was run live; the deadline on a real harness session, `frontier reap`, and two parallel cloud creations
  live remain unchecked, as in the first campaign.

# Campaign of 2026-11-14

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
