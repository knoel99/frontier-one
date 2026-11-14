"""Offline search over the API documentation in apidocs/ (OpenAPI specs, MCP tool list, doc pages).

Every operation gets an id "<doc>:<operationId>" (e.g. "scaleway/instance:CreateServer",
"runpod/rest-v1:CreatePod", "runpod/mcp:create-pod"); `show` returns it with its parameters and its request and
response schemas resolved, so an agent never needs to look for the documentation on the web.
"""

from __future__ import annotations

import functools
import json
import re

from .config import ROOT

DOCS = ROOT / "apidocs"
METHODS = ("get", "post", "put", "patch", "delete")


@functools.cache
def _spec(name: str) -> dict:
    return json.loads((DOCS / f"{name}.json").read_text())


@functools.lru_cache(maxsize=1)
def index() -> list[dict]:
    """One entry per operation of every spec, plus one per MCP tool and per doc page."""
    ops: list[dict] = []
    for f in sorted(DOCS.glob("*/*.json")):
        name = f"{f.parent.name}/{f.stem}"
        if f.stem == "mcp-tools":
            for t in json.loads(f.read_text()):
                ops.append({"id": f"runpod/mcp:{t['name']}", "doc": "runpod/mcp", "method": "MCP", "path": t["name"],
                            "summary": t.get("title") or "", "text": t.get("description", "")})
            continue
        spec = _spec(name)
        for path, item in spec.get("paths", {}).items():
            for m in METHODS:
                op = item.get(m)
                if not op:
                    continue
                oid = op.get("operationId") or f"{m.upper()} {path}"
                ops.append({"id": f"{name}:{oid}", "doc": name, "method": m.upper(), "path": path,
                            "summary": op.get("summary", ""), "text": (op.get("description") or "")[:600],
                            "tags": op.get("tags", [])})
    for f in sorted(DOCS.glob("*/GUIDE.md")):
        for sec in f.read_text().split("\n## ")[1:]:
            title = sec.splitlines()[0].strip()
            ops.append({"id": f"{f.parent.name}/guide", "doc": f"{f.parent.name}/guide", "method": "GUIDE", "path": title,
                        "summary": f"quick guide section: {title}", "text": sec[:3000]})
    for f in sorted(DOCS.glob("*/pages/*.md")):
        title = next((ln[7:].strip() for ln in f.read_text().splitlines() if ln.startswith("title:")), f.stem)
        ops.append({"id": f"{f.parent.parent.name}/page:{f.stem}", "doc": f"{f.parent.parent.name}/pages", "method": "PAGE",
                    "path": f.stem.replace("__", "/"), "summary": title, "text": ""})
    return ops


def list_docs() -> str:
    lines = ["Documents (use api_docs action=search or action=show):"]
    for f in sorted(DOCS.glob("*/*.json")):
        name = f"{f.parent.name}/{f.stem}"
        if f.stem == "mcp-tools":
            lines.append("- runpod/mcp: Runpod MCP server tools (REST v2 surface: pods, endpoints, jobs, GPU stock, billing)")
            continue
        info = _spec(name).get("info", {})
        purpose = info.get("x-frontier-purpose") or (info.get("description") or "").split("\n")[0][:120]
        n = sum(1 for o in index() if o["doc"] == name)
        lines.append(f"- {name}: {info.get('title', '')} ({info.get('version', '')}), {n} operations. {purpose}")
    for prov in sorted({p.parent.parent.name for p in DOCS.glob('*/pages/*.md')}):
        pages = [o["id"] for o in index() if o["doc"] == f"{prov}/pages"]
        lines.append(f"- {prov}/pages: {len(pages)} guide pages: " + ", ".join(p.split(":", 1)[1] for p in pages))
    guides = sorted(DOCS.glob("*/GUIDE.md"))
    if guides:
        lines.append("Quick guides (auth, base URLs, common flows, pitfalls): " +
                     ", ".join(f"{g.parent.name}/guide" for g in guides) + " (action=show id=<provider>/guide)")
    return "\n".join(lines)


def search(query: str, doc: str = "", limit: int = 15) -> str:
    words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if w]
    if not words:
        return "empty query"
    scored = []
    for o in index():
        if doc and not o["doc"].startswith(doc):
            continue
        head = f"{o['id']} {o['path']} {o['summary']}".lower()
        body = o["text"].lower()
        s = sum(4 * (w in head) + min(head.count(w), 2) + min(body.count(w), 2) for w in words)
        s += 6 * all(w in head for w in words)
        if not any(w in head or w in body for w in words):
            s = 0
        if s:
            scored.append((s, o))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return f"no match for {query!r}; try other words or action=list"
    return "\n".join(f"{o['id']}  [{o['method']} {o['path']}]  {o['summary']}" for _, o in scored[:limit])


def _resolve(spec: dict, node, depth: int = 0, seen: tuple = ()):
    if depth > 8:
        return "…"
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                return {"$ref": ref, "note": "recursive"}
            target = spec
            for part in ref.lstrip("#/").split("/"):
                target = target.get(part, {})
            return _resolve(spec, target, depth + 1, seen + (ref,))
        return {k: _resolve(spec, v, depth + 1, seen) for k, v in node.items() if k not in ("example", "examples", "x-one-of")}
    if isinstance(node, list):
        return [_resolve(spec, x, depth + 1, seen) for x in node]
    return node


def show(op_id: str) -> str:
    if op_id.endswith("/guide"):
        g = DOCS / op_id.split("/")[0] / "GUIDE.md"
        return g.read_text() if g.exists() else f"no guide {op_id}"
    if ":" not in op_id:
        return f"id must look like 'scaleway/instance:CreateServer' (from action=search), got {op_id!r}"
    doc, oid = op_id.split(":", 1)
    if doc == "runpod/mcp":
        for t in json.loads((DOCS / "runpod" / "mcp-tools.json").read_text()):
            if t["name"] == oid:
                return json.dumps(t, indent=1)
        return f"no MCP tool {oid}"
    if doc.endswith("/page"):
        f = DOCS / doc.split("/")[0] / "pages" / f"{oid}.md"
        return f.read_text() if f.exists() else f"no page {oid}"
    spec = _spec(doc)
    for path, item in spec.get("paths", {}).items():
        for m in METHODS:
            op = item.get(m)
            if op and (op.get("operationId") or f"{m.upper()} {path}") == oid:
                out = {"method": m.upper(), "path": path, "servers": spec.get("servers"),
                       "security": op.get("security", spec.get("security")),
                       **_resolve(spec, {k: v for k, v in op.items() if k != "responses"})}
                resp = op.get("responses", {})
                ok = next((resp[c] for c in ("200", "201", "202", "204") if c in resp), None)
                if ok is not None:
                    out["response_ok"] = _resolve(spec, ok)
                out["other_responses"] = [c for c in resp if c not in ("200", "201", "202", "204")]
                return json.dumps(out, indent=1)
    return f"no operation {oid} in {doc}"
