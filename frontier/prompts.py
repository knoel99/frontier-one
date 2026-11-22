"""The prompts of `prompts/`, filled: every value the code owns reaches a prompt through a `{{name}}` placeholder."""

from __future__ import annotations

import re

from . import llm, milestones, scoring, tools
from .config import ROOT

CRITERIA = {"scientific_quality": "scientific quality", "artifacts": "code and data", "efficiency": "efficiency"}


def and_list(items) -> str:
    items = [str(x) for x in items]
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


def facts() -> dict[str, str]:
    return {
        "weights": ", ".join(f"{w:g} % {CRITERIA[k]}" for k, w in scoring.WEIGHTS.items()),
        "science_rubrics": ", ".join(f"{k} {w:g}" for k, w in scoring.SCIENCE_RUBRICS.items()),
        "artifact_rubrics": ", ".join(f"{k} {w:g}" for k, w in scoring.ARTIFACT_RUBRICS.items()),
        "adequacy": " | ".join(scoring.ADEQUACY),
        "milestones": and_list(milestones.MILESTONES),
        "classified_share": f"{scoring.CLASSIFIED_SHARE * 100:g}",
        "infra_tools": ", ".join(f"`{t}`" for t in tools.INFRA_TOOLS if t not in tools.RAW_INFRA_TOOLS),
        "levels": ", ".join(f"`{x}`" for x in llm.LEVELS),
    }


def render(name: str, /, **values) -> str:
    path = ROOT / "prompts" / f"{name}.md"
    text = path.read_text()
    table = {**facts(), **values}
    unknown = sorted(set(re.findall(r"\{\{(\w+)\}\}", text)) - set(table))
    if unknown:
        raise KeyError(f"{path}: no value for the placeholder(s) {', '.join('{{' + k + '}}' for k in unknown)}")
    return re.sub(r"\{\{(\w+)\}\}", lambda mt: str(table[mt.group(1)]), text)
