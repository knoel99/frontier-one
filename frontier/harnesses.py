"""Harness adapters: the files each coding harness reads (instructions, MCP servers, sub-agents, skills), generated
from AGENTS.md, roles/ and the tool registry, so that one repository works with every harness.

Facts come from each harness's official documentation, read on 2026-11-14 (summary and sources in docs/HARNESSES.md;
points the docs leave open are marked UNCONFIRMED there). Two standards carry most of the load: AGENTS.md (read
natively by almost every harness) and Agent Skills (.agents/skills/<name>/SKILL.md). The rest is one small file per
harness that declares the `frontier` MCP server.

Used by tools/sync_harnesses.py (this repository) and by the mission launcher (a mission workspace).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import llm
from .agent import ROLES, load_role
from .tools import REGISTRY


@dataclass
class McpCommand:
    """How a harness starts the frontier MCP server."""

    command: str = "python3"
    args: list[str] = field(default_factory=lambda: ["-m", "frontier.mcp"])

    @property
    def argv(self) -> list[str]:
        return [self.command, *self.args]


PROXY_VARS = ["HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"]


def mcp_env_vars() -> list[str]:
    """Names of the variables the frontier MCP server reads, for harnesses that pass a server only the variables they
    are told to: the outbound proxy (which injects a key whose source is `proxy`), and every key and setting of the
    services in config/providers.toml. Names only, never values."""
    from .config import load_config
    from .credentials import services

    names = {v for s in services(load_config()).values() for v in (s.env, *s.settings)}
    return PROXY_VARS + sorted(names)


@dataclass
class Harness:
    name: str
    title: str
    kind: str  # "vendor" | "community"
    provider: str  # the provider whose key it uses natively ("" = any)
    artifacts: list[str]  # files generated for it (keys of ARTIFACTS)
    user_config: str = ""  # MCP can only be declared in a user-level file: what the launcher must add, and where
    notes: str = ""


HARNESSES: dict[str, Harness] = {h.name: h for h in [
    Harness("claude", "Claude Code", "vendor", "anthropic",
            ["claude_md", "mcp_json", "claude_agents", "claude_skill"],
            notes="reads CLAUDE.md (which imports AGENTS.md); project MCP servers need a one-time approval"),
    Harness("codex", "Codex CLI", "vendor", "openai", ["codex_config", "codex_agents", "agents_skill"],
            notes="project .codex/config.toml is read for trusted projects only"),
    Harness("antigravity", "Antigravity CLI (agy)", "vendor", "google", ["agents_mcp", "shared_agents", "agents_skill"]),
    Harness("gemini", "Gemini CLI", "vendor", "google", ["gemini_settings", "gemini_agents", "agents_skill"],
            notes="replaced by Antigravity CLI for some accounts on 2026-06-18"),
    Harness("grok", "Grok Build", "vendor", "xai", ["grok_config", "grok_agents", "claude_skill"],
            notes="also loads .mcp.json and .claude/skills through its Claude compatibility"),
    Harness("kimi", "Kimi Code CLI", "vendor", "moonshot", ["kimi_mcp", "shared_agents", "agents_skill"],
            notes="stdio MCP servers run only in a trusted workspace (KIMI_CODE_TRUST_WORKSPACE=1 headless)"),
    Harness("qwen", "Qwen Code", "vendor", "alibaba", ["qwen_settings", "qwen_agents", "agents_skill", "qwen_skill"]),
    Harness("vibe", "Mistral Vibe", "vendor", "mistral", ["vibe_config", "vibe_agents", "agents_skill"],
            notes="project config is read in trusted folders only"),
    Harness("dsh", "DeepSeek Harness (dsh)", "vendor", "deepseek", ["dsh_patch", "agents_skill"],
            notes="no project MCP file: pass `--patch .dsh/frontier.patch.yml` at launch"),
    Harness("copilot", "GitHub Copilot CLI", "vendor", "", ["github_mcp", "mcp_json", "copilot_agents", "agents_skill"]),
    Harness("cursor", "Cursor (IDE and CLI)", "vendor", "", ["cursor_mcp", "claude_agents", "codex_agents", "agents_skill"],
            notes="reads .claude/agents and .codex/agents as its own sub-agents"),
    Harness("opencode", "OpenCode", "community", "", ["opencode", "opencode_agents", "agents_skill"]),
    Harness("zcode", "ZCode Agent CLI (Z.ai)", "vendor", "zhipu", ["agents_skill"],
            user_config="~/.zcode/cli/config.json: mcp.servers.frontier {type: stdio, command, args}",
            notes="its project config may not declare MCP servers; the mission gives it a home of its own"),
    Harness("hermes", "Hermes Agent (Nous Research)", "community", "", ["agents_skill"],
            user_config="~/.hermes/config.yaml: mcp_servers.frontier {command, args}",
            notes="no project MCP file; delegation through its delegate_task tool"),
    Harness("goose", "Goose", "community", "", ["shared_agents", "agents_skill"],
            user_config="~/.config/goose/config.yaml: extensions.frontier {type: stdio, cmd, args}"),
    Harness("crush", "Crush", "community", "", ["crushrc", "agents_skill"]),
    Harness("kilo", "Kilo Code", "community", "", ["kilo", "kilo_agents"]),
    Harness("devin", "Devin Desktop / Windsurf", "community", "", ["devin_mcp", "shared_agents", "agents_skill"]),
    Harness("amp", "Amp", "community", "", ["amp_settings", "agents_skill"],
            notes="workspace MCP servers need `amp mcp approve frontier`"),
    Harness("continue", "Continue", "community", "", ["continue_mcp", "continue_rules"]),
    Harness("zed", "Zed agent", "community", "", ["zed_settings", "agents_skill"],
            notes="project-level context_servers UNCONFIRMED; a .rules file would override AGENTS.md"),
    Harness("cline", "Cline", "community", "", ["cline_rules", "agents_skill"],
            user_config="~/.cline/data/settings/cline_mcp_settings.json: mcpServers.frontier {command, args}"),
    Harness("openhands", "OpenHands", "community", "", ["agents_skill"],
            user_config="~/.openhands/settings.json or its UI: MCP server frontier (stdio)"),
    Harness("aider", "Aider", "community", "", ["aider_conf"],
            notes="no MCP client: tools through the shell fallback `frontier call` described in AGENTS.md"),
]}

# Native sub-agent model names where the harness documents per-agent model and effort fields.
CLAUDE_ALIAS = {"frontier": "fable", "standard": "sonnet", "light": "haiku"}


def _j(obj) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _toml_list(xs) -> str:
    return "[" + ", ".join(json.dumps(x) for x in xs) + "]"


def _roles() -> list[dict]:
    out = []
    for p in sorted(ROLES.glob("*.md")):
        r = load_role(p.stem)
        r["name"] = p.stem
        out.append(r)
    return out


def _md_agent(front: dict, body: str) -> str:
    lines = ["---"]
    for k, v in front.items():
        if v is None or v == "":
            continue
        lines.append(f"{k}: {json.dumps(v) if isinstance(v, (list, dict)) or ':' in str(v) else v}")
    return "\n".join(lines + ["---", "", body.strip(), ""])


SKILL_DESCRIPTION = ("Frontier tools - sub-agents on any model API with a chosen tier, reasoning level and speed mode; "
                     "Scaleway and Runpod GPU/CPU machines under a budget; budget and time status; Hugging Face "
                     "publishing; mission delivery. Use it when you need compute, another model, the spend so far, or "
                     "to publish or deliver.")


def skill_text() -> str:
    tools = "\n".join(f"- `{n}`: {t.description.split('. ')[0].rstrip('.')}." for n, t in REGISTRY.items()
                      if n not in ("shell", "read_file", "write_file", "edit_file", "http_request"))
    return f"""---
