"""A mission file holds only the parameters of frontier/params.py, each of its kind; mission.load refuses any other."""

from __future__ import annotations

import tomllib

import pytest

from frontier import mission, params, snapshots
from frontier.config import ROOT

EXAMPLE = ROOT / "config" / "mission.example.toml"
GOOD = {"mission": {"subject": "s.md"}, "llm": {"provider": "openai"}}


def test_the_example_files_load_and_every_key_they_set_is_a_row():
    for f in (EXAMPLE, ROOT / "docs" / "diagrams" / "launch-sim" / "mission.toml"):
        assert mission.load(f)["_file"] == str(f)
    m = tomllib.loads(EXAMPLE.read_text())
    keys = {f"{s}.{k}" for s, body in m.items() if s != "secrets" for k in body}
    assert keys <= set(params.BY_KEY), keys - set(params.BY_KEY)


@pytest.mark.parametrize("bad, words", [
    ({"llm": {"provider": "openai", "budget_uds": 5}}, ["llm.budget_uds", "did you mean llm.budget_usd"]),
    ({"misson": {"name": "x"}}, ["[misson]", "did you mean mission"]),
    ({"llm": {"provider": "openai", "budget_usd": -5}}, ["llm.budget_usd", "minimum 0"]),
    ({"scaleway": {"budget_eur": "30"}}, ["scaleway.budget_eur", "not a number"]),
    ({"harness": {"max_sessions": True}}, ["harness.max_sessions", "not a whole number"]),
    ({"harness": {"name": "nosuch"}}, ["harness.name = 'nosuch'", "opencode"]),
    ({"llm": {"provider": "nosuch"}}, ["llm.provider = 'nosuch'", "openai"]),
    ({"llm": {"provider": "openai", "other_providers": ["nosuch"]}}, ["llm.other_providers = 'nosuch'", "anthropic"]),
    ({"mission": {"name": "x"}}, ["mission.subject is missing", "add subject to [mission]"]),
    ({"mission": {"subject": "s", "deadline": "tomorrow"}}, ["mission.deadline", "ISO time"]),
    ({"secrets": {"OPENAI_API_KEY": "vault:x"}}, ["secrets.OPENAI_API_KEY", "env:NAME, file:PATH, cmd:COMMAND or proxy"]),
    ({"skills": [{"repo": "https://x/y", "path": "s"}]}, ["[[skills]] #1 has no rev"]),
])
def test_a_bad_mission_is_refused_with_the_key_and_the_fix(bad, words):
    found = params.problems({**GOOD, **bad})
    assert len(found) == 1 and all(w in found[0] for w in words), found


def test_a_secondary_provider_may_name_its_key_variable_in_its_own_section():
    m = {**GOOD, "llm": {"provider": "openai", "other_providers": ["anthropic"]}}
    assert params.problems({**m, "anthropic": {"key_env": "MY_KEY"}}) == []
    assert "key_env or token_env" in params.problems({**m, "anthropic": {"budget_usd": 5}})[0]


def test_all_problems_of_a_file_come_in_one_error_with_the_file_first(tmp_path):
    f = tmp_path / "m.toml"
    f.write_text('[llm]\nbudget_usd = -1\n[harness]\ntier = "huge"\n')
    with pytest.raises(ValueError) as e:
        mission.load(f)
    lines = str(e.value).splitlines()
    assert lines[0] == f"{f}:" and len(lines) == 5, lines  # budget, tier, subject, provider


def test_the_defaults_of_the_table_are_those_of_the_code():
    assert {k: params.DEFAULTS[f"snapshots.{k}"] for k in snapshots.DEFAULTS} == snapshots.DEFAULTS
    bare = {"mission": {}, "llm": {"provider": "openai"}}
    assert mission.sandbox_kind(bare) == params.DEFAULTS["sandbox.kind"]
    assert all(c in mission.mission_brief(bare, {}, "s") for c in params.DEFAULTS["grading.criteria"])
