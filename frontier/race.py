"""A race: one file holding the missions of every team, written as one mission file per runner and started at once.

    python3 -m frontier race write|start|stop|status race.toml

A race file is a mission file (every section holds what all runners share) plus [race] and one [[runners]] per team
holding the sections that differ. A runner's mission is the shared sections with the runner's merged over them key by
key. Each runner lives in <root>/<runner name>/: mission.toml, launcher.log, and by default its state and ws/.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import tomllib

from . import budget, params
from .config import ROOT
from .mission import MissionFileError

RACES = pathlib.Path.home() / ".frontier" / "races"
RACE_KEYS = ["name", "root"]
LAUNCH = [sys.executable, "-m", "frontier", "mission", "start"]
SAFE_NAME = re.compile(r"[\w.-]+")  # what the dashboard accepts as a team folder (dashboard._team_dir)


def load(path: str | os.PathLike) -> dict:
    """The parsed race file, with `_file` and `race.root` made absolute; every problem raised at once."""
    path = pathlib.Path(path).resolve()
    try:
        r = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise MissionFileError(f"{path}: not valid TOML: {e}") from e
    r["_file"] = str(path)
    out = []
    race = r.get("race") if isinstance(r.get("race"), dict) else {}
    out += [f"race.{k} is not a key of [race]: {params._unknown(k, RACE_KEYS)}" for k in race if k not in RACE_KEYS]
    name = race.get("name")
    if not name:
        out.append('race.name is missing: add [race] with name = "..." (the folder of the race).')
    elif not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name.startswith("."):
        out.append(f"race.name = {name!r} is not a folder name: use letters, digits, '.', '-' and '_'.")
    if not isinstance(race.get("root", ""), str):
        out.append(f"race.root = {race['root']!r} is not a string: put the folder in quotes.")
    elif name:
        root = pathlib.Path(race.get("root") or RACES / str(name)).expanduser()
        race["root"] = str(root if root.is_absolute() else (path.parent / root).resolve())
    runners = r.get("runners", [])
    if not isinstance(runners, list) or not all(isinstance(x, dict) for x in runners):
        out.append("runners must be an array of tables: write each team as [[runners]] with its own sections.")
        runners = []
    if not runners:
        out.append('no runner: add one [[runners]] per team, with [runners.mission] name = "...".')
    seen: list[str] = []
    for i, x in enumerate(runners, 1):
        rn = x.get("mission", {}).get("name") if isinstance(x.get("mission"), dict) else None
        if not rn:
            out.append(f'[[runners]] #{i} has no mission.name: add [runners.mission] name = "...".')
        elif not isinstance(rn, str) or not SAFE_NAME.fullmatch(rn) or rn.startswith("."):
            out.append(f"[[runners]] #{i}: mission.name = {rn!r} is not a folder name: use letters, digits, '.', '-' and '_'.")
        elif rn in seen:
            out.append(f"[[runners]] #{i}: two runners are named {rn!r}: give each runner a name of its own.")
        else:
            seen.append(rn)
    mergeable = not out  # a runner's mission is merged and checked only once the race and its names are sound
    out += [f"mission.{k} in the shared [mission] would be the same folder for every runner: move it to "
            f"[runners.mission] or remove it." for k in ("state", "workspace") if k in r.get("mission", {})]
    if mergeable:
        out += [f"{rn}: {p}" for rn, m in missions(r).items() for p in params.problems(m)]
    if out:
        raise MissionFileError("\n".join([f"{path}:", *out]))
    return r


def missions(race: dict) -> dict[str, dict]:
    """Runner name -> its mission (the shared sections, the runner's merged over them), in file order."""
    base = pathlib.Path(race["_file"]).parent
    root = pathlib.Path(race["race"]["root"])
    shared = {s: v for s, v in race.items() if s not in ("race", "runners", "_file")}
    out = {}
    for runner in race["runners"]:
        m = {s: {**v, **runner[s]} if isinstance(v, dict) and isinstance(runner.get(s), dict) else runner.get(s, v)
             for s, v in shared.items()}
        m.update({s: v for s, v in runner.items() if s not in m})
        name = m["mission"]["name"]
        m["mission"] = {**m["mission"]}
        m["mission"].setdefault("state", str(root / name))
        m["mission"].setdefault("workspace", str(root / name / "ws"))
        for k in ("subject", "state", "workspace"):  # the mission files live elsewhere: paths relative to the race file
            v = m["mission"].get(k)
            if isinstance(v, str) and not v.startswith(("http://", "https://")) and not pathlib.Path(v).is_absolute():
                m["mission"][k] = str((base / v).resolve())
        out[name] = m
    return out


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):  # a JSON string is a TOML basic string, but for DEL, which TOML wants escaped
        return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"no TOML form for {v!r}")


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _value(k)


