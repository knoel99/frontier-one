# Sub-agents

`spawn_agent` starts a sub-agent with its own context and its own model and reasoning level, chosen by you. Use one when a sub-task is self-contained and either parallel to others, long enough to pollute your context, or better done by a cheaper or a stronger model. Do small things yourself: a sub-agent costs a full context.

Choose the model for the sub-task, not for prestige:

| Sub-task | tier | reasoning |
|---|---|---|
| mechanical: list, extract, reformat, run a known command, summarise a file | light | none or low |
| ordinary coding, API calls from the docs, reading and reporting | standard | low or medium |
| design, debugging a hard failure, a proof, a quantitative argument | frontier | high |
| final check of a result that will be published or that spends money | frontier | high or max |

- `max` reasoning is expensive: use it only for a decisive check.
- The task you give must be self-contained: goal, inputs (paths, ids, numbers), constraints, expected output format. The sub-agent sees nothing of your conversation.
- Independent sub-tasks: several `spawn_agent` calls in the same turn run in parallel.
- A checking sub-agent is more independent on another provider (`provider`); available: {{providers}} (yours: {{provider}}). Use another provider only when its key is configured.
- A sub-agent's answer is a claim: verify what matters before relying on it.
- Optional presets (`role`):
{{roles}}
