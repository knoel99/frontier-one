#!/usr/bin/env python3
"""Check the TLA+ model of the LLM reservations (formal/Reservations.tla) with TLC.

    python3 tools/check_model.py

The model of the code must hold; its two broken variants must be refuted, which shows that the check can fail.
Needs Java 11 or later. The TLA+ tools 1.7.4 (2.3 MB) are downloaded once, checked against their SHA-256, into
~/.cache/frontier (or the file named by FRONTIER_TLA_JAR is used). formal/README.md says what the model covers and
how to read a failure.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.request

FORMAL = pathlib.Path(__file__).resolve().parent.parent / "formal"
JAR_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
JAR_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
EXPECTED = {"code": "holds", "release_first": "refuted", "ledger_first": "refuted"}


def jar() -> pathlib.Path:
    path = pathlib.Path(os.environ.get("FRONTIER_TLA_JAR") or pathlib.Path.home() / ".cache" / "frontier" / "tla2tools-1.7.4.jar")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(JAR_URL, timeout=120) as r:
            path.write_bytes(r.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != JAR_SHA256:
        raise SystemExit(f"{path}: SHA-256 {digest}, expected {JAR_SHA256}: delete it and run again")
    return path


def check(variant: str, tools: pathlib.Path) -> tuple[str, str]:
    """("holds" | "refuted" | "error", what TLC said: the states found, the steps to the broken rule, or its error)."""
    with tempfile.TemporaryDirectory() as meta:
        p = subprocess.run(["java", "-XX:+UseParallelGC", "-cp", str(tools), "tlc2.TLC", "-config", f"{variant}.cfg",
                            "-metadir", meta, "Reservations.tla"], cwd=FORMAL, capture_output=True, text=True, check=False)
    out = p.stdout
    if "No error has been found" in out:
        return "holds", (re.search(r"(\d+) distinct states found", out) or [""])[0]
    broken = re.search(r"Invariant (\w+) is violated", out)
    if not broken:
        return "error", (out + p.stderr)[-1500:]
    steps = []
    for state in out.split("\nState ")[1:]:
        action = re.match(r"\d+: <(\w+)", state)
        values = {k: v.strip() for k, v in re.findall(r"/\\ (spent|res|pc) = (.*)", state)}
        steps.append(f"  {action[1] if action else '?':16} spent {values.get('spent')}  held {values.get('res')}  calls {values.get('pc')}")
    return "refuted", f"{broken[1]} is broken after:\n" + "\n".join(steps)


def main() -> int:
    tools, bad = jar(), 0
    for variant, expected in EXPECTED.items():
        result, detail = check(variant, tools)
        ok = result == expected
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {variant}: {result}" + ("" if ok else f", expected {expected}"))
        if not ok or result == "holds":  # an expected refutation needs no trace
            print("    " + detail.replace("\n", "\n    "))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
