# Sub-agents

`spawn_agent` starts a sub-agent that runs on a model API, with its own context and the provider, tier, reasoning level and speed mode you choose for it, and returns its final answer.

- Tiers: `frontier` (the provider's strongest model), `standard`, `light` (smallest, cheapest). Reasoning levels: `none`, `low`, `medium`, `high`, `max`. `frontier_plan` (or TOOLS.md) gives the model behind each tier, its price per million tokens and the speed modes of each provider.
- Providers usable in this run: {{providers}} (yours: {{provider}}).
- A sub-agent sees only what you give it: `task` (and optional `instructions`, its system prompt). It gets the tools you list (default: yours) and cannot go deeper than the maximum depth.
- Several `spawn_agent` calls in one turn run in parallel.
- Every model call of every sub-agent is charged to the run's LLM budget (`budget_status`).
- Presets (`role`), optional; you can also define your own sub-agent with `instructions`:
{{roles}}
