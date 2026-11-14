"""Ready-made infrastructure functions (Scaleway Instances, Runpod pods), exposed to agents like MCP tools.

They wrap the multi-step flows an agent would otherwise rediscover from the API docs (prices from the right
catalogue, image lookup, cloud-init, power-on, polling, deleting the volumes a terminate leaves behind). Every
resource they create is recorded in the run's ledger (runs/<id>/resources.json) so that it can be listed and
deleted at the end of the run; they only delete resources of that ledger unless told otherwise.
"""

from __future__ import annotations

import json
import time

from .http import APIError, mcp_json, scw, scw_defaults

POLL_S = 5
SCW_ZONES = ["fr-par-1", "fr-par-2", "fr-par-3", "nl-ams-1", "nl-ams-2", "nl-ams-3", "pl-waw-1", "pl-waw-2", "pl-waw-3", "it-mil-1"]


def _j(x) -> str:
    return json.dumps(x, indent=1, ensure_ascii=False)


def _need_spend(ctx, what: str) -> str | None:
    if ctx.allow_spend:
        return None
    return f"refused: {what} costs money and this run does not allow spending (start it with --allow-spend)."


def _price_eur_h(p: dict) -> float:
    rp = (p.get("price") or {}).get("retail_price") or {}
    value = rp.get("units", 0) + rp.get("nanos", 0) / 1e9
    unit = (p.get("unit_of_measure") or {}).get("unit", "hour")
    size = (p.get("unit_of_measure") or {}).get("size", 1) or 1
    return round(value / size * {"minute": 60, "second": 3600, "hour": 1}.get(unit, 1), 4)


def _paged(path: str, key: str, query: dict, size_param: str, size: int = 100) -> dict:
    out: dict | list = {} if key == "servers" else []
    page = 1
    while True:
        r = scw("GET", path, query={**query, size_param: size, "page": page})
        chunk = r.get(key) or ({} if isinstance(out, dict) else [])
        if isinstance(out, dict):
            out.update(chunk)
        else:
            out += chunk
        total = r.get("total_count")
        if not chunk or len(chunk) < size or (total is not None and len(out) >= total):
            return out
        page += 1


# ---------------------------------------------------------------- Scaleway


def scw_offers(ctx, zone: str = "", min_gpu_memory_gb: float = 0, gpu_only: bool = True, include_unavailable: bool = False) -> str:
    """Instance types of a zone (or every zone with zone="all") with their real price (Product Catalog) and stock."""
    if zone == "all":
        rows = []
        for z in SCW_ZONES:
            try:
                rows += [{"zone": z, **o} for o in json.loads(scw_offers(ctx, z, min_gpu_memory_gb, gpu_only,
                                                                         include_unavailable))["offers"]]
            except APIError:
                continue
        rows.sort(key=lambda r: (r["eur_per_hour"] is None, r["eur_per_hour"] or 0))
        return _j({"zone": "all", "offers": rows})
    zone = zone or scw_defaults()["zone"]
    types = _paged(f"/instance/v1/zones/{zone}/products/servers", "servers", {}, "per_page")
    avail = _paged(f"/instance/v1/zones/{zone}/products/servers/availability", "servers", {}, "per_page")
    cat = _paged("/product-catalog/v2alpha1/public-catalog/products", "products",
                 {"product_types": "instance", "zone": zone}, "page_size", 500)
    price = {p["product"]: (_price_eur_h(p), p.get("status")) for p in cat}
    rows = []
    for name, t in types.items():
        gi = t.get("gpu_info") or {}
        mem = round((gi.get("gpu_memory") or 0) / 2**30)
        if gpu_only and not t.get("gpu"):
            continue
        if mem < min_gpu_memory_gb:
            continue
        a = (avail.get(name) or {}).get("availability", "unknown")
        if a != "available" and not include_unavailable and a != "scarce":
            continue
        eur, status = price.get(name, (None, None))
        if status == "retired" or t.get("end_of_service"):
            continue
        rows.append({"type": name, "gpus": t.get("gpu", 0), "gpu": gi.get("gpu_name"), "gpu_memory_gb": mem,
                     "vcpus": t.get("ncpus"), "ram_gb": round(t.get("ram", 0) / 2**30), "availability": a, "eur_per_hour": eur})
    rows.sort(key=lambda r: (r["eur_per_hour"] is None, r["eur_per_hour"] or 0))
    return _j({"zone": zone, "offers": rows})


def _server(zone: str, server_id: str) -> dict | None:
    r = scw("GET", f"/instance/v1/zones/{zone}/servers/{server_id}", ok404=True)
    return None if r is None else r["server"]


