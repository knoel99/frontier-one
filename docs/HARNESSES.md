# Harness compatibility

This page lists what each coding harness reads, what Frontier generates for it, and how the launcher runs it.

- `frontier/harnesses.py` generates the files. `tools/sync_harnesses.py` writes them into this repository, and the
  mission launcher writes them into a mission's workspace.
- `config/harnesses.toml` holds the launch recipes.
- The file formats come from each harness's official documentation, read on 2026-11-14. UNCONFIRMED marks what
  the documentation leaves open.

## Three standards cover most harnesses

| Standard | Files | Read by |
|---|---|---|
| AGENTS.md | `AGENTS.md` | natively by Codex, Antigravity, Grok Build, Kimi Code, Qwen Code, Mistral Vibe (root: UNCONFIRMED), dsh, Copilot CLI, Cursor, OpenCode, Hermes, Goose, Crush, Kilo, Devin/Windsurf, Amp, Zed, OpenHands; Claude Code through `CLAUDE.md` = `@AGENTS.md`; Gemini CLI through `context.fileName`; Aider through `read:`; Cline and Continue through a one-line rule |
| MCP (stdio) | one small file per harness declaring the `frontier` server | every harness except Aider |
| Agent Skills | `.agents/skills/frontier/SKILL.md` (and `.claude/skills/` for Claude Code and Grok) | Codex, Antigravity, Gemini CLI, Kimi, Qwen, Vibe, dsh, Copilot, Cursor, OpenCode, Hermes, Goose, Crush, Cline, Zed, OpenHands, Amp, Devin |
| Sub-agents | `roles/*.md` turned into each harness's format | see the table |

A harness without MCP reaches the same tools from the shell, and so does a session whose MCP server is not
connected:

```bash
bin/frontier call <tool> '<json>' [--state <workspace>/.frontier]
```

## What each harness reads

| Harness | Instructions | Project MCP file (generated) | Sub-agents (generated) | Notes and sources |
|---|---|---|---|---|
| Claude Code | `CLAUDE.md` → `@AGENTS.md` | `.mcp.json` (no `cwd` field; one-time approval) | `.claude/agents/*.md` (`model`, `effort`) | code.claude.com/docs/en/{memory,mcp,sub-agents,skills} |
| Codex CLI | AGENTS.md | `.codex/config.toml` `[mcp_servers.frontier]` (trusted projects) | `.codex/agents/*.toml` (`model`, `model_reasoning_effort`) | learn.chatgpt.com/docs/{agent-configuration/agents-md, extend/mcp, agent-configuration/subagents} |
| Antigravity CLI | AGENTS.md, GEMINI.md, `.agents/rules` | `.agents/mcp_config.json` | `.agents/agents/*.md` (model `inherit`/`flash`/`pro`; no effort field) | antigravity.google/docs/{rules,mcp,subagents,skills}?tab=cli |
| Gemini CLI | `context.fileName` = AGENTS.md, GEMINI.md | `.gemini/settings.json` | `.gemini/agents/*.md` | geminicli.com/docs; replaced by Antigravity for some accounts on 2026-06-18 |
| Grok Build | AGENTS.md | `.grok/config.toml` (also loads `.mcp.json`) | `.grok/agents/*.md` (per-agent model/effort UNCONFIRMED) | docs.x.ai/build; xai-org/grok-build user guide |
| Kimi Code | AGENTS.md | `.kimi-code/mcp.json` (trusted workspace) | `.agents/agents/*.md` (no model field) | kimi.com/code/docs/en/kimi-code-cli/customization |
| Qwen Code | QWEN.md and AGENTS.md | `.qwen/settings.json` | `.qwen/agents/*.md` (`model`; no effort field) | qwenlm.github.io/qwen-code-docs |
| Mistral Vibe | AGENTS.md (root loading UNCONFIRMED) | `.vibe/config.toml` `[[mcp_servers]]` (trusted folders) | `.vibe/agents/*.toml` | docs.mistral.ai/vibe/code/cli |
| DeepSeek dsh | AGENTS.md, CLAUDE.md | none in the repo: `.dsh/frontier.patch.yml` passed with `--patch` | no project file documented | github.com/deepseek-ai/deepseek-harness docs |
| Copilot CLI | AGENTS.md and others | `.github/mcp.json` and `.mcp.json` | `.github/agents/*.agent.md` (`model`) | docs.github.com/en/copilot/how-tos/copilot-cli |
| Cursor | AGENTS.md, `.cursor/rules` | `.cursor/mcp.json` | reads `.claude/agents` and `.codex/agents` | cursor.com/docs/context/{rules,mcp,subagents,skills} |
| OpenCode | AGENTS.md | `opencode.json` `mcp.servers` | `.opencode/agents/*.md` (`model` `provider/model#variant`) | opencode.ai/docs/{rules,mcp-servers,agents,skills} |
| ZCode (Z.ai) | AGENTS.md (UNCONFIRMED) | user level only: `~/.zcode/cli/config.json` `mcp.servers`; a mission gives ZCode a home of its own | none | github.com/zai-org/ZCode |
| Hermes Agent | AGENTS.md (unless `.hermes.md`) | user level only: `~/.hermes/config.yaml` `mcp_servers` | none (its `delegate_task` tool) | hermes-agent.nousresearch.com/docs/user-guide |
| Goose | AGENTS.md, `.goosehints` | user level only: `~/.config/goose/config.yaml` `extensions` | `.agents/agents/*.md` (`model`) | goose-docs.ai/docs/guides |
| Crush | AGENTS.md and many others | `.crushrc` (`mcp add …`) | none | github.com/charmbracelet/crush |
| Kilo Code | AGENTS.md | `.kilo/kilo.json` | `.kilo/agents/*.md` | kilo.ai/docs |
| Devin Desktop / Windsurf | AGENTS.md | `.devin/mcp_config.json` | `.agents/agents/*.md` | docs.devin.ai |
| Amp | AGENTS.md | `.amp/settings.json` (`amp mcp approve frontier`) | plugins only | ampcode.com/docs |
| Continue | `.continue/rules/agents.md` → AGENTS.md | `.continue/mcpServers/frontier.yaml` | UNCONFIRMED | docs.continue.dev |
| Zed agent | AGENTS.md (a `.rules` file would win) | `.zed/settings.json` (project level UNCONFIRMED) | none | zed.dev/docs/ai |
| Cline | `.clinerules/agents.md` → AGENTS.md | user level only | UNCONFIRMED | docs.cline.bot |
| OpenHands | AGENTS.md | settings or UI only | none | docs.openhands.dev |
| Aider | `.aider.conf.yml` `read: [AGENTS.md]` | no MCP client: shell fallback | none | aider.chat/docs |