name: frontier
description: {SKILL_DESCRIPTION}
---

# Frontier tools

They are served by the `frontier` MCP server of this repository. When the harness has no MCP client, or the server
is not connected, call any of them from the shell with the same arguments:

```bash
frontier call <tool> '<json arguments>'          # bin/frontier of the Frontier repository; `frontier call list` lists them
```

{tools}

Prices, models, speed modes, compute offers and budgets of the current mission: TOOLS.md at the workspace root.
"""


# AI SDK packages of OpenCode's custom providers (`provider.<id>.npm`). openai-compatible checked live with OpenCode
# 1.19.5 on 2026-11-14 (its newer `providers` section with `package` was ignored: "Model not found").
OPENCODE_PACKAGE = {"openai-chat": "@ai-sdk/openai-compatible", "openai-responses": "@ai-sdk/openai",
                    "anthropic": "@ai-sdk/anthropic", "gemini": "@ai-sdk/google"}


def artifacts(mcp: McpCommand, which: list[str] | None = None, target: dict | None = None) -> dict[str, str]:
    """{relative path: content} for the given harnesses (default: all). `target` (a mission: provider, model,
    gateway, key_env) adds what routes a harness's own model calls through the metering gateway."""
    names = which or list(HARNESSES)
    keys: list[str] = []
    for n in names:
        keys += [a for a in HARNESSES[n].artifacts if a not in keys]
    out: dict[str, str] = {}
    roles = _roles()
    server = {"command": mcp.command, "args": mcp.args}
    for key in keys:
        if key == "claude_md":
            out["CLAUDE.md"] = "@AGENTS.md\n"
        elif key == "mcp_json":
            out[".mcp.json"] = _j({"mcpServers": {"frontier": {"type": "stdio", **server}}})
        elif key == "github_mcp":
            out[".github/mcp.json"] = _j({"mcpServers": {"frontier": {"type": "local", **server, "tools": ["*"]}}})
        elif key == "codex_config":  # Codex starts MCP servers with a short list of variables: name the ones they need
            out[".codex/config.toml"] = (f'[mcp_servers.frontier]\ncommand = "{mcp.command}"\nargs = {_toml_list(mcp.args)}\n'
                                         f"env_vars = {_toml_list(mcp_env_vars())}\n"
                                         # headless, approval policy never: a tool needing approval would be refused.
                                         # The framework itself guards spending (caps, cloud lock, ownership).
                                         'default_tools_approval_mode = "approve"\ntool_timeout_sec = 3600\n')
        elif key == "agents_mcp":
            out[".agents/mcp_config.json"] = _j({"mcpServers": {"frontier": {**server, "timeoutSeconds": 3600}}})
        elif key == "gemini_settings":
            out[".gemini/settings.json"] = _j({"context": {"fileName": ["AGENTS.md", "GEMINI.md"]},
                                               "mcpServers": {"frontier": {**server, "timeout": 3600000}}})
        elif key == "grok_config":
            out[".grok/config.toml"] = (f'[mcp_servers.frontier]\ncommand = "{mcp.command}"\nargs = {_toml_list(mcp.args)}\n'
                                        "tool_timeout_sec = 3600\n")
        elif key == "kimi_mcp":
            out[".kimi-code/mcp.json"] = _j({"mcpServers": {"frontier": server}})
        elif key == "qwen_settings":
            out[".qwen/settings.json"] = _j({"context": {"fileName": ["AGENTS.md", "QWEN.md"]},
                                             "mcpServers": {"frontier": {**server, "timeout": 3600000}}})
        elif key == "vibe_config":
            out[".vibe/config.toml"] = ('[[mcp_servers]]\nname = "frontier"\ntransport = "stdio"\n'
                                        f'command = "{mcp.command}"\nargs = {_toml_list(mcp.args)}\ntool_timeout_sec = 3600\n')
            if target and target.get("model"):  # Vibe 3.1 knows its own model list only, and calls a provider's api_base
                m = target["model"]
                out[".vibe/config.toml"] += (
                    f'\n[[providers]]\nname = "frontier"\napi_base = "{target["base_url"]}"\n'
                    f'api_key_env_var = "{target["key_env"]}"\napi_style = "openai"\n'
                    f'\n[models."{m}"]\nname = "{m}"\nprovider = "frontier"\nalias = "{m}"\n')
        elif key == "dsh_patch":
            args = ", ".join(f"'{a}'" for a in mcp.args)
            out[".dsh/frontier.patch.yml"] = (
                "# Pass to dsh at launch: dsh --patch .dsh/frontier.patch.yml (dsh has no project MCP file).\n"
                "- insert:\n    - id: mcp-frontier\n      name: '@deepseek-ai/dsh-mcp-client'\n      config:\n"
                f"        serverName: frontier\n        transport: stdio\n        command: {mcp.command}\n"
                f"        args: [{args}]\n        env: {{}}\n")
            if target and target.get("model"):  # dsh reads its model here only: DSH_MODEL is ignored (dsh 0.2.2)
                out[".dsh/frontier.patch.yml"] += (
                    "- id: agent-default-model\n  config:\n    provider: deepseek-official\n"
                    f"    model: {target['model']}\n"
                    + (f"    reasoningEffort: {target['effort']}\n" if target.get("effort") else ""))
        elif key == "cursor_mcp":
            out[".cursor/mcp.json"] = _j({"mcpServers": {"frontier": {"type": "stdio", **server}}})
        elif key == "opencode":
            conf = {"$schema": "https://opencode.ai/config.json",
                    "mcp": {"servers": {"frontier": {"type": "local", "command": mcp.argv}}}}
            if target:
                conf["provider"] = {"frontier": {
                    "npm": OPENCODE_PACKAGE.get(target["style"], OPENCODE_PACKAGE["openai-chat"]), "name": "Frontier gateway",
                    "options": {"baseURL": target["base_url"],
                                "apiKey": "{env:" + target["key_env"] + "}"},
                    "models": {target["model"]: {}}}}
            out["opencode.json"] = _j(conf)
        elif key == "kilo":
            out[".kilo/kilo.json"] = _j({"mcp": {"frontier": {"type": "local", "command": mcp.argv, "enabled": True,
                                                              "timeout": 3600000}}})
        elif key == "crushrc":
            args = " ".join(f"--args {a}" for a in mcp.args)
            out[".crushrc"] = f"# Crush project configuration (bash with builtins)\nmcp add frontier --command {mcp.command} {args}\n"
        elif key == "devin_mcp":
            out[".devin/mcp_config.json"] = _j({"mcpServers": {"frontier": server}})
        elif key == "amp_settings":
            out[".amp/settings.json"] = _j({"amp.mcpServers": {"frontier": server}})
        elif key == "continue_mcp":
            args = ", ".join(json.dumps(a) for a in mcp.args)
            out[".continue/mcpServers/frontier.yaml"] = (f"name: Frontier\nversion: 0.0.1\nschema: v1\nmcpServers:\n"
                                                         f"  - name: frontier\n    command: {mcp.command}\n    args: [{args}]\n")
        elif key == "continue_rules":
            out[".continue/rules/agents.md"] = ("---\nname: AGENTS.md\nalwaysApply: true\n---\n"
                                                "Read and follow AGENTS.md at the repository root.\n")
        elif key == "zed_settings":
            out[".zed/settings.json"] = _j({"context_servers": {"frontier": {**server, "env": {}}}})
        elif key == "cline_rules":
            out[".clinerules/agents.md"] = "Read and follow AGENTS.md at the repository root.\n"
        elif key == "aider_conf":
            out[".aider.conf.yml"] = "# Aider reads no instruction file by itself.\nread:\n  - AGENTS.md\n"
        elif key == "agents_skill":
            out[".agents/skills/frontier/SKILL.md"] = skill_text()
        elif key == "claude_skill":
            out[".claude/skills/frontier/SKILL.md"] = skill_text()
        elif key == "qwen_skill":
            out[".qwen/skills/frontier/SKILL.md"] = skill_text()
        elif key.endswith("_agents"):
            for r in roles:
                out.update(_agent_files(key, r))
    return out


