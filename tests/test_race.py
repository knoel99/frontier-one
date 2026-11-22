"""A race file becomes one mission file per runner, started, watched and stopped by one command each."""

from __future__ import annotations

import http.client
import json
import pathlib
import signal
import sys
import threading
import time
import tomllib

import pytest

from frontier import dashboard, mission, params, race
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
    r = tomllib.loads(EXAMPLE.read_text())
    r["runners"][0] |= {"secrets": {"OPENAI_API_KEY": "proxy"}, "skills": [{"repo": "r", "rev": "v", "path": "p"}], "scaleway": {}}
    assert tomllib.loads(race.dumps(r)) == r


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


def test_the_schema_holds_every_parameter_with_its_choices():
    rows = {r["key"]: r for r in json.loads(json.dumps(params.schema()))}
    offered = [p for p in params.PARAMS if p.form]
    assert list(rows) == [p.key for p in offered] and "llm.key_env" not in rows  # an older form: kept in a file, not offered
    assert all(rows[p.key]["choices"] == (p.choices() if p.choices else None) for p in offered)
    assert "codex" in rows["harness.name"]["choices"] and rows["llm.budget_usd"]["minimum"] == 0


@pytest.fixture
def setup_server(tmp_path, monkeypatch):
    """A dashboard on a free port editing tmp_path/race.toml (missing at first); req(method, path, body, cookie)."""
    (tmp_path / "stand_in.py").write_text(STAND_IN)
    monkeypatch.setattr(race, "LAUNCH", [sys.executable, str(tmp_path / "stand_in.py")])

    class Handler(dashboard._Handler):
        token, race_file, root = "s3cret-token", tmp_path / "race.toml", tmp_path / "root"

    srv = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    session = "frontier_dashboard=" + dashboard._session("s3cret-token")

    def req(method, path, body=None, cookie=session):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request(method, path, body=None if body is None else json.dumps(body), headers={"Cookie": cookie} if cookie else {})
        r = c.getresponse()
        data = r.read()
        return r.status, json.loads(data) if r.headers["Content-Type"] == "application/json" else data

    yield req, Handler
    srv.shutdown()


def a_race(tmp_path) -> dict:
    (tmp_path / "s.md").write_text("subject\n")
    return {"race": {"name": "r", "root": "root"}, "mission": {"subject": "s.md", "duration_hours": 1},
            "llm": {"budget_usd": 5.0}, "scaleway": {}, "secrets": {"OPENAI_API_KEY": "env:MY_KEY", "HF_TOKEN": "proxy"},
            "runners": [{"mission": {"name": "openai"}, "llm": {"provider": "openai", "budget_usd": 2.5}},
                        {"mission": {"name": "anthropic"}, "llm": {"provider": "anthropic"}, "harness": {"name": "claude"}}]}


def test_the_setup_routes_round_trip_a_race_and_refuse_a_bad_one_writing_nothing(tmp_path, setup_server):
    req, Handler = setup_server
    f = tmp_path / "race.toml"
    code, d = req("GET", "/api/setup")
    assert code == 200 and d["race"] is None and d["file"] == str(f) and d["services"] == ["scaleway", "runpod", "huggingface"]
    assert len(d["schema"]) == len([p for p in params.PARAMS if p.form]) and "frontier" in d["providers"]["openai"]
    posted = a_race(tmp_path)
    code, d = req("POST", "/api/setup", {"race": posted})
    assert code == 200 and d["problems"] == [] and d["race"] == posted == tomllib.loads(f.read_text())
    assert set(d["status"]) == {"openai", "anthropic"} and race.load(f)["race"]["root"] == str(tmp_path / "root")
    before = f.read_text()
    bad = a_race(tmp_path)
    bad["llm"]["budget_usd"] = -1
    del bad["runners"][1]["llm"]["provider"]
    code, d = req("POST", "/api/setup", {"race": bad})
    assert code == 400 and any("llm.budget_usd = -1" in p for p in d["problems"])
    assert any("anthropic: llm.provider is missing" in p for p in d["problems"]) and f.read_text() == before
    assert req("POST", "/api/setup", {"race": "x"})[0] == 400 and req("POST", "/api/setup", "x" * (300 * 1024))[0] == 400
    f.write_text(before.replace("budget_usd = 5.0", "budget_usd = -5.0"))  # a refused file shows as written, with its problems
    code, d = req("GET", "/api/setup")
    assert code == 200 and d["race"]["llm"]["budget_usd"] == -5.0 and any("below its minimum" in p for p in d["problems"])
    assert req("POST", "/api/race/start")[0] == 409 and f.read_text() == before.replace("5.0", "-5.0", 1)


