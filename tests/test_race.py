"""A race file becomes one mission file per runner, started, watched and stopped by one command each."""

from __future__ import annotations

import pathlib
import signal
import sys
import time
import tomllib

import pytest

from frontier import dashboard, mission, race
from frontier.config import ROOT

EXAMPLE = ROOT / "config" / "race.example.toml"
SHARED = """[mission]
subject = "s.md"
duration_hours = 1
[llm]
budget_usd = 5.0
"""
RUNNERS = """[[runners]]
[runners.mission]
name = "openai"
[runners.llm]
provider = "openai"
budget_usd = 2.0
[[runners]]
[runners.mission]
name = "anthropic"
[runners.llm]
provider = "anthropic"
"""


def race_file(tmp_path, body: str | None = None) -> pathlib.Path:
    (tmp_path / "s.md").write_text("subject\n")
    f = tmp_path / "race.toml"
    f.write_text(f'[race]\nname = "r"\nroot = "{tmp_path / "root"}"\n' + SHARED + RUNNERS if body is None else body)
    return f


def test_the_example_loads_and_each_written_mission_loads():
    r = race.load(EXAMPLE)
    r["race"]["root"] = "/nonexistent/root"
    ms = race.missions(r)
    assert list(ms) == ["openai", "anthropic"]
    for name, m in ms.items():
        assert pathlib.Path(m["mission"]["subject"]) == ROOT / "config" / "subject" / "stage_milady_2027.pdf"
        assert (m["mission"]["state"], m["mission"]["workspace"]) == (f"/nonexistent/root/{name}", f"/nonexistent/root/{name}/ws")


def test_written_missions_load_and_the_dashboard_finds_the_runners(tmp_path):
    files = race.write(race.load(race_file(tmp_path)))
    root = tmp_path / "root"
    assert files == {n: root / n / "mission.toml" for n in ("openai", "anthropic")}
    for f in files.values():
        m = mission.load(f)
        assert m["mission"]["state"] == str(f.parent) and m["mission"]["workspace"] == str(f.parent / "ws")
    mission.prepare(files["openai"], ping=False, offers=False)
    assert [t["name"] for t in dashboard.snapshot(root)["teams"]] == ["openai"]
    assert (root / "openai" / "ws" / "subject" / "s.md").exists()


def test_a_runner_overrides_a_shared_key_and_a_shared_key_reaches_every_runner(tmp_path):
    ms = race.missions(race.load(race_file(tmp_path)))
    assert ms["openai"]["llm"] == {"budget_usd": 2.0, "provider": "openai"}
    assert ms["anthropic"]["llm"] == {"budget_usd": 5.0, "provider": "anthropic"}
    assert all(m["mission"]["duration_hours"] == 1 for m in ms.values())


@pytest.mark.parametrize("body, words", [
    (SHARED + RUNNERS, ["race.name is missing"]),
    ('[race]\nname = "r"\nrot = "x"\n' + SHARED + RUNNERS, ["race.rot", "did you mean root"]),
    ('[race]\nname = "a/b"\n' + SHARED + RUNNERS, ["'a/b'", "not a folder name"]),
    ('[race]\nname = "r"\n' + SHARED, ["no runner", "[[runners]]"]),
    ('[race]\nname = "r"\n' + SHARED + '[[runners]]\n[runners.llm]\nprovider = "openai"\n', ["#1 has no mission.name"]),
    ('[race]\nname = "r"\n' + SHARED + RUNNERS.replace('"anthropic"', '"openai"'), ["#2", "two runners are named 'openai'"]),
    ('[race]\nname = "r"\n' + SHARED + '[[runners]]\n[runners.mission]\nname = "../x"\n', ["'../x'", "not a folder name"]),
    ('[race]\nname = "r"\n' + SHARED + RUNNERS.replace("budget_usd = 2.0", "budget_uds = 2.0"),
     ["openai: llm.budget_uds", "did you mean llm.budget_usd"]),
    ('[race]\nname = "r"\n' + SHARED + RUNNERS.replace('provider = "anthropic"', ""), ["anthropic: llm.provider is missing"]),
    ('[race]\nname = "r"\n' + SHARED.replace("[llm]", 'workspace = "w"\n[llm]') + RUNNERS,
     ["mission.workspace in the shared [mission]", "[runners.mission]"]),
])
def test_a_bad_race_file_is_refused_with_its_fix(tmp_path, body, words):
    f = race_file(tmp_path, body)
    with pytest.raises(mission.MissionFileError) as e:
        race.load(f)
    assert str(e.value).startswith(f"{f}:")
    for w in words:
        assert w in str(e.value)


