"""Snapshots of a mission at fixed times and spends, and the metering of its local CPU.

At each time milestone (5, 15, 30 minutes, then 1, 2, 4, 8 hours, doubling) and each spend milestone (5, 10, 20, 50,
100, 200, 500, 1000, 2000, 5000 USD, the total of model calls and simulations), the metering gateway adds a notice to
the next calls of the harness asking it to bring the report up to date with what it has, and writes a
`snapshot_notice` event. `grace_minutes` later the launcher's watchdog commits the workspace, tags the commit
`snapshot-<label>` and writes a `snapshot` event with the time, the model-call spend, the simulation spend and the CPU
used. Milestones that fall within the grace of a pending one are merged into its snapshot. Milestones before
`notice_from_minutes` are snapshotted without a notice: there is no report to update yet. The jury grades the
snapshots blind to draw the quality of each team against time and against spend.

Local CPU: every process whose environment carries the mission's FRONTIER_STATE is sampled from /proc; the sum of
their user and system time, with that of their children already reaped, is the core-seconds used on this machine,
written as `cpu_usage` events and billed at `local_cpu_usd_per_core_hour` (config/providers.toml [limits]). A
process that leaves the mission's tree (a daemon reparented to init) is counted up to its last sample. Work inside an
OpenShell sandbox is not visible here.
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import subprocess

from .budget import CLOUD_CURRENCY, append_chained, now, parse_time, read_jsonl

TIME_MINUTES = (5, 15, 30, 60, 120, 240, 480)  # then doubling
SPEND_USD = (5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)  # then doubling
DEFAULTS = {"enabled": True, "grace_minutes": 5.0, "notice_from_minutes": 15.0, "notice_calls": 3,
            "time_minutes": list(TIME_MINUTES), "spend_usd": list(SPEND_USD)}
CPU_SAMPLE_S = 10
_TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def settings(state: pathlib.Path) -> dict:
    """The mission's snapshot settings (written by the launcher from the mission's [snapshots] section)."""
    p = state / "snapshots.json"
    try:
        return {**DEFAULTS, **json.loads(p.read_text())}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def write_settings(state: pathlib.Path, mission_section: dict | None) -> dict:
    s = {**DEFAULTS, **(mission_section or {})}
    (state / "snapshots.json").write_text(json.dumps(s, indent=1))
    return s


def _extend(values: list[float], upto: float) -> list[float]:
    out = list(values)
    while out and out[-1] < upto:
        out.append(out[-1] * 2)
    return out


def label(kind: str, value: float) -> str:
    if kind == "time":
        return f"{value / 60:g}h" if value >= 60 else f"{value:g}min"
    return f"{value:g}usd"


def core_seconds(state: pathlib.Path) -> float:
    """The last measured local CPU of the mission, in core-seconds (0 when never measured)."""
    return max([float(e.get("core_seconds", 0)) for e in read_jsonl(state / "events.jsonl")
                if e.get("event") == "cpu_usage"], default=0.0)


def spend(budget, cpu_usd_per_core_hour: float = 0.0) -> tuple[float, float]:
    """(model calls USD, simulations USD: rented machines converted to USD plus billed local CPU)."""
    at = now()
    rented = sum(budget.cloud_spent(prov, at) for prov in CLOUD_CURRENCY)  # EUR counted at par, as in the jury
    return budget.llm_spent(), rented + core_seconds(budget.dir) / 3600 * cpu_usd_per_core_hour


class Snapshots:
    """Shared by the gateway (notices) and the watchdog (snapshots), through the mission's events.jsonl."""

    def __init__(self, budget, cpu_usd_per_core_hour: float = 0.0):
        self.b, self.price = budget, cpu_usd_per_core_hour
        self.s = settings(budget.dir)
        self.left, self.text, self.told = 0, "", set()  # told: labels whose notice this instance gave

    def _events(self, kind: str) -> list[dict]:
        return [e for e in read_jsonl(self.b.dir / "events.jsonl") if e.get("event") == kind]

    def _minutes(self) -> float | None:
        """Minutes since the mission started, or None before it has a start."""
        start = parse_time(self.b.caps.get("start"))
        return (now() - start).total_seconds() / 60 if start else None

    def crossed(self) -> list[tuple[str, float]]:
        """Milestones reached so far: [("time", minutes) | ("spend", usd)]."""
        minutes = self._minutes() or 0.0
        llm, sim = spend(self.b, self.price)
        out = [("time", m) for m in _extend(self.s["time_minutes"], minutes) if minutes >= m]
        return out + [("spend", u) for u in _extend(self.s["spend_usd"], llm + sim) if llm + sim >= u]

    def notice(self) -> str:
        """Called by the gateway for each model call: the notice to add to it, or ""."""
        if not self.s.get("enabled", True):
            return ""
        # A file lock: the gateway and the watchdog have a Snapshots each, and both would write a new milestone
        with open(self.b.dir / ".snapshots.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            notices = self._events("snapshot_notice")
            seen = {(e["kind"], float(e["value"])) for e in notices}
            minutes = self._minutes() or 0.0
            for kind, value in [c for c in self.crossed() if c not in seen]:
                nf = float(self.s["notice_from_minutes"])
                ev = {"t": now().isoformat(timespec="seconds"), "event": "snapshot_notice", "kind": kind,
                      "value": value, "label": label(kind, value), "silent": value < nf if kind == "time" else minutes < nf}
                append_chained(self.b.dir / "events.jsonl", ev)
                notices.append(ev)
            # whoever recorded it (the watchdog ticks without model calls), the gateway tells each pending milestone
            taken = {lab for e in self._events("snapshot") for lab in e.get("labels", [])}
            fresh = [e["label"] for e in notices if not e.get("silent") and e["label"] not in taken | self.told]
            if fresh:
                self.told.update(fresh)
                self.left = int(self.s["notice_calls"])
                self.text = (f"[Frontier snapshot notice: {fresh[-1].replace('usd', ' USD')} reached. Bring "
                             "the report up to date with what you have now (results so far, figures, limits); the "
                             f"framework takes a snapshot of the repository in {self.s['grace_minutes']:g} minutes, "
                             "which the jury grades to draw your quality against time and spend. Then go on.]")
            if self.left <= 0:
                return ""
            self.left -= 1
            return self.text

    def due(self) -> list[dict]:
        """Notices whose grace has passed and that no snapshot covers yet, with the ones merged into them."""
        taken = {lab for e in self._events("snapshot") for lab in e.get("labels", [])}
        pending = [e for e in self._events("snapshot_notice") if e["label"] not in taken]
        if not pending:
            return []
        first = parse_time(pending[0]["t"])
        grace = float(self.s["grace_minutes"]) * 60
        if (now() - first).total_seconds() < (0 if pending[0].get("silent") else grace):
            return []
        return [e for e in pending if (parse_time(e["t"]) - first).total_seconds() <= grace]

    def take(self, ws: pathlib.Path) -> dict | None:
        """Called by the watchdog: commit and tag the workspace for the due milestones, write the `snapshot` event."""
        if not self.s.get("enabled", True):
            return None
        due = self.due()
        if not due:
            return None
        labels = [e["label"] for e in due]
        commit(ws, "Snapshot " + ", ".join(labels), tuple(f"snapshot-{lab}" for lab in labels), allow_empty=True)
        head = subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        llm, sim = spend(self.b, self.price)
        minutes = self._minutes()
        ev = {"t": now().isoformat(timespec="seconds"), "event": "snapshot", "labels": labels, "commit": head,
              "minutes": round(minutes, 2) if minutes is not None else None,
              "llm_usd": round(llm, 4), "simulation_usd": round(sim, 4),
              "core_seconds": round(core_seconds(self.b.dir), 1)}
        append_chained(self.b.dir / "events.jsonl", ev)
        return ev


def commit(ws: pathlib.Path, message: str, tags: tuple[str, ...] = (), allow_empty: bool = False) -> None:
    """Commit and tag the workspace from outside the harness (whose sandbox may keep .git read-only), under the
    framework's own identity, output captured (inside the MCP server, file descriptor 1 is the JSON-RPC pipe)."""
    git = ["git", "-C", str(ws)]
    subprocess.run([*git, "add", "-A"], capture_output=True)
    subprocess.run([*git, "-c", "user.name=frontier", "-c", "user.email=frontier@localhost", "commit", "-q",
                    *(["--allow-empty"] if allow_empty else []), "-m", message], capture_output=True)
    for tag in tags:
        subprocess.run([*git, "tag", "-f", tag], capture_output=True)


class CpuMeter:
    """Core-seconds of the processes that carry FRONTIER_STATE=<state>, sampled from /proc."""

    def __init__(self, state: pathlib.Path, proc: pathlib.Path = pathlib.Path("/proc")):
        self.state, self.proc = str(state), proc
        self.last: dict[int, tuple[float, int]] = {}  # pid -> (its time with reaped children, its parent)
        self.gone = core_seconds(state)  # what earlier launches on this state measured: this one adds to it

    def _sample(self) -> dict[int, tuple[float, int]]:
        marker = f"FRONTIER_STATE={self.state}".encode()
        out = {}
        for d in self.proc.iterdir():
            if not d.name.isdigit():
                continue
            try:
                if marker not in (d / "environ").read_bytes().split(b"\0"):
                    continue
                f = (d / "stat").read_text().rsplit(")", 1)[1].split()
            except OSError:
                continue
            # after the command: state ppid ... utime(14) stime(15) cutime(16) cstime(17), 1-based from pid
            ppid, ticks = int(f[1]), sum(int(x) for x in f[11:15])
            out[int(d.name)] = (ticks / _TICK, ppid)
        return out

    def measure(self) -> float:
        now_ = self._sample()
        for pid, (secs, ppid) in self.last.items():
            if pid not in now_ and ppid not in now_:  # not reaped by a process of the mission: its time is lost there
                self.gone += secs
        self.last = now_
        return self.gone + sum(s for s, _ in now_.values())

    def record(self) -> float:
        """Measure, and write a `cpu_usage` event when the total grew by a core-second or more."""
        before = core_seconds(pathlib.Path(self.state))
        total = max(self.measure(), before)
        if total - before < 1:
            return total
        append_chained(pathlib.Path(self.state) / "events.jsonl", {
            "t": now().isoformat(timespec="seconds"), "event": "cpu_usage", "core_seconds": round(total, 1)})
        return total


__all__ = ["Snapshots", "CpuMeter", "settings", "write_settings", "label", "core_seconds", "spend"]
