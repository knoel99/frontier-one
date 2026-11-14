# Industry standards and where Frontier stands

Survey of 2026-11-14 (official specs and docs; [U] = only secondary sources or not confirmed). For each topic: the
standard, what Frontier does, and the gaps that remain.

## 1. Harness-agnostic repositories

| Standard | Status in the industry | Frontier |
|---|---|---|
| **AGENTS.md** (agents.md, Agentic AI Foundation since 2025-12) | plain Markdown; read by nearly every harness; Codex caps the combined files at 32 KiB; Claude Code reads it only without a CLAUDE.md | `AGENTS.md` everywhere; `CLAUDE.md` = `@AGENTS.md`; Gemini and Qwen `context.fileName` (docs/HARNESSES.md) |
| **MCP** (Linux Foundation / AAIF; spec revisions 2025-06-18, 2025-11-25, 2026-07-28) | stdio: one JSON message per line, logs on stderr, exit on EOF; tool annotations `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`; 2026-07-28 is stateless (`server/discover`, version in `_meta`, `resultType`) and no CLI harness is confirmed to speak it yet [U] | standard-library stdio server: handshake for 2024-11-05 to 2025-11-25, `server/discover`, calls accepted without a handshake, every tool annotated, deterministic order. **Gap**: the 2026-07-28 result fields (`resultType`, `ttlMs`, `cacheScope`, `_meta` versioning) are not implemented; the official `mcp>=2` Python SDK serves every revision and is the way to close it if the standard-library rule is relaxed |
| **Agent Skills** (agentskills.io, AAIF since 2026-10) | `SKILL.md` with `name` (= directory, lowercase) and `description` (≤ 1,024 characters); discovery in `.agents/skills/`, plus `.claude/skills/` (Claude Code) and `.qwen/skills/` (Qwen) | `frontier` skill in `.agents/skills/`, mirrored to `.claude/skills/` and `.qwen/skills/` |
| **A2A** (v1.0, AAIF) | agent-to-agent across organisations | not used: one team, one machine |
| **ACP** (Agent Client Protocol: Zed, JetBrains) | editor ↔ agent over stdio; ~50 agents | not used; a possible future driver for the launcher (one protocol to run and record many harnesses) |

## 2. LLM cost and budget control

- **Gateways.** LiteLLM is the common open-source gateway, but its budgets need Postgres and, without a database, its
  global `max_budget` fails open; two LiteLLM releases (1.82.7, 1.82.8) were malicious on PyPI on 2026-03-24.
  Portkey, Helicone and Langfuse changed hands in 2026; Cloudflare AI Gateway spend limits are best effort (cost
  recorded after the request). OpenRouter per-key `limit` is a real hard cap (403 before the provider), and its
  responses report `usage.cost`.
- **Frontier.** Its own standard-library gateway (`frontier/gateway.py`) passes requests through unchanged (no
  translation, every harness feature kept), meters four wire formats, streaming included, and enforces a hard cap
  with a **reservation** of each call's worst case (whole request as uncached input + its output limit), held until
  the call is charged and shared by every process of the mission (sub-agents, gateway, shell calls), so parallel
  calls cannot overrun the cap together. A cost reported by the API (OpenRouter `usage.cost`) is used as is.
- **Telemetry names.** The ledger (`llm.jsonl`) uses the OpenTelemetry GenAI attribute names (`gen_ai.provider.name`,
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `…cache_read.input_tokens`, `…cache_write.input_tokens`,
  `…reasoning.output_tokens`, `gen_ai.agent.name`) plus `frontier.cost.usd` (the conventions have no cost
  attribute; they are still in Development status).
- **Provider-side caps, to set as a second line.** OpenAI organisation/project hard limits (429
  `project_spend_limit_exceeded`, monthly, not instantaneous); Anthropic workspace limits (monthly); OpenRouter key
  limits; Runpod prepaid balance with auto-pay off (the only hard cap on Runpod; at $0 pods stop and pods without a
  network volume lose their data). Scaleway has billing alerts only.
- **Gap.** A harness whose traffic cannot be routed through the gateway is metered from its own usage report at the
  end of each session (between sessions, not during one); `config/harnesses.toml` says which harness is in which case.