Roo Code was archived on 2026-05-15, and this page does not cover it.

## Sub-agents on the right model and effort

A native sub-agent file can set the model and the reasoning effort only where the harness documents those fields:
Claude Code, Codex, Cursor, and OpenCode (its `#variant`). In every other harness, a native sub-agent inherits the
main model.

The `spawn_agent` tool of the MCP server works the same in every harness. Each call names a provider, a tier, a
reasoning level, and a speed mode. The call runs on the model API, and the framework charges it to the mission's
budget.

## How the launcher runs a harness

`config/harnesses.toml` holds a launch recipe for twelve harnesses. A recipe gives:

- the install and setup commands
- the headless command and its environment
- the route of the harness's own LLM traffic through the metering gateway
- the usage report that the launcher reads when the traffic is not metered

A provider without its own harness gets OpenCode. The other twelve harnesses get project files only.

Before you rely on a recipe with a new version of a harness, run `mission start --prepare-only` and one short
session.

Eleven harnesses each delivered a live mission on 2026-11-15, behind the outbound filter, with their calls metered
by the gateway (`docs/TEST-RESULTS.md`, F22). The table gives the route and the version that ran, where the recipe
records one. The vendor commands first ran headless on 2026-11-13, in the previous campaign
(`old/race/PROVIDERS-TESTED.md` at commit 7223bd6, in git history).

| Harness | Routed through the gateway by | Run live | Fallback when not metered |
|---|---|---|---|
| Claude Code | `ANTHROPIC_BASE_URL` | delivered 2026-11-15 | `total_cost_usd` of `--output-format json` |
| Codex CLI | a custom provider passed with `-c model_providers.frontier.*` (Codex ignores `OPENAI_BASE_URL`; WebSockets off) | delivered 2026-11-14 and 2026-11-15, Codex 0.169.0 | `turn.completed` usage of `--json` |
| Antigravity CLI | `GOOGLE_GEMINI_BASE_URL` | delivered 2026-11-15 | `usage` of `--output-format json` |
| Gemini CLI | `GOOGLE_GEMINI_BASE_URL` | delivered 2026-11-15, Gemini CLI 0.68 | `stats` of `--output-format json` |
| Grok Build | `GROK_XAI_API_BASE_URL` | delivered 2026-11-15, grok 1.0.77: the gateway's charge matched xAI's own `cost_in_usd_ticks` | its JSON report (UNCONFIRMED) |
| Kimi Code | `KIMI_MODEL_BASE_URL` (`KIMI_MODEL_PROVIDER_TYPE=kimi`) | delivered 2026-11-15 | none documented |
| Qwen Code | `OPENAI_BASE_URL` | delivered 2026-11-15 | `usage` of the result message |
| Mistral Vibe | a `[[providers]]` entry on the gateway in the project `.vibe/config.toml`, read with `--trust` | Vibe 3.1.2 reached the gateway on 2026-11-15, then the Mistral account refused the request | its JSON output |
| DeepSeek dsh | `DEEPSEEK_BASE_URL` (Anthropic Messages) | delivered 2026-11-15 | none documented |
| OpenCode | a provider `frontier` in the mission's `opencode.json` (`baseURL`, `transport: http`) | delivered 2026-11-15 on DeepSeek and on Cerebras (opencode 1.19.5 on Cerebras) | `--format json` |
| ZCode | a home of the mission whose `provider_config.json` puts the model on the gateway | delivered 2026-11-15, v0.17.1 on glm-5.3 | its JSON usage report |
| Hermes Agent | `HERMES_HOME` = a home of the mission whose `config.yaml` puts the model on the gateway and declares the MCP server | delivered 2026-11-15 | none documented |

Some traffic passes no base URL, by the harnesses' own documentation: Claude Code's fast-mode and WebFetch checks,
Codex with a ChatGPT sign-in, agy and Grok with an account sign-in, and dsh's search, fetch, and telemetry.

A harness's own cost figure is a client-side estimate. The gateway prices each call itself, and it uses a
server-side cost when the API gives one (xAI `cost_in_usd_ticks`, OpenRouter `usage.cost`).
