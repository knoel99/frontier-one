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

**Frontier is usable, and fourteen more framework defects were found and fixed.** Levels 0 to 4 were run in order. Level 1 and 2
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
| D8 | F23 with Docker | Inside the mission's sandbox, Codex's own sandbox (bubblewrap, `--sandbox workspace-write`) cannot create a namespace: every command and file write failed and the AI gave up | PR #26: a harness's `in_sandbox` table (Codex: `danger-full-access`), applied by `frontier mission session`; re-checked: the session in the image delivered |
| D9 | harnesses live | Gemini CLI 0.68 died in 1.9 s ("Invalid auth method selected", exit 41), its untrusted workspace turning yolo back into approval prompts | PR #32: `GEMINI_CLI_TRUST_WORKSPACE=true` and a setup that selects `gemini-api-key`; re-checked: delivered |
| D10 | harnesses live | Mistral Vibe 3.1.2 refused the mission's model ("Unknown model ... Available: mistral-medium-3.5") and could not be routed, so no Vibe session was metered | PR #33: the project `.vibe/config.toml` declares a provider on the gateway and the mission's model; re-checked: accepted, calls reach the gateway |
| D11 | F23 with OpenShell | `openshell sandbox create --from <Dockerfile>`: "'--from' no longer builds local Dockerfiles"; and a failed OpenShell command surfaced without OpenShell's message | PR #35: the image is built with the gateway's engine and passed by name; a failed command shows its output |
| D12 | F23 with OpenShell | The policy listed binaries as strings: "binaries[0]: type mismatch: expected mapping" | PR #35: `{path: ...}` items |
| D13 | F23 with OpenShell | "workload user 'sandbox' does not exist in the pinned image" | PR #35: the image creates the policy's user |
| D14 | F23 with OpenShell | `sandbox upload <dir> <dest>` writes `<dest>/<dir name>`: the session found no `/sandbox/state/mission.json` | PR #35: staging path, then moved into place; re-checked: the mission delivered inside the sandbox |

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
| F7 | Deadline | PASS | a Codex mission of 90 s whose experiment is `sleep 300`: the session ended at the deadline (exit 124, `stopped: deadline`) and no process of it was left; exit 124 after 5 s on a test command |
| F8 | MCP tools served to the harness | PASS | Codex called `budget_status` as an MCP tool through the launcher's own command and the gateway (`mcp_tool_call` item served by `frontier`); in the level 3 run the AI chose the shell fallback for every call |
| F9 | Shell fallback | PASS | same answer as MCP at level 1; all 146 tool calls of the level 3 run |
| F10 | Sub-agents on four providers and a role | PASS | openai, google, deepseek, zhipu and `reviewer` (streamed Responses and Gemini calls); in the level 3 run two of four DeepSeek reviews ended at their turn limit with an empty answer |
| F11 | Speed mode (flex) | PASS | 1.3e-05 USD against 2.7e-05 at standard |
| F12 | Runpod runner | PASS | a pod at level 1; in the level 3 run the AI's pod ran the framework's 110 tests (`110 passed in 11.23s`) and was deleted |
| F13 | Scaleway runner | PASS | DEV1-S at Amsterdam: result read from its metadata, deleted with its volume, GET 404 |
| F14 | Cloud budget cap | PASS | creations over the cap refused on both clouds; two `rp_create_pod` at once under a cap that holds one: one created, one refused (live) |
| F15 | Watchdog | PASS | a pod past `max_hours` deleted by `infra.enforce`; `frontier reap <state>` (the CLI) deleted another past its `max_hours` (get-pod 404) |
| F16 | Hugging Face publishing | PASS | bad card refused; private dataset created, then deleted and read back |
| F17 | Delivery | PASS | `delivery.json`, `COSTS.md`, tag `delivery`, final costs; one `deliver` refused by design (report naming repository files) before the accepted one |
| F18 | Tamper-evident ledgers | PASS, partial | an edited line is detected and every ledger of the campaign is intact; a ledger cut at its end still verifies (the chain has no anchored head) |
| F19 | Dashboard | PASS | live page served (HTTP 200) during the run; the static snapshot renders without page errors and its figures equal the ledgers |
| F20 | Runner activity | PASS | `runners/<id>.log`: creation, every command and output, logs before deletion |
| F21 | API docs and raw calls | PASS | catalog search, read-only Scaleway and Runpod calls |
| F22 | Files for 24 harnesses | PASS, partial | `sync_harnesses --check`; run live through `frontier mission start`, each delivered and metered by the gateway: Codex, dsh, OpenCode (DeepSeek), Qwen Code, Kimi Code, Gemini CLI (after D9), ZCode, Antigravity, Grok Build and Hermes (DeepSeek); Vibe reached the gateway after D10, then its Mistral account refused it (see below); Claude Code not run (no Anthropic key) |
| F23 | OpenShell sandbox | PASS | OpenShell 0.1.8 with its Docker driver: a mission ran its session inside the sandbox and delivered (after D8, D11 to D14): 9 calls metered by the gateway inside, intact ledgers, commits made inside back on the host with the `delivery` tag, nothing left (sandbox, provider, image); the framework's policy allowed `api.openai.com` and blocked a host outside it |
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

