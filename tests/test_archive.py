import importlib.util
import io
import json
import pathlib
import tarfile

import pytest

from frontier import archive, budget


def race(tmp_path: pathlib.Path) -> pathlib.Path:
    """A race directory with one team whose workspace is outside its state directory, and a jury."""
    root, ws = tmp_path / "missions", tmp_path / "work" / "t1"
    (root / "t1").mkdir(parents=True)
    (ws / "snapshots").mkdir(parents=True)
    (ws / "data").mkdir()
    budget.write_json(root / "t1" / "mission.json", {"name": "t1", "provider": "openai", "workspace": str(ws)})
    budget.append_chained(root / "t1" / "llm.jsonl", {"t": "2026-01-01T00:00:00+00:00", "frontier.cost.usd": 0.1})
    budget.append_chained(root / "t1" / "events.jsonl", {"t": "2026-01-01T00:05:00+00:00", "event": "snapshot", "labels": ["5min"]})
    (ws / "report.md").write_text("# Report\n")
    (ws / "DELIVERY.md").write_text("where things are\n")
    (ws / "snapshots" / "5min.md").write_text("# Snapshot 5min\n")
    (ws / "data" / "big.npy").write_bytes(b"0" * 100)  # data and models stay out
    (root / "jury").mkdir()
    budget.write_json(root / "jury" / "snapshots.json", {"t1": {"abc": {"scientific_quality": 10, "artifacts": 5}}})
    return root


def members(tar: pathlib.Path) -> set[str]:
    with tarfile.open(tar, "r:gz") as t:
        return {m.name for m in t.getmembers()}


def test_an_archive_holds_the_analysis_data_and_replaces_the_previous_once_validated(tmp_path):
    root = race(tmp_path)
    first = archive.make(root)
    names = members(root / ".archive" / first["archive"])
    assert {"t1/llm.jsonl", "t1/.llm.jsonl.head", "t1/events.jsonl", "t1/mission.json", "jury/snapshots.json",
            "t1/workspace/report.md", "t1/workspace/DELIVERY.md", "t1/workspace/snapshots/5min.md"} <= names
    assert not any("big.npy" in n or n.endswith(".lock") for n in names)
    assert first["chains"] == {} and first["chains_checked"] == 2 and first["evaluations"]["snapshots"] == 1
    assert archive.latest(root) == first
    budget.append_chained(root / "t1" / "events.jsonl", {"t": "2026-01-01T00:20:00+00:00", "event": "snapshot", "labels": ["15min"]})
    assert archive.evaluations(root) != first["evaluations"]  # --watch takes a new one
    second = archive.make(root)
    assert sorted(p.name for p in (root / ".archive").glob("race-*")) == sorted([second["archive"], second["archive"][:-7] + ".json"])


def test_an_archive_that_does_not_read_back_as_copied_is_dropped_and_the_previous_kept(tmp_path, monkeypatch):
    root = race(tmp_path)
    first = archive.make(root)
    real = tarfile.TarFile.extractfile

    def damaged(self, member):  # the archive reads back with one file not as copied
        return io.BytesIO(b"damaged") if member.name.endswith("report.md") else real(self, member)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", damaged)
    with pytest.raises(ValueError, match="not validated"):
        archive.make(root)
    assert archive.latest(root) == first and (root / ".archive" / first["archive"]).exists()
    assert not list((root / ".archive").glob("*.tmp"))


def test_a_ledger_that_does_not_verify_is_archived_and_named(tmp_path):
    root = race(tmp_path)
    lines = (root / "t1" / "llm.jsonl").read_text().replace('"frontier.cost.usd": 0.1', '"frontier.cost.usd": 0.0')
    (root / "t1" / "llm.jsonl").write_text(lines)
    m = archive.make(root)
    assert m["chains"] == {"t1/llm.jsonl": "broken at line 1"}  # the archive keeps the evidence


def test_the_dashboard_serves_the_archive_and_collect_fetches_and_checks_it(tmp_path):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    from frontier import dashboard
    spec = importlib.util.spec_from_file_location("race_vm", pathlib.Path(__file__).parent.parent / "tools" / "race_vm.py")
    race_vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(race_vm)

    root = race(tmp_path)

    class Handler(dashboard._Handler):
        token = "s3cret-token"

    Handler.root = root
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", "/api/archive")
        assert c.getresponse().status == 403  # no session
        dest = tmp_path / "fetched"
        with pytest.raises(race_vm.urllib.error.HTTPError):  # none yet: down stops
            race_vm.collect(base, "s3cret-token", dest)
        m = archive.make(root)
        out = race_vm.collect(base, "s3cret-token", dest)
        assert out.name == m["archive"] and out.read_bytes() == (root / ".archive" / m["archive"]).read_bytes()
        budget.append_chained(root / "t1" / "events.jsonl", {"t": "2026-01-01T00:20:00+00:00", "event": "snapshot"})
        m2 = archive.make(root)
        assert race_vm.collect(base, "s3cret-token", dest).name == m2["archive"]
        assert [p.name for p in dest.iterdir()] == [m2["archive"]]  # the copy fetched before is deleted
        assert json.loads(json.dumps(dashboard.snapshot(root)["archive"]))["archive"] == m2["archive"]
    finally:
        srv.shutdown()