def _server_view(s: dict) -> dict:
    ips = [ip.get("address") for ip in s.get("public_ips") or []] or ([s["public_ip"]["address"]] if s.get("public_ip") else [])
    return {"id": s["id"], "name": s["name"], "type": s["commercial_type"], "state": s["state"], "zone": s["zone"],
            "public_ips": ips, "tags": s.get("tags", []),
            "volumes": [{"id": v["id"], "type": v.get("volume_type")} for v in (s.get("volumes") or {}).values()]}


def scw_create_instance(ctx, name: str, commercial_type: str, max_hours: float, zone: str = "", image_label: str = "",
                        cloud_init: str = "", tags: list[str] | None = None, wait_running: bool = True) -> str:
    """Create an Instance from a marketplace image, set cloud-init, power it on, wait until running.
    max_hours: the framework deletes it after that many hours; the budget reserves price x max_hours."""
    if g := _need_spend(ctx, f"creating a Scaleway {commercial_type}"):
        return g
    if not max_hours or max_hours <= 0:
        return "error: max_hours must be > 0"
    d = scw_defaults()
    zone = zone or d["zone"]
    gpu = any(commercial_type.upper().startswith(p) for p in ("L4", "L40S", "H100", "B300", "RENDER", "RTX", "GPU"))
    image_label = image_label or ("ubuntu_noble_gpu_os_12" if gpu else "ubuntu_noble")
    imgs = scw("GET", "/marketplace/v2/local-images",
               query={"image_label": image_label, "zone": zone, "type": "instance_sbs"}).get("local_images", [])
    img = next((i for i in imgs if commercial_type in i.get("compatible_commercial_types", [])), None)
    if not img:
        return f"error: no {image_label} image in {zone} compatible with {commercial_type}"
    offer = next((o for o in json.loads(scw_offers(ctx, zone, 0, gpu_only=False, include_unavailable=True))["offers"]
                  if o["type"] == commercial_type), {})
    ok, why = ctx.run.budget.cloud_allowed("scaleway", offer.get("eur_per_hour"), max_hours)
    if not ok:
        return f"refused: {why}"
    body = {"name": name, "commercial_type": commercial_type, "image": img["id"], "project": d["project_id"],
            "tags": ["frontier", f"run:{ctx.run.run_id}", *(tags or [])]}
    s = scw("POST", f"/instance/v1/zones/{zone}/servers", body)["server"]
    ctx.run.record({"provider": "scaleway", "kind": "instance", "id": s["id"], "zone": zone, "name": name,
                    "type": commercial_type, "eur_per_hour": offer.get("eur_per_hour"), "max_hours": max_hours,
                    "volumes": [v["id"] for v in (s.get("volumes") or {}).values()]})
    if cloud_init:
        scw("PATCH", f"/instance/v1/zones/{zone}/servers/{s['id']}/user_data/cloud-init", cloud_init, content_type="text/plain")
    scw("POST", f"/instance/v1/zones/{zone}/servers/{s['id']}/action", {"action": "poweron"})
    if wait_running:
        t0 = time.time()
        while time.time() - t0 < 600:
            s = _server(zone, s["id"]) or s
            if s["state"] in ("running", "stopped") and s.get("state_detail") != "booting kernel":
                break
            time.sleep(POLL_S)
    return _j({**_server_view(s), "eur_per_hour": offer.get("eur_per_hour"), "image": image_label,
               "note": "SSH as root with the project's SSH keys; delete with scw_delete_instance"})


def scw_instance(ctx, server_id: str, zone: str = "") -> str:
    s = _server(zone or scw_defaults()["zone"], server_id)
    return "not found (deleted?)" if s is None else _j(_server_view(s))


def scw_list_instances(ctx, zone: str = "", tag: str = "frontier") -> str:
    zone = zone or scw_defaults()["zone"]
    r = scw("GET", f"/instance/v1/zones/{zone}/servers", query={"tags": tag, "per_page": 100})
    return _j([_server_view(s) for s in r.get("servers", [])])