## Follow-up of the same day

With Docker started in the session, the open items were taken up:
- F23: see D8 above. F7, F14 and F15: the three live checks the first campaign left to unit tests (PR #30).
- More harnesses live: D9, D10 (PRs #32, #33).
- Usability: `rp_upload` (PR #31) copies a workspace file or directory into a pod in base64 chunks with a SHA-256
  check; live: `frontier/` and `config/` (116 KB) in 54 calls and 28 s, then imported on the pod.
- The jury's file listing no longer tells the length of redacted names (PR #27); Scaleway job definitions and runs
  follow the ownership rule of D3 (PR #28); commit `4c8df21` has its regression test (PR #29).
- Not a defect: a told snapshot milestone crossed in the same instant as a silent one is merged into the silent
  snapshot. The module's docstring states it and `test_snapshots_notice_merge_tag_and_event` asserts it.

Spend of the follow-up, from the ledgers: LLM 0.249 USD (the harness missions 0.229, Gemini 0.134 of it), Runpod
0.012 USD. Everything created was deleted and read back (no pod, server, IP, volume or Hugging Face test repository);
the Docker images built for F23 were removed.

## OpenShell and the remaining harnesses (same day, with the owner's go-ahead to install)

OpenShell 0.1.8 was installed from NVIDIA's release (its install script checks the package's SHA-256), its gateway
started by hand (no systemd here) with the Docker driver. Host setup it needed, outside the framework:
- this session reaches the Internet only through a TLS-intercepting proxy: the gateway config
  (`~/.config/openshell/gateway.toml`, schema version 2) sets `https_proxy`, `proxy_ca_bundle` and
  `proxy_connect_by_hostname` under `[openshell.drivers.docker]`; without them every allowed connection failed;
- OpenShell injects keys from the host: a key the session proxy holds needs an `env:` placeholder in `[secrets]`;
- an SSH client on the host (`openssh-client`), which `sandbox upload` uses.

Then D11 to D14 (PR #35), one after the other, each found by the next live run. Antigravity, Grok Build and Hermes
were installed with their vendors' scripts, ZCode built from its repository: all four delivered.

Spend of these runs, from the ledgers: LLM 0.27 USD (the four harness missions 0.26, the OpenShell missions 0.01).
Nothing left in the clouds, on the Hub or in OpenShell; the base images were removed (OpenShell's own runtime images
stay with its install).

## Still open

- Mistral Vibe end to end: this account answers 429 to any request of about 15,000 tokens (checked directly), and
  Vibe's system prompt is that large.
- Claude Code: no Anthropic key in this session.
- A raw cloud call that names another team's resource only in its body is not checked; every billed creation is
  refused under a cloud budget, so nothing of theirs can be billed this way.
- A ledger cut at its end still verifies (the hash chain has no anchored head).

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