def _agent_files(key: str, r: dict) -> dict[str, str]:
    name, desc, body = r["name"], r.get("description", ""), r["instructions"]
    tier, level = r.get("tier", "standard"), r.get("reasoning", "medium")
    if key == "claude_agents":
        front = {"name": name, "description": desc, "model": CLAUDE_ALIAS.get(tier, "inherit"),
                 "effort": level if level != "none" else "low"}
        return {f".claude/agents/{name}.md": _md_agent(front, body)}
    if key == "codex_agents":
        from .config import load_config

        cfg = load_config()
        spec = llm.resolve(cfg, "openai", tier, level)
        text = (f'name = "{name}"\ndescription = {json.dumps(desc)}\nmodel = "{spec.model}"\n'
                f'model_reasoning_effort = "{spec.effort_value}"\ndeveloper_instructions = """\n{body.strip()}\n"""\n')
        return {f".codex/agents/{name}.toml": text}
    if key == "shared_agents":  # Antigravity, Kimi Code, Goose, Devin: portable subset (model inherited)
        return {f".agents/agents/{name}.md": _md_agent({"name": name, "description": desc}, body)}
    if key == "gemini_agents":
        return {f".gemini/agents/{name}.md": _md_agent({"name": name, "description": desc, "kind": "local"}, body)}
    if key == "grok_agents":
        return {f".grok/agents/{name}.md": _md_agent({"name": name, "description": desc}, body)}
    if key == "qwen_agents":
        return {f".qwen/agents/{name}.md": _md_agent({"name": name, "description": desc, "model": "inherit"}, body)}
    if key == "vibe_agents":
        text = (f'agent_type = "subagent"\ndisplay_name = "{name}"\ndescription = {json.dumps(desc)}\n'
                f'instructions = """\n{body.strip()}\n"""\n')
        return {f".vibe/agents/{name}.toml": text}
    if key == "copilot_agents":
        return {f".github/agents/{name}.agent.md": _md_agent({"name": name, "description": desc}, body)}
    if key == "opencode_agents":
        return {f".opencode/agents/{name}.md": _md_agent({"description": desc, "mode": "subagent"}, body)}
    if key == "kilo_agents":
        return {f".kilo/agents/{name}.md": _md_agent({"description": desc, "mode": "subagent"}, body)}
    return {}