def _delete_scw_instance(run, server_id: str, zone: str) -> dict:
    s = _server(zone, server_id)
    done = {"id": server_id, "server": "already gone", "volumes_deleted": [], "ips_deleted": []}
    rec = next((r for r in run.resources if r["id"] == server_id), {})
    vols = set(rec.get("volumes", []))
    ips = []
    if s is not None:
        vols |= {v["id"] for v in (s.get("volumes") or {}).values() if v.get("volume_type") == "sbs_volume"}
        ips = [ip["id"] for ip in s.get("public_ips") or []]
        if s["state"] == "stopped":
            scw("DELETE", f"/instance/v1/zones/{zone}/servers/{server_id}")
        else:
            scw("POST", f"/instance/v1/zones/{zone}/servers/{server_id}/action", {"action": "terminate"})
        t0 = time.time()
        while _server(zone, server_id) is not None and time.time() - t0 < 600:
            time.sleep(POLL_S)
        done["server"] = "deleted"
    for v in vols:  # terminate only detaches Block Storage volumes: they keep billing
        t0 = time.time()
        while time.time() - t0 < 300:
            vol = scw("GET", f"/block/v1/zones/{zone}/volumes/{v}", ok404=True)
            if vol is None:
                break
            if vol.get("status") in ("available", "error") and not vol.get("references"):
                scw("DELETE", f"/block/v1/zones/{zone}/volumes/{v}")
                done["volumes_deleted"].append(v)
                break
            time.sleep(POLL_S)
    for ip in ips:
        r = scw("GET", f"/instance/v1/zones/{zone}/ips/{ip}", ok404=True)
        if r and not (r.get("ip") or {}).get("server"):
            scw("DELETE", f"/instance/v1/zones/{zone}/ips/{ip}")
            done["ips_deleted"].append(ip)
    run.mark(server_id, "deleted")
    return done


def scw_delete_instance(ctx, server_id: str, zone: str = "", not_created_by_this_run: bool = False) -> str:
    """Delete an Instance with its Block Storage volumes and its flexible IPs."""
    zone = zone or scw_defaults()["zone"]
    if not ctx.run.owns(server_id) and not (not_created_by_this_run and ctx.allow_spend):
        return ("refused: this run did not create that Instance. Pass not_created_by_this_run=true only if the task "
                "names it (needs --allow-spend).")
    return _j(_delete_scw_instance(ctx.run, server_id, zone))


# ---------------------------------------------------------------- Runpod (through the MCP server)


def rp_offers(ctx, min_memory_gb: float = 0, cloud: str = "SECURE", gpu_count: int = 1, include_unavailable: bool = False) -> str:
    """GPU types with $/hour per GPU and stock for pods."""
    gpus = mcp_json("list-gpu-types", {"include": ["AVAILABILITY"], "product": ["POD"], "cloud": cloud, "count": gpu_count})
    gpus = gpus.get("gpus", gpus) if isinstance(gpus, dict) else gpus
    key = cloud.lower()
    rows = []
    for g in gpus:
        p = (g.get("price") or {}).get(key)
        a = g.get("availability", "unknown")
        if (g.get("memory") or 0) < min_memory_gb or not p:
            continue
        if not include_unavailable and a in ("NONE", "UNAVAILABLE", None):
            continue
        rows.append({"gpu_type_id": g["id"], "name": g.get("name"), "memory_gb": g.get("memory"), "usd_per_gpu_hour": p,
                     "availability": a, "max_count": (g.get("maxCount") or {}).get(key),
                     "data_centers": [d["id"] for d in g.get("dataCenters") or [] if d.get("availability") not in ("NONE",)][:6]})
    rows.sort(key=lambda r: r["usd_per_gpu_hour"])
    return _j({"cloud": cloud, "gpu_count": gpu_count, "offers": rows})


def rp_create_pod(ctx, name: str, gpu_type_id: str, max_hours: float,
                  image: str = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404", gpu_count: int = 1, cloud: str = "SECURE",
                  disk_gb: int = 50, ports: list[str] | None = None, env: dict | None = None, command: str = "",
                  start_ssh: bool = False) -> str:
    """Create a pod (billed per second from creation). `command`: container command as one shell string.
    max_hours: the framework deletes it after that many hours; the budget reserves price x max_hours."""
    if g := _need_spend(ctx, f"creating a Runpod pod ({gpu_count}× {gpu_type_id})"):
        return g
    if not max_hours or max_hours <= 0:
        return "error: max_hours must be > 0"
    offers = json.loads(rp_offers(ctx, 0, cloud, gpu_count, include_unavailable=True))["offers"]
    offer = next((o for o in offers if o["gpu_type_id"] == gpu_type_id), None)
    ok, why = ctx.run.budget.cloud_allowed("runpod", offer["usd_per_gpu_hour"] * gpu_count if offer else None, max_hours)
    if not ok:
        return f"refused: {why}"
    body = {"name": name, "image": image, "gpu": {"id": gpu_type_id, "count": gpu_count}, "cloud": cloud, "disk": disk_gb,
            "env": {"FRONTIER_RUN": ctx.run.run_id, **(env or {})}}
    if ports:
        body["ports"] = ports
    if command:
        body["args"] = json.dumps({"entrypoint": ["/bin/bash", "-c"], "cmd": [command]})
    if start_ssh:
        body["startSsh"] = True
    pod = mcp_json("create-pod", {"body": body})
    pod_id = pod.get("id") or (pod.get("pod") or {}).get("id")
    if not pod_id:
        return f"error: create-pod returned no id: {_j(pod)[:1500]}"
    usd = offer["usd_per_gpu_hour"] * gpu_count if offer else None
    ctx.run.record({"provider": "runpod", "kind": "pod", "id": pod_id, "name": name, "type": f"{gpu_count}x {gpu_type_id}",
                    "usd_per_hour": usd, "max_hours": max_hours})
    return _j({"id": pod_id, "usd_per_hour_gpu_only": usd, "pod": pod,
               "note": "run commands with rp_exec once it is RUNNING; delete with rp_delete_pod"})


