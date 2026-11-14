#!/usr/bin/env python3
"""Download the official API documentation that agents read offline through the `api_docs` tool.

Writes into apidocs/:
  scaleway/<api>.json       OpenAPI 3.1 specs from scaleway.com/en/developers (converted from YAML)
  runpod/rest-v1.json       OpenAPI 3.0 spec of rest.runpod.io/v1
  runpod/mcp-tools.json     tool list (names, descriptions, input schemas) of the Runpod MCP server (REST v2 surface)
  runpod/pages/*.md         selected pages of docs.runpod.io (serverless job API, pods, pricing)
  scaleway/pages/*.md       selected pages of the Scaleway documentation
  SOURCES.json              URL, date and size of every file

Needs PyYAML (pip install pyyaml) and network access. Re-run it to refresh the docs.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1] / "apidocs"

SCALEWAY_APIS = {
    # name: (path on scaleway.com/en/developers/api/, what it is for)
    "instance": ("instance/v1", "CPU and GPU virtual machines: servers, types, images, volumes, IPs, security groups, user data"),
    "block": ("block/v1", "Block Storage volumes and snapshots (sbs_volume) attached to Instances"),
    "marketplace": ("marketplace/v2", "OS images catalogue: find the image id for a zone and commercial type"),
    "billing": ("billing/v2beta1", "consumption and invoices"),
    "iam": ("iam/v1alpha1", "API keys, applications, policies, SSH keys"),
    "vpc": ("vpc/v2", "VPCs and private networks"),
    "ipam": ("ipam/v1", "IP addresses"),
    "secret-manager": ("secret-manager/v1beta1", "secrets"),
    "registry": ("registry/v1", "container registry namespaces and images"),
    "serverless-jobs": ("serverless-jobs/v1alpha1", "batch jobs on containers (CPU), billed per second"),
    "inference": ("inference/v1", "Managed Inference: dedicated model deployments"),
    "generative-apis": ("generative-apis/v1", "Generative APIs: serverless OpenAI-compatible LLM endpoints"),
    "file-storage": ("file-storage/v1alpha1", "File Storage file systems shared between Instances"),
}

RUNPOD_PAGES = [
    "serverless/endpoints/send-requests",
    "serverless/endpoints/operation-reference",
    "serverless/endpoints/endpoint-configurations",
    "serverless/endpoints/job-states",
    "serverless/pricing",
    "pods/overview",
    "pods/manage-pods",
    "pods/pricing",
    "pods/choose-a-pod",
]

SCALEWAY_PAGES = [
    "instances/how-to/use-cloud-init",
    "gpu/how-to/create-manage-gpu-instance",
    "gpu/reference-content/choosing-gpu-instance-type",
    "object-storage/api-cli/using-api-call-list",
]


def get(url: str, headers: dict | None = None, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "curl/8.5.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> int:
    import yaml  # only needed here

    sources = {}
    today = dt.date.today().isoformat()

    def save(rel: str, content: bytes | str, url: str) -> None:
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode()
        p.write_bytes(content)
        sources[rel] = {"url": url, "fetched": today, "bytes": len(content)}
        print(f"{rel:45s} {len(content):>8d}  {url}")

    for name, (path, purpose) in SCALEWAY_APIS.items():
        url = f"https://www.scaleway.com/en/developers/api/{path}/schema.yml"
        spec = yaml.safe_load(get(url))
        spec.setdefault("info", {})["x-frontier-purpose"] = purpose
        save(f"scaleway/{name}.json", json.dumps(spec, separators=(",", ":")), url)

    url = "https://rest.runpod.io/v1/openapi.json"
    save("runpod/rest-v1.json", json.dumps(json.loads(get(url)), separators=(",", ":")), url)

    # The Runpod MCP server lists its tools without credentials.
    url = "https://mcp.runpod.io/"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    tools = json.loads(get(url, hdr, body))["result"]["tools"]
    save("runpod/mcp-tools.json", json.dumps(tools, indent=1), url)

    for page in RUNPOD_PAGES:
        url = f"https://docs.runpod.io/{page}.md"
        try:
            save(f"runpod/pages/{page.replace('/', '__')}.md", get(url), url)
        except Exception as e:  # noqa: BLE001
            print(f"skip {url}: {e}", file=sys.stderr)
    for page in SCALEWAY_PAGES:
        url = f"https://www.scaleway.com/en/docs/{page}.md"
        try:
            save(f"scaleway/pages/{page.replace('/', '__')}.md", get(url), url)
        except Exception as e:  # noqa: BLE001
            print(f"skip {url}: {e}", file=sys.stderr)

    (ROOT / "SOURCES.json").write_text(json.dumps(sources, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
