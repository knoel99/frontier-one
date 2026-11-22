"""The prompts take every value the code owns from the code, through the placeholders of frontier/prompts.py."""

from __future__ import annotations

import json
import re

import pytest

from frontier import agent, jury, llm, mission, prompts, scoring

M = {"mission": {"name": "x"}, "llm": {"provider": "openai"}}


def _brief(**snapshots) -> str:
    return mission.mission_brief({**M, "snapshots": snapshots} if snapshots else M, {}, "subject/s.pdf")


def test_every_prompt_renders_completely(run):
    system = agent.Agent(run, "openai", "frontier", "high").messages[0]["content"]
    assert "# Cloud infrastructure" in system and "# Sub-agents" in system
    for text in (system, _brief(), prompts.render("jury")):
        assert "{{" not in text


def test_an_unknown_placeholder_is_refused_with_its_prompt_named(monkeypatch, tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "t.md").write_text("{{weights}} {{nobody}}")
    monkeypatch.setattr(prompts, "ROOT", tmp_path)
    with pytest.raises(KeyError, match=r"t\.md.*\{\{nobody\}\}"):
        prompts.render("t")
    assert prompts.render("t", nobody="{{x}}").endswith(" {{x}}")


def test_the_jury_answer_template_passes_validate():
    text = prompts.render("jury")
    answer = json.loads(re.search(r"```json\n(.*?)```", text, flags=re.S).group(1))
    answer["scientific_quality"]["adequacy"] = next(iter(scoring.ADEQUACY))
    jury.validate(answer)
    prose = text.split("```json")[0]
    assert all(f"`{k}`" in prose for k in scoring.ADEQUACY)


def test_the_subagents_prompt_names_every_tier():
    text = prompts.render("subagents", providers="openai", provider="openai", roles="")
    assert all(f"`{t}`" in text for t in llm.TIERS)


def test_the_brief_states_the_snapshots_of_its_own_mission():
    default = _brief()
    assert "15 minutes on, a notice" in default and "(5, 15 and 30 minutes" in default
    assert "5 minutes on" not in default.replace("15 minutes on", "")
    assert "(10 and 20 minutes and so on)" in _brief(time_minutes=[10, 20])
    assert "snapshot" not in _brief(enabled=False)


def test_a_constant_changed_in_the_code_changes_the_prompts(monkeypatch):
    monkeypatch.setattr(scoring, "WEIGHTS", {"scientific_quality": 60.0, "artifacts": 25.0, "efficiency": 15.0})
    assert "60 % scientific quality, 25 % code and data, 15 % efficiency" in prompts.render("jury")
    assert "(weight 60)" in _brief() and "(weight 15)" in _brief()
