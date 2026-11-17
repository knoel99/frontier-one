#!/usr/bin/env python3
"""Run a mission on a Scaleway VM of its own and get the link to its live dashboard.

    python3 tools/race_vm.py up MISSION.toml [--zone nl-ams-1] [--type BASIC2-A4C-8G] [--branch master] [--wait]
    python3 tools/race_vm.py up --fake [--speed 10] [--hours 72] [--wait]   # a synthetic race from its start, no key, no LLM
    python3 tools/race_vm.py link SERVER_ID                      # a new single-use login link to its dashboard
    python3 tools/race_vm.py log SERVER_ID [--zone nl-ams-1]     # the tail of the VM's first-boot log
    python3 tools/race_vm.py down SERVER_ID [--zone nl-ams-1]

`up` creates, for this race only: an IAM application whose API key expires a day after the mission's end and may
read the mission's keys (the Key-value secret `frontier-keys` at path / of Secret Manager) and, when the mission has
a [scaleway] section, manage Instances and Block Storage; a flexible IP; and the VM. Its first boot
(tools/race_vm_init.sh) installs the framework and the mission's harness under a non-root user, starts the mission,
and serves the dashboard over HTTPS at https://<vm>.<ip>.sslip.io. Its random token never travels: it stays on the VM
and in ~/.frontier/race-vms/<server id>.json (mode 600) on this machine, and signs single-use login links valid 15 minutes
(frontier/dashboard.py). `up` prints one; `link` makes another.
The mission's [secrets] section is dropped: on the VM every key comes from `frontier-keys`.
`down` deletes the VM, its volumes, its IP, and the IAM application with its key and policy.
`up --fake` runs no mission: its VM gets no key and no IAM application, and serves the dashboard of a synthetic race
(tools/fake_race.py, tools/race_vm_fake_init.sh) behind the same HTTPS and token.
The operator's own Scaleway key comes from the usual sources (SCW_SECRET_KEY, or a proxy that injects it).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
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
from frontier.dashboard import LINK_MINUTES, login_link
from frontier.http import scw, scw_defaults, scw_raw

INIT = ROOT / "tools" / "race_vm_init.sh"
FAKE_INIT = ROOT / "tools" / "race_vm_fake_init.sh"
SECRET, SECRET_REGION = "frontier-keys", "fr-par"
VMS = pathlib.Path.home() / ".frontier" / "race-vms"  # each VM's host and dashboard token, readable by its owner only
DISK_BYTES = 40_000_000_000  # the harnesses' npm, uv and pnpm trees
FAKE_DISK_BYTES = 10_000_000_000  # a fake race installs no harness


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


def create_firewall(zone: str, project: str, name: str) -> str:
    """A security group of the VM's own: every inbound connection dropped but HTTPS and the HTTP that Let's Encrypt and
    the redirect to HTTPS need; SSH included, since nobody logs in (`log` reads the boot log from the user data)."""
    sg = scw("POST", f"/instance/v1/zones/{zone}/security_groups", {
        "name": name, "project": project, "stateful": True, "inbound_default_policy": "drop",
        "outbound_default_policy": "accept", "enable_default_security": True,
        "description": "race VM: inbound 80 and 443 only; deleted by tools/race_vm.py down"})["security_group"]["id"]
    for port in (80, 443):
        for ip_range in ("0.0.0.0/0", "::/0"):
            scw("POST", f"/instance/v1/zones/{zone}/security_groups/{sg}/rules", {
                "protocol": "TCP", "direction": "inbound", "action": "accept", "ip_range": ip_range, "dest_port_from": port})
    return sg


def wait_ready(url: str, minutes: float) -> bool:
    """True once the dashboard answers over HTTPS: 403 to a request without a session is the expected answer."""
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                if r.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 403:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(20)
    return False


def up(a) -> int:
    d = scw_defaults()
    project, zone = d["project_id"], a.zone
    if not project:
        sys.exit("SCW_DEFAULT_PROJECT_ID is not set")
    if not a.fake and not a.mission:
        sys.exit("give a mission file, or --fake")
    now = dt.datetime.now(dt.UTC)
    created: dict[str, str] = {}
    if a.fake:
        vm, text = f"race-fake-{now:%Y%m%d-%H%M}", ""
    else:
        path = pathlib.Path(a.mission).resolve()
        text = path.read_text()
        m = tomllib.loads(text)
        subject_name, subject = subject_file(path, m)
        perms = ["SecretManagerReadOnly", "SecretManagerSecretAccess"]
        if "scaleway" in m:
            perms += ["InstancesFullAccess", "BlockStorageFullAccess"]
        vm = f"race-{m['mission']['name']}"[:60]
    try:
        if not a.fake:
            org = scw("GET", f"/account/v3/projects/{project}")["organization_id"]
            created["app"], key = create_iam(vm, org, project, perms, mission_end(m, now) + dt.timedelta(days=1))
        created["sg"] = create_firewall(zone, project, vm)
        ip = scw("POST", f"/instance/v1/zones/{zone}/ips", {"project": project, "type": "routed_ipv4"})["ip"]
        created["ip"] = ip["id"]
        # a name of this VM's own: Scaleway hands the same IP back, and Let's Encrypt allows 5 certificates a week per name
        label = re.sub(r"[^a-z0-9-]+", "-", vm.lower()).strip("-")[:63]  # a DNS label
        host = f"{label}.{ip['address'].replace('.', '-')}.sslip.io"
        token = secrets.token_urlsafe(24)
        if a.fake:
            # --speed N: the race starts at boot and plays N times faster; --speed 0: --hours already written, then live
            fake = (f"--replay --speed {a.speed:g} --hours {a.hours or 72:g}" if a.speed
                    else f"--hours {a.hours or 24:g} --live")
            script = fill(FAKE_INIT.read_text(), {"BRANCH": a.branch, "TOKEN": token, "HOST": host, "FAKEARGS": fake})
        else:
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
            "security_group": created["sg"],
            "tags": ["frontier", "race-vm"] + ([f"iam-app:{created['app']}"] if created.get("app") else []),
            "volumes": {"0": {"size": FAKE_DISK_BYTES if a.fake else DISK_BYTES, "volume_type": "sbs_volume"}}})["server"]
        created["server"] = server["id"]
        scw("PATCH", f"/instance/v1/zones/{zone}/servers/{server['id']}/user_data/cloud-init", script,
            content_type="text/plain")
        scw("POST", f"/instance/v1/zones/{zone}/servers/{server['id']}/action", {"action": "poweron"})
    except Exception as e:  # noqa: BLE001 - whatever failed, nothing created may be left billing
        print(f"up failed: {e}; deleting what was created", file=sys.stderr)
        teardown(zone, created.get("server", ""), created.get("ip", ""), created.get("app", ""), created.get("sg", ""))
        return 1
    VMS.mkdir(parents=True, exist_ok=True)
    saved = VMS / f"{server['id']}.json"
    saved.touch(mode=0o600)
    saved.write_text(json.dumps({"zone": zone, "host": host, "token": token}))
    print(f"server {server['id']} ({a.type}, {zone}, {ip['address']})\ndashboard https://{host}/\n"
          f"login link, single use, {LINK_MINUTES} min: {login_link('https://' + host, token)}\n"
          f"another one: python3 tools/race_vm.py link {server['id']}\n"
          f"delete it with: python3 tools/race_vm.py down {server['id']} --zone {zone}", flush=True)
    if a.wait:
        print("dashboard answering" if wait_ready(f"https://{host}/", 30) else "dashboard not answering after 30 min", flush=True)
    return 0


def teardown(zone: str, server_id: str, ip_id: str, app: str, sg: str = "") -> None:
    """Delete the IAM application, then the server with its Block Storage volumes and its flexible IP; a second run
    after a partial failure finishes the job."""
    vols: list[str] = []
    s = scw("GET", f"/instance/v1/zones/{zone}/servers/{server_id}", ok404=True) if server_id else None
    if s:
        s = s["server"]
        vols = [v["id"] for v in (s.get("volumes") or {}).values() if v.get("volume_type") == "sbs_volume"]
        ip_id = ip_id or next((i["id"] for i in s.get("public_ips") or []), "")
        app = app or next((t.split(":", 1)[1] for t in s.get("tags", []) if t.startswith("iam-app:")), "")
        own = s.get("security_group") or {}
        sg = sg or (own.get("id", "") if own.get("name") == s["name"] else "")  # never the project's default group
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
    if sg and scw("GET", f"/instance/v1/zones/{zone}/security_groups/{sg}", ok404=True):
        scw("DELETE", f"/instance/v1/zones/{zone}/security_groups/{sg}")
    print(f"deleted: server {server_id or '-'}, volumes {vols or '-'}, ip {ip_id or '-'}, IAM application {app or '-'}, "
          f"firewall {sg or '-'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up", help="create the race VM and print its dashboard link")
    u.add_argument("mission", nargs="?")
    u.add_argument("--fake", action="store_true", help="no mission: the dashboard of a synthetic race (tools/fake_race.py)")
    u.add_argument("--hours", type=float, help="with --fake: hours of race (72 replayed, or 24 written at once with --speed 0)")
    u.add_argument("--speed", type=float, default=10, help="with --fake: race hours per real hour, 0 for a race already written")
    u.add_argument("--zone", default="nl-ams-1")
    u.add_argument("--type", help="instance type (default BASIC2-A4C-8G, BASIC2-A2C-4G with --fake)")
    u.add_argument("--branch", default="master", help="branch of the framework the VM clones")
    u.add_argument("--wait", action="store_true", help="wait until the dashboard answers (up to 30 min)")
    lk = sub.add_parser("link", help="print a new single-use login link to the VM's dashboard")
    lk.add_argument("server_id")
    lg = sub.add_parser("log", help="print the tail of the VM's first-boot log (written at the end or on a failure)")
    lg.add_argument("server_id")
    lg.add_argument("--zone", default="nl-ams-1")
    d = sub.add_parser("down", help="delete the race VM, its volumes, its IP and its IAM application")
    d.add_argument("server_id")
    d.add_argument("--zone", default="nl-ams-1")
    a = ap.parse_args()
    if a.cmd == "up":
        a.type = a.type or ("BASIC2-A2C-4G" if a.fake else "BASIC2-A4C-8G")
        return up(a)
    if a.cmd == "link":
        saved = VMS / f"{a.server_id}.json"
        if not saved.exists():
            sys.exit(f"no token saved for {a.server_id} in {VMS}: it was created from another machine")
        vm = json.loads(saved.read_text())
        print(login_link("https://" + vm["host"], vm["token"]))
        return 0
    if a.cmd == "log":
        code, text = scw_raw("GET", f"/instance/v1/zones/{a.zone}/servers/{a.server_id}/user_data/race-log")
        print(text if code == 200 else f"no log yet (HTTP {code}): the VM writes it when its first boot ends or fails")
        return 0 if code == 200 else 1
    teardown(a.zone, a.server_id, "", "")
    (VMS / f"{a.server_id}.json").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
