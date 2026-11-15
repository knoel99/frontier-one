"""Rules of the framework's code that a check enforces (AGENTS.md pairs each rule with its check). A rule is here
because the same mistake was fixed at least twice; its message names what to use instead."""

from __future__ import annotations

import ast
import pathlib

import pytest

FRONTIER = pathlib.Path(__file__).resolve().parent.parent / "frontier"


def _dotted(node: ast.AST) -> str:
    """`a.b.c` of a Name/Attribute chain; a call or subscript in the chain shows as `()`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return "()"


def _is_json(text: ast.AST) -> bool:
    """`json.dumps(...)`, alone or followed by `+ "\\n"`."""
    while isinstance(text, ast.BinOp):
        text = text.left
    return isinstance(text, ast.Call) and _dotted(text.func) == "json.dumps"


def violations(name: str, source: str) -> list[str]:
    """The rules a source file of frontier/ breaks, as `file:line: what to use instead`."""
    out = []
    shared_state = name != "budget.py"  # budget.py owns the locks and the JSON state files
    for node in ast.walk(ast.parse(source)):
        if shared_state and isinstance(node, (ast.Import, ast.ImportFrom)) and "fcntl" in ast.unparse(node):
            out.append(f"{name}:{node.lineno}: take a lock with budget.locked, and change a JSON state file with "
                       "budget.update_json; a hand-written lock was forgotten or left half-done three times")
        if not isinstance(node, ast.Call):
            continue
        called = _dotted(node.func)
        if shared_state and (called == "json.dump" or called.endswith(".write_text") and _is_json(node.args[0])):
            out.append(f"{name}:{node.lineno}: write a JSON file with budget.write_json (whole, then renamed): a "
                       "reader in another process must never see half a file")
        if called == "llm.chat" and name not in ("budget.py", "__main__.py"):  # __main__: `frontier ping`, no mission
            out.append(f"{name}:{node.lineno}: a model call under a budget goes through Budget.metered_chat, "
                       "which reserves its worst case and charges it; llm.chat alone is not charged")
        if called.endswith((".reserve", ".release")) and name != "budget.py":
            out.append(f"{name}:{node.lineno}: hold a model call with `with budget.call(...)`: it charges the call "
                       "before releasing its reservation, and charges the worst case when no path settled it")
    return out


@pytest.mark.parametrize("path", sorted(FRONTIER.glob("*.py")), ids=lambda p: p.name)
def test_the_code_follows_its_rules(path):
    assert violations(path.name, path.read_text()) == []