def test_a_secret_that_runs_or_reads_on_the_host_is_refused_from_the_browser_and_kept_when_already_in_the_file(tmp_path, setup_server):
    req, _ = setup_server
    f = tmp_path / "race.toml"
    posted = a_race(tmp_path)
    posted["secrets"]["OPENAI_API_KEY"] = "cmd:cat ~/.ssh/id_rsa"
    code, d = req("POST", "/api/setup", {"race": posted})
    assert code == 400 and d["problems"] == ["secrets.OPENAI_API_KEY: only env:NAME or proxy can be set here; a file: or cmd: source "
                                             "is written by hand in the race file."] and not f.exists()
    by_hand = a_race(tmp_path) | {"secrets": {"OPENAI_API_KEY": "cmd:vault kv get -field=key secret/openai"}}
    by_hand["runners"][1]["secrets"] = {"ANTHROPIC_API_KEY": "file:/run/secrets/anthropic"}
    f.write_text(race.dumps(by_hand))
    by_hand["mission"]["duration_hours"] = 2
    code, d = req("POST", "/api/setup", {"race": by_hand})
    assert code == 200 and tomllib.loads(f.read_text()) == by_hand  # what the file held survives a save
    moved = json.loads(json.dumps(by_hand))
    moved["runners"][0]["secrets"] = moved["runners"][1].pop("secrets")  # the same source in another place
    code, d = req("POST", "/api/setup", {"race": moved})
    assert code == 400 and d["problems"][0].startswith("openai: secrets.ANTHROPIC_API_KEY:")


def test_the_setup_routes_need_a_session_and_a_race_file(tmp_path, setup_server):
    req, Handler = setup_server
    for method, path in [("GET", "/api/setup"), ("POST", "/api/setup"), ("POST", "/api/race/start"), ("POST", "/api/race/stop")]:
        assert req(method, path, {}, cookie="")[0] == 403
        assert req(method, path, {}, cookie="frontier_dashboard=1.forged")[0] == 403
    Handler.race_file = None
    for method, path in [("GET", "/api/setup"), ("POST", "/api/setup"), ("POST", "/api/race/start"), ("POST", "/api/race/stop")]:
        assert req(method, path, {})[0] == 404


def test_the_race_starts_and_stops_from_the_dashboard_and_its_file_is_frozen_while_it_runs(tmp_path, setup_server):
    req, _ = setup_server
    f, root = tmp_path / "race.toml", tmp_path / "root"
    assert req("POST", "/api/race/start")[1] == {"problems": ["no race file yet: save the race first."]}
    assert req("POST", "/api/setup", {"race": a_race(tmp_path)})[0] == 200
    code, d = req("POST", "/api/race/start")
    assert code == 200 and all(v.startswith("started") for v in d["result"].values())
    assert wait_for(lambda: all(s["running"] for s in req("GET", "/api/setup")[1]["status"].values()))
    before, changed = f.read_text(), a_race(tmp_path) | {"mission": {"subject": "s.md", "duration_hours": 3}}
    code, d = req("POST", "/api/setup", {"race": changed})
    assert code == 409 and "anthropic, openai running" in d["problems"][0] and f.read_text() == before
    code, d = req("POST", "/api/race/stop")
    assert code == 200 and all(v.startswith(("stopped", "still running")) for v in d["result"].values())  # no wait
    assert wait_for(lambda: not any(s["running"] for s in req("GET", "/api/setup")[1]["status"].values()))
    assert all((root / n / "marker").read_text() == "cleaned" for n in ("openai", "anthropic"))
    assert req("POST", "/api/setup", {"race": changed})[0] == 200
