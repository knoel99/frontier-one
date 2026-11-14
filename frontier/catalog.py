"""The cloud API catalog: one compact signature per operation (catalog/*.json, built by tools/build_catalog.py),
plus a short hand-written guide per provider (catalog/<provider>-guide.md).

Ids: "scaleway/<api>:<operationId>" (e.g. scaleway/instance:CreateServer), "runpod/mcp:<tool>" (e.g.
runpod/mcp:create-pod), "<provider>/guide".
"""

from __future__ import annotations

import functools
import json
import re

from .config import ROOT

DIR = ROOT / "catalog"


@functools.lru_cache(maxsize=1)
def entries() -> list[dict]:
    out: list[dict] = []
    for f in sorted(DIR.glob("*.json")):
        if f.stem != "SOURCES":
            out += json.loads(f.read_text())
    return out


@functools.lru_cache(maxsize=1)
def by_id() -> dict[str, dict]:
    return {e["id"]: e for e in entries()}


def read_only_mcp_tools() -> set[str]:
    return {e["path"] for e in entries() if e["method"] == "MCP" and e.get("read_only")}


def overview() -> str:
    lines = ["Catalog (api_docs action=search query=... [doc=...], then action=show id=...):"]
    groups: dict[str, int] = {}
    for e in entries():
        groups[e["id"].split(":")[0]] = groups.get(e["id"].split(":")[0], 0) + 1
    try:
        purposes = json.loads((DIR / "SOURCES.json").read_text())
    except FileNotFoundError:
        purposes = {}
    for g, n in sorted(groups.items()):
        lines.append(f"- {g}: {n} operations. {purposes.get(g, {}).get('purpose', '')}".rstrip())
    lines.append("- guides: " + ", ".join(f"{g.stem.removesuffix('-guide')}/guide" for g in sorted(DIR.glob("*-guide.md"))))
    return "\n".join(lines)


def _tokens(text: str) -> list[str]:
    """Lower-case words; CamelCase and plurals split so that 'CreateServer' matches 'create server'."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text).lower()
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in re.split(r"[^a-z0-9]+", text) if w]


def search(query: str, doc: str = "", limit: int = 15) -> str:
    words = _tokens(query)
    if not words:
        return "empty query"
    scored = []
    for e in entries():
        if doc and not e["id"].startswith(doc):
            continue
        head = set(_tokens(f"{e['id']} {e['path']} {e['summary'][:120]}"))
        body = set(_tokens(json.dumps(e.get("body") or e.get("args") or "")))
        exact = sum(w in head for w in words)
        prefix = sum(any(t.startswith(w) for t in head) for w in words)
        if not exact and not prefix and not any(w in body for w in words):
            continue
        s = 4 * exact + prefix + sum(w in body for w in words) + 6 * (exact == len(words))
        scored.append((s, e))
    for g in sorted(DIR.glob("*-guide.md")):
        text = g.read_text().lower()
        s = sum(min(text.count(w), 3) for w in words)
        if s and (not doc or doc.startswith(g.stem.removesuffix("-guide"))):
            scored.append((s, {"id": f"{g.stem.removesuffix('-guide')}/guide", "method": "GUIDE", "path": "", "summary": "quick guide"}))
    if not scored:
        return f"no match for {query!r}; try other words, or action=list"
    scored.sort(key=lambda x: -x[0])
    return "\n".join(f"{e['id']}  [{e['method']} {e['path']}]  {e['summary'][:120]}" for _, e in scored[:limit])


def show(op_id: str) -> str:
    if op_id.endswith("/guide"):
        g = DIR / f"{op_id.split('/')[0]}-guide.md"
        return g.read_text() if g.exists() else f"no guide {op_id}"
    e = by_id().get(op_id)
    if e is None:
        return f"no operation {op_id!r}; ids come from action=search (e.g. scaleway/instance:CreateServer, runpod/mcp:create-pod)"
    return json.dumps(e, indent=1, ensure_ascii=False)
