# Harness compatibility

What each coding harness reads, what `tools/sync_harnesses.py` (this repository) and the mission launcher (a mission
workspace) generate for it, and how the launcher runs it. Read in each harness's official documentation on
2026-11-14; UNCONFIRMED marks what the docs leave open. The generator is `frontier/harnesses.py`; launch recipes
are in `config/harnesses.toml`.

## The common layer

| Standard | Files | Read by |
|---|---|---|
| AGENTS.md | `AGENTS.md` | natively by Codex, Antigravity, Grok Build, Kimi Code, Qwen Code, Mistral Vibe (root: UNCONFIRMED), dsh, Copilot CLI, Cursor, OpenCode, Hermes, Goose, Crush, Kilo, Devin/Windsurf, Amp, Zed, OpenHands; Claude Code through `CLAUDE.md` = `@AGENTS.md`; Gemini CLI through `context.fileName`; Aider through `read:`; Cline and Continue through a one-line rule |
| MCP (stdio) | one small file per harness declaring the `frontier` server | every harness except Aider |
| Agent Skills | `.agents/skills/frontier/SKILL.md` (and `.claude/skills/` for Claude Code and Grok) | Codex, Antigravity, Gemini CLI, Kimi, Qwen, Vibe, dsh, Copilot, Cursor, OpenCode, Hermes, Goose, Crush, Cline, Zed, OpenHands, Amp, Devin |
| Sub-agents | `roles/*.md` turned into each harness's format | see the table |

A harness without MCP, or a session whose server is not connected, reaches the same tools from the shell:
`bin/frontier call <tool> '<json>' [--state <workspace>/.frontier]`.

## Per harness

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

Roo Code was archived on 2026-05-15 and is not covered.

## Sub-agents on the right model and effort

Native sub-agent files can set the model and the reasoning effort only where the harness documents those fields
(Claude Code, Codex, Cursor, OpenCode's `#variant`); elsewhere a native sub-agent inherits the main model. The
`spawn_agent` tool of the MCP server works the same in every harness: provider, tier, reasoning level and speed mode
per call, on the model API, charged to the mission's budget.

## Launching (missions)

`config/harnesses.toml` holds for each harness its install and setup commands, its headless command, its
environment, how its own LLM traffic is routed through the metering gateway, and how it reports usage. The vendor
commands ran headless on 2026-11-13 in the previous campaign (`old/race/PROVIDERS-TESTED.md` at commit 7223bd6, in git history). A provider without its
own harness gets OpenCode. Run `mission start --prepare-only` and one short session before relying on a recipe.

| Harness | Routed through the gateway by | Checked | Fallback when not metered |
|---|---|---|---|
| Claude Code | `ANTHROPIC_BASE_URL` | docs (code.claude.com llm-gateway) | `total_cost_usd` of `--output-format json` |
| Codex CLI | a custom provider passed with `-c model_providers.frontier.*` (Codex ignores `OPENAI_BASE_URL`; WebSockets off) | **run 2026-11-14**: a mission session metered, $0.004 | `turn.completed` usage of `--json` |
| Antigravity CLI | `GOOGLE_GEMINI_BASE_URL` (wire method UNCONFIRMED) | docs | `usage` of `--output-format json` |
| Gemini CLI | `GOOGLE_GEMINI_BASE_URL` | docs | `stats` of `--output-format json` |
| Grok Build | not routed: TLS-only connections documented; `GROK_XAI_API_BASE_URL` would be the variable | docs | its JSON report (UNCONFIRMED) |
| Kimi Code | `KIMI_MODEL_BASE_URL` (`KIMI_MODEL_PROVIDER_TYPE=kimi`) | docs | none documented |
| Qwen Code | `OPENAI_BASE_URL` | docs | `usage` of the result message |
| Mistral Vibe | not routed: only `[[providers]] api_base` in `~/.vibe/config.toml`, left to the user | docs | its JSON output |
| DeepSeek dsh | `DEEPSEEK_BASE_URL` (Anthropic Messages) | docs | none documented |
| OpenCode | a provider `frontier` in the mission's `opencode.json` (`baseURL`, `transport: http`) | docs, package names UNCONFIRMED | `--format json` |
| Hermes Agent | `HERMES_HOME` = a mission home whose `config.yaml` puts the model on the gateway and declares the MCP server | docs, flags UNCONFIRMED | none documented |

Traffic that no base URL catches (documented): Claude Code's fast-mode and WebFetch checks, Codex with a ChatGPT
sign-in, agy and Grok with an account sign-in, dsh's search, fetch and telemetry. Harnesses' own cost figures are
client-side estimates; the gateway prices calls itself and uses a server-side cost when the API gives one (xAI
`cost_in_usd_ticks`, OpenRouter `usage.cost`).
