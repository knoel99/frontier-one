"""Every test starts outside any mission: the FRONTIER_* variables of the environment (set for a harness inside a
mission, such as FRONTIER_STATE) would point the tests at that mission's real state and ledgers."""

from __future__ import annotations

import os

import pytest

from frontier.config import load_config


@pytest.fixture(autouse=True)
def _outside_any_mission(monkeypatch):
    for name in [n for n in os.environ if n.startswith("FRONTIER_")]:
        monkeypatch.delenv(name)


@pytest.fixture
def cfg():
    return load_config()
