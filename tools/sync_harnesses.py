#!/usr/bin/env python3
"""Generate the harness adapter files of this repository (instructions pointers, project MCP configs, sub-agents,
skills) from AGENTS.md, roles/ and the tool registry; --check fails when a generated file is missing or stale.

The MCP command is `python3 -m frontier.mcp`: harnesses start project servers from the repository root.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from frontier.harnesses import McpCommand, artifacts  # noqa: E402


def main(argv: list[str]) -> int:
    check = "--check" in argv
    files = artifacts(McpCommand())
    stale = []
    for rel, content in sorted(files.items()):
        p = ROOT / rel
        if p.exists() and p.read_text() == content:
            continue
        stale.append(rel)
        if not check:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    if check:
        if stale:
            print("stale or missing harness files (run python3 tools/sync_harnesses.py):\n  " + "\n  ".join(stale))
            return 1
        print(f"{len(files)} harness files up to date")
        return 0
    print(f"wrote {len(stale)} of {len(files)} harness files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