def test_a_mission_survives_the_toml_round_trip():
    m = tomllib.loads((ROOT / "config" / "mission.example.toml").read_text())
    assert tomllib.loads(race.dumps(m)) == m
    odd = {"mission": {"name": 'q"uote\\back\nline\tt\x7f é 😀', "duration_hours": 1.5e-05, "n": -3},
           "secrets": {"OPENAI_API_KEY": "cmd:vault kv get -field=key secret/race/openai", "HF_TOKEN": "proxy"},
           "network": {"allow": [], "deny": ["a.com/x"]}, "grading": {"weights": [50, 25.5, 0]},
           "snapshots": {"enabled": False},
           "skills": [{"repo": "https://github.com/a/b", "rev": "abc", "path": "skills/x"}, {"repo": "r", "rev": "v", "path": "p"}]}
    assert tomllib.loads(race.dumps(odd)) == odd


STAND_IN = """import pathlib, signal, sys, time
signal.signal(signal.SIGINT, signal.default_int_handler)
f = pathlib.Path(sys.argv[-1]).parent / "marker"
f.write_text("running")
try:
    time.sleep(60)
except KeyboardInterrupt:
    f.write_text("cleaned")
"""


def wait_for(cond, s: float = 20) -> bool:
    end = time.time() + s
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_start_status_and_stop_drive_every_runner(tmp_path):
    (tmp_path / "stand_in.py").write_text(STAND_IN)
    r, root = race.load(race_file(tmp_path)), tmp_path / "root"
    argv = [sys.executable, str(tmp_path / "stand_in.py")]
    t0 = time.time()
    started = race.start(r, argv)
    assert time.time() - t0 < 5 and all(v.startswith("started") for v in started.values())
    assert wait_for(lambda: all((root / n / "marker").exists() for n in started))
    assert all(s["running"] and not s["delivered"] for s in race.status(r).values())
    assert all(v.startswith("already running") for v in race.start(r, argv).values())
    assert race.stop(r, wait_s=20) == {"openai": "stopped", "anthropic": "stopped"}
    assert all((root / n / "marker").read_text() == "cleaned" for n in started)
    assert not any(s["running"] for s in race.status(r).values())
    assert race.stop(r, wait_s=0) == {"openai": "was not running", "anthropic": "was not running"}
    (root / "openai" / "delivery.json").write_text("{}")
    assert race.start(r, argv)["openai"] == "delivered: not started again"
    race.stop(r, wait_s=20)


DRIVER = """import pathlib, sys
from frontier import launcher, mission
launcher.HARNESS_CONF = pathlib.Path(sys.argv[1])
mission._offers_section = lambda ctx: ""
launcher.start(sys.argv[2], ping=False)
"""


def test_the_real_launcher_stopped_by_the_race_runs_its_finish_even_started_with_sigint_ignored(tmp_path):
    (tmp_path / "harnesses.toml").write_text(
        '[codex]\ncommand = ["python3", "-c", "import pathlib, time; pathlib.Path(\'harness-up\').touch(); time.sleep(60)"]\n')
    f = race_file(tmp_path, f'[race]\nname = "r"\nroot = "{tmp_path / "root"}"\n' + SHARED
                  + '[[runners]]\n[runners.mission]\nname = "openai"\n[runners.llm]\nprovider = "openai"\n'
                  + '[runners.harness]\nname = "codex"\n')
    r, ws = race.load(f), tmp_path / "root" / "openai" / "ws"
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)  # inherited by the launcher, as from a background shell
    try:
        race.start(r, [sys.executable, "-c", DRIVER, str(tmp_path / "harnesses.toml")])
    finally:
        signal.signal(signal.SIGINT, old)
    log = tmp_path / "root" / "openai" / "launcher.log"
    assert wait_for(lambda: (ws / "harness-up").exists(), 60), log.read_text()
    assert race.stop(r, wait_s=30) == {"openai": "stopped"}, log.read_text()
    assert (ws / "COSTS.md").exists() and "not delivered; COSTS.md written" in log.read_text()