## 3. Cloud spend guardrails

- **Practice.** Mandatory owner / run / expiry tags at creation, an automatic reaper for expired or untagged
  resources, scoped and expiring keys (Scaleway IAM applications limited to one project, `expires_at`; Runpod
  restricted keys; Hugging Face fine-grained tokens per use).
- **Frontier.** Every resource has a `max_hours` and the budget reserves price × max_hours at creation; the ledger
  is shared; resources the run did not create are never touched; the watchdog of the MCP server and of the launcher
  deletes what overran its time or budget; Scaleway Instances carry `frontier`, `run:<id>`, `expires:<time>` tags and
  Runpod pods `FRONTIER_RUN`, `FRONTIER_EXPIRES`; `frontier reap <state>` is a dead-man sweep to run from cron on the
  host, which also deletes expired Instances tagged with the run that the ledger lost; a Scaleway terminate leaves
  Block volumes and IPs, which the delete function removes.
- **Gap.** No switch inside the machine itself (a pod or Instance whose host-side processes all died keeps running
  until a reaper runs): run `frontier reap` from cron, and use the provider-side caps above.

## 4. Evaluating autonomous research agents

- **Benchmarks.** PaperBench (weighted rubric trees written with the authors, an LLM judge checked against human
  grades, `reproduce.sh` re-run in a fresh VM, using banned resources scores 0), MLE-bench (≥ 3 seeds, mean ± SEM,
  rule-violation detectors), RE-Bench (normalised score, timestamped score log, cost per run), ScienceAgentBench and
  HAL/CORE-Bench (accuracy against USD cost; automatic grading under-counted valid alternatives), METR (reward hacks
  count as failures; API + GPU cost and wall-clock time next to every score).
- **Lesson from METR's 2026-08-26 incident report.** Agents attacked a scorer and some edited their transcripts:
  logs and judges must be out of the agents' reach.
- **Frontier.** The framework, not the agent, writes the ledgers and logs; they live outside the agent's workspace
  (`~/.frontier/missions/<name>/`) and `llm.jsonl` and `events.jsonl` are hash-chained (an edited line is detected;
  `budget_status` and `COSTS.md` report the integrity and the last hash). Cost (LLM + cloud) and delivery time are
  measured by the framework and written at `deliver`. The compliance matrix gives a judge one rubric leaf per
  instruction of the subject.
- **Not built here (the evaluator's side).** A judge: a PaperBench-style weighted rubric whose leaves are the
  subject's instruction ids and the research claims, re-running `DELIVERY.md`'s commands on a fresh machine, kept
  hidden from the teams, with its agreement checked against a few hand-graded submissions.

## 5. Research deliverables

| Standard | What it asks | In the mission brief (`prompts/mission.md`) |
|---|---|---|
| NeurIPS 2026 paper checklist | 16 items: claims, limits, reproducibility, statistical significance, compute, licences, LLM use | answers required (report or `CHECKLIST.md`) |
| Hugging Face model / dataset cards | YAML metadata (`license`, `library_name` for models since 2024-08, `task_categories` for datasets), a real description | required for every publication; `hf_publish` refuses a card without license (and library_name for a model) or with an empty body |
| Croissant 1.1 (MLCommons) | dataset metadata with provenance; the Hub generates the core file | left to the Hub's automatic file |
| CITATION.cff 1.2.0 | citation metadata (GitHub, Zenodo) | required |
| Requirements traceability (ECSS-E-ST-10C matrix; deviation vs waiver, ECSS-M-ST-40C) | one row per requirement, status, evidence, justification of departures | `COMPLIANCE.md` matrix: id, instruction and location, status (`compliant`, `partial`, `deviation`, `not applicable`), evidence, justification |
| Agent logbooks (ICML 2026 agent reproduction challenge, Trackio) | one page per claim, every run logged with command, output, exit code, duration; toy runs labelled | not required: the hash-chained framework logs, `DECISIONS.md` and the reproduction commands cover the same ground; Trackio can be installed by the agent if it wants one |

These deliverable requirements are defaults of the brief template; the mission owner edits them there.
