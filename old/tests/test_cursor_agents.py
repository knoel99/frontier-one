"""tools/cursor_agents.py: a Cloud Agent is launched with the model and effort of its role, and logged."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def test_cursor_launch_uses_role_model_and_logs(tmp_path, monkeypatch):
    import cursor_agents as ca
    monkeypatch.setattr(ca, "LEDGER", tmp_path / "runs.jsonl")
    monkeypatch.setattr(ca, "STATE", tmp_path / "state.json")
    sent = []

    class Fake(ca.API):
        def __init__(self):
            pass

        def call(self, method, path, body=None):
            if path == "/models":
                return {"items": [{"id": "grok-4.7", "parameters": [
                    {"id": "fast", "values": [{"value": "true"}, {"value": "false"}]},
                    {"id": "effort", "values": [{"value": v} for v in ("low", "medium", "high", "xhigh")]}]},
                    {"id": "composer-2.5", "parameters": [{"id": "fast", "values": ["true", "false"]}]}]}
            sent.append((method, path, body))
            return {"agent": {"id": "bc-1"}, "run": {"id": "run-1"}}
    task = tmp_path / "t.md"
    task.write_text("do it")
    ns = argparse.Namespace(task=str(task), role="physicien", ref="main", name=None, allow_dropped=False)
    assert ca.cmd_launch(Fake(), ns) == 0
    body = sent[0][2]
    assert body["model"] == {"id": "grok-4.7", "params": [{"id": "effort", "value": "high"}, {"id": "fast", "value": "true"}]}
    assert body["repos"][0]["url"] == "https://github.com/knoel99/milady-2027"
    row = json.loads((tmp_path / "runs.jsonl").read_text())
    assert row["agent"] == "bc-1" and row["run"] == "run-1" and row["dropped"] == []
    assert ca.cmd_check(Fake()) == 0