def dumps(m: dict) -> str:
    """A mission as TOML: sections of plain values, and arrays of tables such as [[skills]]."""
    lines = []
    for section, body in m.items():
        for table in body if isinstance(body, list) else [body]:
            lines += [f"[[{_key(section)}]]" if isinstance(body, list) else f"[{_key(section)}]",
                      *(f"{_key(k)} = {_value(v)}" for k, v in table.items()), ""]
    return "\n".join(lines)


def _dir(race: dict, name: str) -> pathlib.Path:
    return pathlib.Path(race["race"]["root"]) / name


def write(race: dict) -> dict[str, pathlib.Path]:
    out = {}
    for name, m in missions(race).items():
        f = _dir(race, name) / "mission.toml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(dumps(m))
        out[name] = f
    return out


def _pid(race: dict, name: str, rec: dict) -> int | None:
    """The recorded pid of the runner's launcher if that process still runs its mission file, else None."""
    pid = (rec.get(name) or {}).get("pid")
    # shortcut: /proc is Linux only; elsewhere every launcher reads as not running. Upgrade when a race runs on macOS.
    try:
        cmd = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (OSError, TypeError):
        return None
    return pid if str(_dir(race, name) / "mission.toml").encode() in cmd else None  # a zombie's cmdline is empty


def _record(race: dict) -> pathlib.Path:
    return pathlib.Path(race["race"]["root"]) / "race.json"


def start(race: dict, argv: list[str] | None = None) -> dict[str, str]:
    """Start the launcher of every runner that is neither running nor delivered, each in a session of its own; at once."""
    files, ms = write(race), missions(race)
    out = {}

    def launch(rec: dict) -> None:
        for name, f in files.items():
            if pid := _pid(race, name, rec):
                out[name] = f"already running (pid {pid})"
            elif (pathlib.Path(ms[name]["mission"]["state"]) / "delivery.json").exists():
                out[name] = "delivered: not started again"
            else:
                with open(f.parent / "launcher.log", "a") as log:
                    p = subprocess.Popen([*(argv or LAUNCH), str(f)], cwd=ROOT, start_new_session=True,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1"})  # the log is read live
                rec[name] = {"pid": p.pid, "started": budget.now().isoformat(timespec="seconds")}
                out[name] = f"started (pid {p.pid})"

    budget.update_json(_record(race), launch, default={})
    return out


def stop(race: dict, wait_s: float = 60) -> dict[str, str]:
    """SIGINT to every live launcher (its cleanup deletes the billed machines; never SIGKILL it), then wait up to wait_s."""
    rec = budget.read_json(_record(race), {})
    live = {n: pid for n in missions(race) if (pid := _pid(race, n, rec))}
    for pid in live.values():
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.time() + wait_s
    while time.time() < deadline and any(_pid(race, n, rec) for n in live):
        time.sleep(0.2)
    return {n: "was not running" if n not in live else
            f"still running (pid {live[n]}): its cleanup goes on" if _pid(race, n, rec) else "stopped"
            for n in missions(race)}


def status(race: dict) -> dict[str, dict]:
    rec = budget.read_json(_record(race), {})
    return {n: {"running": bool(pid := _pid(race, n, rec)), "pid": pid,
                "delivered": (pathlib.Path(m["mission"]["state"]) / "delivery.json").exists(),
                "log": str(_dir(race, n) / "launcher.log")} for n, m in missions(race).items()}
