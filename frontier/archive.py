"""Archive of a race's analysis data, taken at each evaluation, so that deleting the race's machine loses nothing.

    python3 -m frontier archive [--root DIR] [--watch]

An archive is DIR/.archive/race-<UTC time>.tar.gz with its manifest race-<UTC time>.json. It holds every team's state
directory (ledgers, budgets, machines, logs), the jury's directory, the race's clock, and from each team's workspace
its report, its milestone snapshots and its Markdown records; data and models stay out (a team publishes them). The
hash-chained logs are copied under the lock their writer takes, so that each log and its head agree.

An archive is validated before it counts: read back whole, every file in it must be the one copied. Only then is its
manifest written and the previous archive deleted: there is always one validated archive, the latest. `--watch` takes
one whenever an evaluation was added (a snapshot, a grade, the jury's verdict), at most one a minute. The dashboard
serves the latest at /api/archive, and `tools/race_vm.py collect` (and `down`) fetches it.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import sys
import tarfile
import tempfile
import time

from .budget import locked, now, read_json, read_jsonl, verify_chain, write_json
from .mission import STATE_ROOT, report_files, states, workspace_of

FILE_MAX = 50_000_000  # bytes: a larger file stays out of the archive, named in the manifest
SKIP = (".lock", ".tmp")
CHAINED = ("llm.jsonl", "events.jsonl")  # budget.append_chained's logs, each with its head


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evaluations(root: pathlib.Path) -> dict:
    """What has been evaluated so far: the snapshots the watchdog took, the grades the jury gave, its final verdict."""
    snaps = sum(1 for p in root.glob("*/events.jsonl") for e in read_jsonl(p) if e.get("event") == "snapshot")
    grades = sum(len(g) for g in (read_json(root / "jury" / "snapshots.json", {}) or {}).values() if isinstance(g, dict))
    verdict = root / "jury" / "jury.json"
    return {"snapshots": snaps, "grades": grades, "verdict": verdict.stat().st_mtime if verdict.exists() else None}


def _capture(root: pathlib.Path) -> dict[str, bytes | None]:
    """Path in the archive -> content, as of now. A chained log and its head are read under their writer's lock."""
    out: dict[str, bytes | None] = {}

    def add(path: pathlib.Path, name: str) -> None:
        if path.name.endswith(SKIP) or not path.is_file():
            return
        if path.stat().st_size > FILE_MAX:
            out[name] = None  # too large: named in the manifest
            return
        if path.name in CHAINED:
            with locked(path.with_name(f".{path.name}.lock")):
                out[name] = path.read_bytes()
                head = path.with_name(f".{path.name}.head")
                if head.exists():
                    out[str(pathlib.PurePosixPath(name).with_name(head.name))] = head.read_bytes()
        elif path.name not in {f".{c}.head" for c in CHAINED}:  # a head is taken with its log
            out[name] = path.read_bytes()

    for d in states(root):
        for f in sorted(d.rglob("*")):
            add(f, f.relative_to(root).as_posix())
        ws = workspace_of(d)
        if ws.is_dir() and not ws.is_relative_to(d):  # a workspace inside the state directory is in it already
            for f in sorted({*report_files(ws), *ws.glob("*.md"), *(ws / "snapshots").glob("*"), *ws.glob("report*.pdf")}):
                add(f, f"{d.name}/workspace/{f.relative_to(ws).as_posix()}")
    for f in sorted((root / "jury").rglob("*")):
        add(f, f.relative_to(root).as_posix())
    add(root / "clock.json", "clock.json")
    return out


def latest(root: pathlib.Path) -> dict | None:
    """The manifest of the latest validated archive, or None."""
    found = sorted((root / ".archive").glob("race-*.json"))
    return read_json(found[-1]) if found else None


def make(root: pathlib.Path) -> dict:
    """Take an archive, validate it, then delete the previous one. Raises ValueError, keeping the previous archive,
    when the archive read back is not what was copied."""
    d = root / ".archive"
    d.mkdir(exist_ok=True)
    with locked(d / ".lock"):
        ev, files = evaluations(root), _capture(root)
        kept = {n: b for n, b in files.items() if b is not None}
        stamp = now().strftime("%Y%m%dT%H%M%S%fZ")
        tar, tmp = d / f"race-{stamp}.tar.gz", d / f"race-{stamp}.tar.gz.tmp"
        with tarfile.open(tmp, "w:gz") as t:
            for name, data in sorted(kept.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mtime = len(data), int(time.time())
                t.addfile(info, io.BytesIO(data))
        try:  # validate: the whole archive reads back, file for file what was copied
            with tarfile.open(tmp, "r:gz") as t:
                back = {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
            bad = sorted(set(kept) ^ set(back)) + [n for n in kept if n in back and _sha(back[n]) != _sha(kept[n])]
            if bad:
                raise ValueError(f"archive not validated: {len(bad)} files differ from the copy, e.g. {bad[0]}")
            with tempfile.TemporaryDirectory() as x:  # the chained logs as they stand in the archive
                chains = {}
                for name, data in back.items():
                    if pathlib.PurePosixPath(name).name in CHAINED:
                        p = pathlib.Path(x) / name
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(data)
                        head = p.with_name(f".{p.name}.head")
                        hname = str(pathlib.PurePosixPath(name).with_name(head.name))
                        if hname in back:
                            head.write_bytes(back[hname])
                        chains[name] = verify_chain(p)
        except (ValueError, OSError, tarfile.TarError):
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(tar)
        from .dashboard import race_clock  # here, not at the top: the dashboard imports this module

        clock = read_json(root / "clock.json")
        manifest = {"archive": tar.name, "made": now().isoformat(timespec="seconds"),
                    "race_time": (race_clock(clock, now()) if clock else now()).isoformat(timespec="seconds"),
                    "evaluations": ev, "files": len(kept), "bytes": tar.stat().st_size, "sha256": _sha(tar.read_bytes()),
                    "chains": {n: c for n, c in chains.items() if c is not True}, "chains_checked": len(chains),
                    "skipped": sorted(n for n, b in files.items() if b is None)}
        write_json(d / f"race-{stamp}.json", manifest)
        for old in d.glob("race-*"):  # the previous archives: this one is validated
            if old.name not in (tar.name, f"race-{stamp}.json"):
                old.unlink(missing_ok=True)
        return manifest


def watch(root: pathlib.Path, every: float = 30, gap: float = 60) -> None:
    """Take an archive whenever an evaluation was added since the latest one, at most one each `gap` seconds."""
    last = 0.0
    while True:
        m = latest(root)
        if (m is None or m["evaluations"] != evaluations(root)) and time.monotonic() - last >= gap and root.is_dir():
            last = time.monotonic()
            try:
                m = make(root)
                print(f"{m['made']}: {m['archive']}, {m['files']} files, {m['bytes'] / 1e6:.1f} MB", flush=True)
            except (ValueError, OSError, tarfile.TarError) as e:  # the previous archive stays; the next round retries
                print(f"{now().isoformat(timespec='seconds')}: {e}", file=sys.stderr, flush=True)
        time.sleep(every)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m frontier archive", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(STATE_ROOT), help="directory holding the missions' state directories")
    ap.add_argument("--watch", action="store_true", help="take one at each evaluation, until stopped")
    a = ap.parse_args(argv)
    root = pathlib.Path(a.root)
    if a.watch:
        watch(root)
        return 0
    m = make(root)
    print(f"{root / '.archive' / m['archive']}: {m['files']} files, {m['bytes'] / 1e6:.1f} MB, sha256 {m['sha256']}")
    return 0

