#!/usr/bin/env python3
"""Build catalog/*.json: one compact signature per cloud API operation, like a Swagger index or an MCP tool list.

Each entry: id, method, path, summary, params ("name*" = required), body (field -> "type: short description",
nested one level), returns (top-level response fields). No long prose: agents see what exists and how to call it.

Sources (official, downloaded at build time, not stored):
  Scaleway OpenAPI specs  https://www.scaleway.com/en/developers/api/<api>/<version>/schema.yml
  Runpod MCP tools        https://mcp.runpod.io/ (tools/list, then describe-tool for each tool's full schema)

Needs PyYAML and network access; the Runpod MCP server needs a RUNPOD_API_KEY or a proxy that injects it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.request

OUT = pathlib.Path(__file__).resolve().parents[1] / "catalog"

SCALEWAY_APIS = {
    "instance": ("instance/v1", "CPU and GPU virtual machines: servers, types, images, volumes, IPs, security groups, user data"),
    "block": ("block/v1", "Block Storage volumes and snapshots attached to Instances"),
    "marketplace": ("marketplace/v2", "OS images: image id for a zone and commercial type"),
    "billing": ("billing/v2beta1", "consumption and invoices"),
    "serverless-jobs": ("serverless-jobs/v1alpha1", "batch jobs on containers (CPU), billed per second"),
    "inference": ("inference/v1", "dedicated model deployments"),
}
METHODS = ("get", "post", "put", "patch", "delete")
# Runpod MCP tools left out: account administration, not compute.
RUNPOD_SKIP = ("organization", "cost-center", "delegation", "audit", "report_", "secret", "registr", "ssh-keys",
               "cluster", "global-volume", "ask_question", "journal")
DESC = 70  # characters kept of a field description


def get(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "curl/8.5.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def short(text: str | None, n: int = 110, sentence: bool = True) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text or "")).strip()
    if sentence:
        t = re.split(r"(?<=[a-z0-9)`][.!?])\s+(?=[A-Z])", t, maxsplit=1)[0]
    return t if len(t) <= n else t[: n - 1] + "…"


class Summarizer:
    """Compact text for a JSON schema, resolving local $refs (#/components/... or #/$defs/...)."""

    def __init__(self, root: dict):
        self.root = root

    def deref(self, s: dict, seen=()) -> dict:
        while isinstance(s, dict) and "$ref" in s and s["$ref"] not in seen:
            seen = seen + (s["$ref"],)
            t = self.root
            for part in s["$ref"].lstrip("#/").split("/"):
                t = t.get(part, {})
            s = {**t, **{k: v for k, v in s.items() if k != "$ref"}}
        if isinstance(s, dict) and "allOf" in s and len(s["allOf"]) == 1:
            rest = {k: v for k, v in s.items() if k != "allOf"}
            return {**self.deref(s["allOf"][0], seen), **rest}
        if isinstance(s, dict) and "allOf" in s:
            merged: dict = {"type": "object", "properties": {}, "required": []}
            for part in s["allOf"]:
                p = self.deref(part, seen)
                merged["properties"].update(p.get("properties", {}))
                merged["required"] += p.get("required", [])
            merged["properties"].update(s.get("properties", {}))
            merged["required"] += s.get("required", [])
            merged["description"] = s.get("description") or merged.get("description", "")
            s = merged
        return s if isinstance(s, dict) else {}

    def typ(self, s: dict) -> str:
        s = self.deref(s)
        for key in ("oneOf", "anyOf"):
            if key in s:
                return " | ".join(self.typ(x) for x in s[key][:4])
        t = s.get("type", "object" if "properties" in s else "any")
        if isinstance(t, list):
            t = "|".join(x for x in t if x != "null") or "null"
        if "enum" in s:
            vals = [str(v) for v in s["enum"] if v is not None]
            return "enum " + "|".join(vals[:10]) + ("|…" if len(vals) > 10 else "")
        if t == "array":
            return f"array<{self.typ(s.get('items', {}))}>"
        if t == "object" and "properties" in s:
            return "object{" + ",".join(list(s["properties"])[:12]) + ("…" if len(s["properties"]) > 12 else "") + "}"
        if t == "object" and isinstance(s.get("additionalProperties"), dict):
            return f"map<string,{self.typ(s['additionalProperties'])}>"
        return t

    def fields(self, s: dict, depth: int = 0) -> dict:
        s = self.deref(s)
        req = set(s.get("required", []))
        out = {}
        for name, sub in (s.get("properties") or {}).items():
            sub = self.deref(sub)
            key = name + ("*" if name in req else "")
            desc = short(sub.get("description"), DESC)
            inner = sub
            if inner.get("type") == "array":
                inner = self.deref(inner.get("items", {}))
            if depth < 1 and inner.get("properties") and len(inner["properties"]) <= 30:
                out[key] = {"_": (self.typ(sub) + (": " + desc if desc else "")), **self.fields(inner, depth + 1)}
            else:
                out[key] = self.typ(sub) + (": " + desc if desc else "")
        return out


def scaleway(sources: dict) -> list[dict]:
    import yaml

    ops = []
    for name, (path, purpose) in SCALEWAY_APIS.items():
        url = f"https://www.scaleway.com/en/developers/api/{path}/schema.yml"
        spec = yaml.safe_load(get(url))
        sources[f"scaleway/{name}"] = {"url": url, "version": spec.get("info", {}).get("version"), "purpose": purpose}
        sm = Summarizer(spec)
        for p, item in spec.get("paths", {}).items():
            for m in METHODS:
                op = item.get(m)
                if not op:
                    continue
                params = []
                for prm in op.get("parameters", []) + item.get("parameters", []):
                    prm = sm.deref(prm)
                    if prm.get("in") in ("path", "query"):
                        params.append(f"{prm['name']}{'*' if prm.get('required') else ''} ({prm['in']}) "
                                      f"{sm.typ(prm.get('schema', {}))}" + (f": {short(prm.get('description'), 50)}"
                                                                           if prm.get("description") else ""))
                e = {"id": f"scaleway/{name}:{op.get('operationId', m + p)}", "method": m.upper(), "path": p,
                     "summary": short(op.get("summary") or op.get("description"), 140), "params": params}
                body = ((op.get("requestBody") or {}).get("content") or {})
                if body:
                    ctype, c = next(iter(body.items()))
                    e["body"] = sm.fields(c.get("schema", {})) if "json" in ctype else f"{ctype} text"
                ok = next((op["responses"][c] for c in ("200", "201", "204") if c in op.get("responses", {})), None)
                if ok:
                    sch = next(iter((sm.deref(ok).get("content") or {}).values()), {}).get("schema")
                    if sch:
                        e["returns"] = sm.typ(sch)
                ops.append(e)
    return ops


def runpod(sources: dict) -> list[dict]:
    url = os.environ.get("RUNPOD_MCP_URL", "https://mcp.runpod.io/")
    key = os.environ.get("RUNPOD_API_KEY", "proxy-injected")
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": f"Bearer {key}"}

    def rpc(method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        return json.loads(get(url, body, hdr))["result"]

    tools = rpc("tools/list")["tools"]
    sources["runpod/mcp"] = {"url": url, "tools": len(tools)}
    ops = []
    tools = [t for t in tools if not any(k in t["name"] for k in RUNPOD_SKIP)]
    for t in tools:
        schema = t.get("inputSchema", {})
        try:  # full schema (some listings say "schema on demand")
            full = json.loads(rpc("tools/call", {"name": "describe-tool", "arguments": {"name": t["name"]}})["content"][0]["text"])
            schema = full.get("inputSchema", schema)
        except Exception as e:  # noqa: BLE001
            print(f"describe-tool {t['name']}: {e}", file=sys.stderr)
        sm = Summarizer(schema)
        ann = t.get("annotations") or {}
        ops.append({"id": f"runpod/mcp:{t['name']}", "method": "MCP", "path": t["name"],
                    "summary": short(t.get("description"), 220, sentence=False), "args": sm.fields(schema),
                    "read_only": bool(ann.get("readOnlyHint"))})
    return ops


def main() -> int:
    OUT.mkdir(exist_ok=True)
    sources = {"built": dt.date.today().isoformat()}
    for name, fn in (("scaleway", scaleway), ("runpod", runpod)):
        ops = fn(sources)
        lines = ",\n".join(json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in ops)
        (OUT / f"{name}.json").write_text("[\n" + lines + "\n]\n")
        print(f"catalog/{name}.json: {len(ops)} operations, {(OUT / f'{name}.json').stat().st_size} bytes")
    (OUT / "SOURCES.json").write_text(json.dumps(sources, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
