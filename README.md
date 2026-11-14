# sciathon

A framework where AI agents run a science hackathon (a *sciathon*) to push the frontier of science: several teams, one per AI provider, each with its provider's coding harness, the same brief and the same budgets, work on one research repository and are judged on the same objectives.

This repository is the **control plane**. The research itself happens in a separate **target** repository; today it is [`knoel99/milady-2027`](https://github.com/knoel99/milady-2027) (physics-constrained generative models of dynamical systems, an M2 internship). The race was moved here from `race/` of that repository on 2026-11-14 (milady-2027 at `93e6e69`, where its history stays).

| Path | What it holds |
|---|---|
| `race/` | the brief, goal and prompts of the teams, the deliverables, the common objectives, the pilots, the host and isolation runbooks, the diagrams, and `config.toml` (teams and provider keys, budgets, machines, model tier and effort of every team's main session, deadline) |
| `tools/` | `race_launch.py` (plan, launch, collect, run one team iteration), `race_host.py` (one VM shared by several teams), `race_progress.py` (lock and measure the objectives: the judge), `race_snapshot.py` (a team's clean starting repository), `compute_broker.py` (per-team CPU quota and run cache), `cursor_agents.py` (the Cursor pilot), `target.py` (where the target is) |
| `infra/deploy/` | the shell side: team VM user data, shared host, team loop, broker job VM, `racectl` |
| `tests/` | the tests of the tools above, run against a checkout of the target |

## How it drives the target

- The target checkout is `SCIATHON_TARGET`, or `../milady-2027` next to this repository.
- The race tools import the target's own tools (`call_model.py`, `run_role.py`, `check_calls.py`) and read its `agents/providers.toml` (which model and effort each provider's harness and each role use) and `agents/roles/`.
- `python3 tools/race_launch.py install-brief` copies `race/` into the target checkout, where the prompts expect it; the target's `.gitignore` ignores these copies and keeps the one file a team writes there, `race/teams/<team>/objectives.toml`.
- On a team machine, `SCIATHON_HOME` names this repository's clone, so a team runs `python3 "$SCIATHON_HOME"/tools/race_progress.py check` or `python3 "$SCIATHON_HOME"/tools/compute_broker.py submit …` from its checkout.

```bash
git clone https://github.com/knoel99/milady-2027.git ../milady-2027
python3 tools/race_launch.py plan                   # harness, models and efforts of every team and role
python3 -m pytest tests                             # needs the target checkout
```

The runbook of the full race is [`race/README.md`](race/README.md); a single team on a Claude Code cloud session is [`race/PILOT.md`](race/PILOT.md); several teams on one VM is [`race/HOST.md`](race/HOST.md).

## Still in the target

Each team's work stays where it is published: `research/teams/<team>/` and `site/teams/<team>/` of milady-2027, with the tools a team uses inside its checkout (`runpod_job.py`, `usage_report.py`, `review_due.py`, `check_deliverables.py`, `teams_index.py`). Moving more of the provider-specific configuration here (for instance the harness entries of `agents/providers.toml`) is the next step.
