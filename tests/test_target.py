"""The target checkout is consistent with the race configuration."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import target


def test_every_team_has_a_provider_and_is_shown_on_the_site():
    t = target.use_target_tools()
    teams = tomllib.loads((target.RACE / "config.toml").read_text())["teams"]
    providers = tomllib.loads((t / "agents" / "providers.toml").read_text())
    assert [k for k in teams if k not in providers] == []
    site = tomllib.loads((t / "milady.toml").read_text()).get("site", {})
    assert site.get("race_teams") == list(teams), "milady.toml [site] race_teams differs from race/config.toml [teams]"


def test_install_brief_keeps_the_team_files(tmp_path):
    own = tmp_path / "race" / "teams" / "openai" / "objectives.toml"
    own.parent.mkdir(parents=True)
    own.write_text('team = "openai"\n')
    files = target.install_brief(tmp_path)
    assert "race/BRIEF.md" in files and "race/config.toml" in files
    assert own.read_text() == 'team = "openai"\n'