def rp_pod(ctx, pod_id: str) -> str:
    return _j(mcp_json("get-pod", {"id": pod_id}))


def rp_list_pods(ctx) -> str:
    return _j(mcp_json("list-pods", {}))


def rp_exec(ctx, pod_id: str, command: str, timeout_s: int = 20) -> str:
    """Run a shell command inside a running pod (no SSH needed); waits at most 25 s per call."""
    body = {"command": ["sh", "-c", command], "timeoutSeconds": max(1, min(25, timeout_s))}
    return _j(mcp_json("exec-pod", {"id": pod_id, "body": body}))


def rp_logs(ctx, pod_id: str, tail: int = 200) -> str:
    return _j(mcp_json("stream-pod-logs", {"podId": pod_id, "tail": tail, "maxWaitMs": 2000}))


def _delete_rp_pod(run, pod_id: str) -> dict:
    try:
        mcp_json("delete-pod", {"id": pod_id})
        state = "deleted"
    except APIError as e:
        state = "already gone" if "404" in str(e) or "not found" in str(e).lower() else f"error: {e}"
        if state.startswith("error"):
            return {"id": pod_id, "state": state}
    run.mark(pod_id, "deleted")
    return {"id": pod_id, "state": state}


def rp_delete_pod(ctx, pod_id: str, not_created_by_this_run: bool = False) -> str:
    if not ctx.run.owns(pod_id) and not (not_created_by_this_run and ctx.allow_spend):
        return ("refused: this run did not create that pod. Pass not_created_by_this_run=true only if the task names it "
                "(needs --allow-spend).")
    return _j(_delete_rp_pod(ctx.run, pod_id))


# ---------------------------------------------------------------- ledger


def infra_resources(ctx) -> str:
    """Resources created by this run (all agents), with state and price."""
    return _j(ctx.run.resources)


def cleanup(run) -> list[dict]:
    """Delete every resource of the run's ledger that is still alive."""
    out = []
    for r in list(run.resources):
        if r.get("state") == "deleted":
            continue
        try:
            if r["provider"] == "scaleway":
                out.append(_delete_scw_instance(run, r["id"], r["zone"]))
            elif r["provider"] == "runpod":
                out.append(_delete_rp_pod(run, r["id"]))
        except APIError as e:
            out.append({"id": r["id"], "error": str(e)[:500]})
    return out


def infra_cleanup(ctx) -> str:
    return _j(cleanup(ctx.run))


def enforce(run) -> list[dict]:
    """Delete what overran its max_hours, and every resource of a provider whose budget is spent. Called by the
    watchdog of long-lived processes (MCP server, gateway) and before every budget report."""
    from .budget import CLOUD_CURRENCY, now

    done = []
    at = now()
    caps = run.budget.caps
    for r in run.alive():
        cap = caps.get(CLOUD_CURRENCY[r["provider"]][0])
        over_time = r.get("max_hours") and run.budget._hours(r, at) >= r["max_hours"]
        over_budget = cap is not None and run.budget.cloud_spent(r["provider"], at) >= cap
        if not (over_time or over_budget):
            continue
        why = "max_hours reached" if over_time else "budget spent"
        try:
            if r["provider"] == "scaleway":
                res = _delete_scw_instance(run, r["id"], r["zone"])
            else:
                res = _delete_rp_pod(run, r["id"])
        except APIError as e:
            res = {"id": r["id"], "error": str(e)[:300]}
        run.log(event="watchdog_delete", reason=why, result=res)
        done.append({**res, "reason": why})
    return done


def budget_status(ctx) -> str:
    """Budgets, spend so far (LLM by model, cloud by provider), commitments of alive resources, time left."""
    deleted = enforce(ctx.run)
    st = ctx.run.budget.status()
    if deleted:
        st["watchdog_deleted"] = deleted
    return _j(st)
