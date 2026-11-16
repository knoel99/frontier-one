#!/usr/bin/env python3
"""Run a mission on a Scaleway VM of its own and get the link to its live dashboard.

    python3 tools/race_vm.py up MISSION.toml [--zone nl-ams-1] [--type BASIC2-A4C-8G] [--branch master] [--wait]
    python3 tools/race_vm.py down SERVER_ID [--zone nl-ams-1]

`up` creates, for this race only: an IAM application whose API key expires a day after the mission's end and may
read the mission's keys (the Key-value secret `frontier-keys` at path / of Secret Manager) and, when the mission has
a [scaleway] section, manage Instances and Block Storage; a flexible IP; and the VM. Its first boot
(tools/race_vm_init.sh) installs the framework and the mission's harness under a non-root user, starts the mission,
and serves the dashboard over HTTPS at https://<ip>.sslip.io behind a random token. `up` prints that link.
The mission's [secrets] section is dropped: on the VM every key comes from `frontier-keys`.
`down` deletes the VM, its volumes, its IP, and the IAM application with its key and policy.
The operator's own Scaleway key comes from the usual sources (SCW_SECRET_KEY, or a proxy that injects it).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import pathlib
import re
import secrets
import sys
import time
import tomllib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from frontier.budget import parse_time
from frontier.http import scw, scw_defaults

INIT = ROOT / "tools" / "race_vm_init.sh"
SECRET, SECRET_REGION = "frontier-keys", "fr-par"
DISK_BYTES = 40_000_000_000  # the harnesses' npm, uv and pnpm trees


def mission_for_vm(text: str) -> str:
    """The mission file as the VM runs it: without its [secrets] table, whose sources are the operator's machine's."""
    return re.sub(r"(?ms)^\[secrets\][^\[]*", "", text)


def mission_end(m: dict, now: dt.datetime) -> dt.datetime:
    if "deadline" in m["mission"]:
        return parse_time(m["mission"]["deadline"])
    return now + dt.timedelta(hours=float(m["mission"]["duration_hours"]))


def subject_file(path: pathlib.Path, m: dict) -> tuple[str, bytes]:
    """(relative path, content) of a local subject, sent with the mission; ("", b"") for a URL."""
    s = m["mission"]["subject"]
    if re.match(r"https?://", s):
        return "", b""
    if pathlib.Path(s).is_absolute() or ".." in pathlib.Path(s).parts:
        sys.exit(f"subject {s!r}: give a path relative to the mission file, inside its folder, or a URL")
    # shortcut: the subject travels in the user data (Scaleway caps it, about 1 MB); a bigger one goes by URL
    return s, (path.parent / s).read_bytes()


def fill(template: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        template = template.replace(f"__{k}__", v)
    if left := sorted(set(re.findall(r"__[A-Z0-9]+__", template))):
        raise ValueError(f"unfilled placeholders: {left}")
    return template


def create_iam(name: str, org: str, project: str, perms: list[str], expires: dt.datetime) -> tuple[str, str]:
    """(application id, secret key) of a new IAM application allowed `perms` on the project."""
    app = scw("POST", "/iam/v1alpha1/applications", {"name": name, "organization_id": org,
                                                      "description": "race VM key, deleted by tools/race_vm.py down"})["id"]
    scw("POST", "/iam/v1alpha1/policies", {"name": name, "organization_id": org, "application_id": app,
                                           "rules": [{"permission_set_names": perms, "project_ids": [project]}]})
    key = scw("POST", "/iam/v1alpha1/api-keys", {"application_id": app, "default_project_id": project,
                                                 "description": name,
                                                 "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return app, key["secret_key"]


def wait_ready(url: str, minutes: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(20)
    return False


def up(a) -> int:
    path = pathlib.Path(a.mission).resolve()
    text = path.read_text()
    m = tomllib.loads(text)
    name, d = m["mission"]["name"], scw_defaults()
    project, zone = d["project_id"], a.zone
    if not project:
        sys.exit("SCW_DEFAULT_PROJECT_ID is not set")
    org = scw("GET", f"/account/v3/projects/{project}")["organization_id"]
    now = dt.datetime.now(dt.UTC)
    subject_name, subject = subject_file(path, m)
    perms = ["SecretManagerReadOnly", "SecretManagerSecretAccess"]
    if "scaleway" in m:
        perms += ["InstancesFullAccess", "BlockStorageFullAccess"]
    vm = f"race-{name}"[:60]
    created: dict[str, str] = {}
    try:
        created["app"], key = create_iam(vm, org, project, perms, mission_end(m, now) + dt.timedelta(days=1))
        ip = scw("POST", f"/instance/v1/zones/{zone}/ips", {"project": project, "type": "routed_ipv4"})["ip"]
        created["ip"] = ip["id"]
        host = ip["address"].replace(".", "-") + ".sslip.io"
        token = secrets.token_urlsafe(24)
        script = fill(INIT.read_text(), {
            "BRANCH": a.branch, "MISSION_B64": base64.b64encode(mission_for_vm(text).encode()).decode(),
            "SUBJECT_NAME": subject_name, "SUBJECT_B64": base64.b64encode(subject).decode(), "SCW_KEY": key,
            "REGION": SECRET_REGION, "PROJECT": project, "SECRET": SECRET, "ZONE": zone, "TOKEN": token, "HOST": host})
        imgs = scw("GET", "/marketplace/v2/local-images",
                   query={"image_label": "ubuntu_noble", "zone": zone, "type": "instance_sbs"})["local_images"]
        img = next((i for i in imgs if a.type in i.get("compatible_commercial_types", [])), None)
        if not img:
            raise ValueError(f"no ubuntu_noble image for {a.type} in {zone}")
        server = scw("POST", f"/instance/v1/zones/{zone}/servers", {
            "name": vm, "commercial_type": a.type, "image": img["id"], "project": project, "public_ips": [ip["id"]],
            "tags": ["frontier", "race-vm", f"iam-app:{created['app']}"],
            "volumes": {"0": {"size": DISK_BYTES, "volume_type": "sbs_volume"}}})["server"]
        created["server"] = server["id"]
        scw("PATCH", f"/instance/v1/zones/{zone}/servers/{server['id']}/user_data/cloud-init", script,
            content_type="text/plain")
        scw("POST", f"/instance/v1/zones/{zone}/servers/{server['id']}/action", {"action": "poweron"})
    except Exception as e:  # noqa: BLE001 - whatever failed, nothing created may be left billing
        print(f"up failed: {e}; deleting what was created", file=sys.stderr)
        teardown(zone, created.get("server", ""), created.get("ip", ""), created.get("app", ""))
        return 1
    url = f"https://{host}/?t={token}"
    print(f"server {server['id']} ({a.type}, {zone}, {ip['address']})\ndashboard {url}\n"
          f"delete it with: python3 tools/race_vm.py down {server['id']} --zone {zone}")
    if a.wait:
        print("dashboard answering" if wait_ready(url, 30) else "dashboard not answering after 30 min", flush=True)
    return 0


def teardown(zone: str, server_id: str, ip_id: str, app: str) -> None:
    """Delete the IAM application, then the server with its Block Storage volumes and its flexible IP; a second run
    after a partial failure finishes the job."""
    vols: list[str] = []
    s = scw("GET", f"/instance/v1/zones/{zone}/servers/{server_id}", ok404=True) if server_id else None
    if s:
        s = s["server"]
        vols = [v["id"] for v in (s.get("volumes") or {}).values() if v.get("volume_type") == "sbs_volume"]
        ip_id = ip_id or next((i["id"] for i in s.get("public_ips") or []), "")
        app = app or next((t.split(":", 1)[1] for t in s.get("tags", []) if t.startswith("iam-app:")), "")
    # the IAM application first: its id is in the server's tags, lost once the server is gone
    if app and (application := scw("GET", f"/iam/v1alpha1/applications/{app}", ok404=True)):
        for p in scw("GET", "/iam/v1alpha1/policies", query={"application_ids": app,
                                                             "organization_id": application["organization_id"]})["policies"]:
            scw("DELETE", f"/iam/v1alpha1/policies/{p['id']}")
        scw("DELETE", f"/iam/v1alpha1/applications/{app}")  # its API keys go with it
    if s:
        if s["state"] == "stopped":
            scw("DELETE", f"/instance/v1/zones/{zone}/servers/{server_id}")
        else:
            scw("POST", f"/instance/v1/zones/{zone}/servers/{server_id}/action", {"action": "terminate"})
        while scw("GET", f"/instance/v1/zones/{zone}/servers/{server_id}", ok404=True) is not None:
            time.sleep(5)
    for v in vols:  # terminate only detaches Block Storage volumes: they keep billing
        while (vol := scw("GET", f"/block/v1/zones/{zone}/volumes/{v}", ok404=True)) is not None:
            if vol.get("status") in ("available", "error") and not vol.get("references"):
                scw("DELETE", f"/block/v1/zones/{zone}/volumes/{v}")
            time.sleep(5)
    if ip_id and scw("GET", f"/instance/v1/zones/{zone}/ips/{ip_id}", ok404=True):
        scw("DELETE", f"/instance/v1/zones/{zone}/ips/{ip_id}")
    print(f"deleted: server {server_id or '-'}, volumes {vols or '-'}, ip {ip_id or '-'}, IAM application {app or '-'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up", help="create the race VM and print its dashboard link")
    u.add_argument("mission")
    u.add_argument("--zone", default="nl-ams-1")
    u.add_argument("--type", default="BASIC2-A4C-8G")
    u.add_argument("--branch", default="master", help="branch of the framework the VM clones")
    u.add_argument("--wait", action="store_true", help="wait until the dashboard answers (up to 30 min)")
    d = sub.add_parser("down", help="delete the race VM, its volumes, its IP and its IAM application")
    d.add_argument("server_id")
    d.add_argument("--zone", default="nl-ams-1")
    a = ap.parse_args()
    if a.cmd == "up":
        return up(a)
    teardown(a.zone, a.server_id, "", "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
