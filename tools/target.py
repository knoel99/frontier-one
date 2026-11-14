"""Where the race tools find the repository the teams work on (the target), and its tools.

The sciathon repository is the control plane of a race: configuration, prompts, launcher, host,
broker and judge. The teams work in a checkout of another repository, the target (today
knoel99/milady-2027). The target is, in order:

1. the environment variable SCIATHON_TARGET (a path to a checkout);
2. ../milady-2027 next to this repository.

The target provides agents/providers.toml, agents/roles/ and the tools a team uses inside its
checkout (call_model.py, run_role.py, check_calls.py, runpod_job.py, ...): `use_target_tools()`
puts its tools/ first on sys.path so that the race tools import them from there.

`install_brief(checkout)` copies race/ of this repository into the checkout, where the prompts
expect it (race/BRIEF.md, race/GOAL.md, ...). The target's .gitignore ignores these copies and keeps
race/teams/<team>/objectives.toml, the one file of race/ that a team writes. Standard library only.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent   # this repository (sciathon)
RACE = HOME / "race"


def target() -> Path:
    env = os.environ.get("SCIATHON_TARGET")
    path = Path(env).expanduser() if env else HOME.parent / "milady-2027"
    return path.resolve()


def use_target_tools() -> Path:
    t = target()
    if not (t / "agents" / "providers.toml").exists():
        sys.exit(f"no target checkout at {t}: set SCIATHON_TARGET to a checkout of milady-2027")
    tools = str(t / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return t


def install_brief(checkout: Path) -> list[str]:
    """Copy race/ (prompts, rules, configuration, diagrams) into CHECKOUT/race/; team files are kept."""
    out = []
    for src in sorted(RACE.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(RACE)
        dst = checkout / "race" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        out.append(f"race/{rel}")
    return out
