"""Running a mission's harness inside an NVIDIA OpenShell sandbox (OpenShell 0.1.8, docs read 2026-11-14:
docs.nvidia.com/openshell/v0.1.8 — sandboxes, providers, profiles, policy schema).

What OpenShell gives the mission:
- Secrets stay outside. Each key the mission uses (frontier/credentials.py) becomes an OpenShell provider
  backed by a profile of our own; inside the sandbox its variable holds an opaque placeholder, and the OpenShell
  proxy writes the real key into the request only for the profile's hosts and header.
- Network is deny-by-default: only the profiles' hosts, package registries, and the mission's `[network] allow`
  list (read-only, L7-enforced) are reachable; `[network] deny` removes hosts from the allow list.
- Landlock and seccomp confine the filesystem and processes; the workload runs as the `sandbox` user.

Flow (the host launcher drives it; the harness, the metering gateway and the MCP server run inside):
  profile import → provider create → sandbox create (--from Dockerfile, --policy, --provider …) →
  upload workspace and state → per session: sandbox exec `frontier mission session` → download workspace and
  state → host commit → at the end sandbox delete, provider delete.

Run live on 2026-11-15 with OpenShell 0.1.8 and its Docker compute driver.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

from . import credentials
from .config import ROOT, load_config

IN_ROOT = "/opt/frontier"  # this repository inside the sandbox
IN_STATE = "/sandbox/state"
IN_WS = "/sandbox/workspace"
# Processes allowed to reach the credentialed endpoints: Python (MCP server, gateway, sub-agents) and Node
# (most harness CLIs). OpenShell matches scripts by their interpreter; native harness binaries are added per harness.
BINARIES = ["/usr/bin/python3*", "/usr/local/bin/python3*", "/usr/bin/node", "/usr/local/bin/node",
            "/usr/local/lib/node_modules/**", "/usr/bin/curl", "/usr/bin/git"]
PACKAGE_HOSTS = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]
HF_STORAGE = ["cas-bridge.xethub.hf.co", "cas-server.xethub.hf.co", "transfer.xethub.hf.co"]  # signed upload URLs


def _endpoint(host: str, access: str = "read-write") -> dict:
    return {"host": host, "port": 443, "protocol": "rest", "access": access, "enforcement": "enforce"}


def profiles(m: dict, cfg: dict, binaries: list[str]) -> dict[str, dict]:
    """OpenShell provider profiles: one per key the mission uses (frontier/credentials.py), sent by the proxy to the
    service's hosts only."""
    out: dict[str, dict] = {}
    for svc in credentials.used(m, cfg):
        pid = f"frontier-llm-{svc.name}" if svc.kind == "llm" else f"frontier-{svc.name}"
        bearer = svc.header == "authorization" and svc.scheme.lower() == "bearer"
        out[pid] = {"id": pid, "display_name": f"Frontier {svc.name}", "category": "inference" if svc.kind == "llm" else "other",
                    "credentials": [{"name": "api_key", "env_vars": [svc.env], "required": True,
                                     "auth_style": "bearer" if bearer else "header", "header_name": svc.header}],
                    "discovery": {"credentials": ["api_key"]},
                    "endpoints": [_endpoint(h) for h in svc.hosts], "binaries": binaries}
    return out


def policy(m: dict, binaries: list[str]) -> tuple[dict, list[str]]:
    """Sandbox policy (deny by default) and warnings about the network lists."""
    net = m.get("network", {})
    deny = {h.lower() for h in net.get("deny", [])}
    warnings = []
    allow = []
    for h in net.get("allow", []):
        if h.lower() in deny:
            warnings.append(f"{h} is in both lists: denied")
            continue
        if h.startswith("*.") and any(d.endswith(h[1:]) for d in deny):
            warnings.append(f"{h} is a wildcard covering a denied host: the wildcard is dropped, list the hosts instead")
            continue
        allow.append(h)
    binaries = [{"path": b} for b in binaries]  # OpenShell 0.1.8: each item a mapping (checked live 2026-11-15)
    rules = {
        "packages": {"name": "package registries", "endpoints": [_endpoint(h, "read-only") for h in PACKAGE_HOSTS],
                     "binaries": binaries},
        "hf_storage": {"name": "Hugging Face storage (signed URLs)", "endpoints": [_endpoint(h) for h in HF_STORAGE],
                       "binaries": binaries},
    }
    if allow:
        rules["research"] = {"name": "research allow list (read-only)", "endpoints": [_endpoint(h, "read-only") for h in allow],
                             "binaries": binaries}
    pol = {"version": 1,
           "filesystem_policy": {"include_workdir": True, "read_only": ["/usr", "/lib", "/etc", IN_ROOT],
                                 "read_write": ["/sandbox", "/tmp"]},
           "landlock": {"compatibility": "hard_requirement"},
           "process": {"run_as_user": "sandbox", "run_as_group": "sandbox"},
           "network_policies": rules}
    return pol, warnings


def dockerfile(h: dict, base: str) -> str:
    install = h.get("install", "")
    return f"""# Generated by frontier/sandbox.py: the image a mission's harness runs in.
FROM {base}
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git curl ca-certificates \\
    && rm -rf /var/lib/apt/lists/*
RUN {install or 'true'}
RUN python3 -m pip install --break-system-packages --no-cache-dir huggingface_hub
RUN useradd -m -s /bin/bash sandbox && mkdir -p /sandbox/workspace /sandbox/state && chown -R sandbox:sandbox /sandbox
COPY frontier {IN_ROOT}
ENV PATH={IN_ROOT}/bin:$PATH
"""


class OpenShell:
    """The commands of one mission's sandbox. `dry_run` records them instead of running them."""

    def __init__(self, info: dict, h: dict, dry_run: bool = False):
        self.info, self.h, self.dry_run = info, h, dry_run
        self.m, self.s = info["mission"], info["settings"]
        self.name = "frontier-" + "".join(c if c.isalnum() else "-" for c in self.s["name"].lower())[:40]
        self.dir = pathlib.Path(info["state"]) / "openshell"
        self.binaries = BINARIES + list(h.get("sandbox_binaries", []))
        self.commands: list[list[str]] = []
        self.engine = self.m.get("sandbox", {}).get("engine", "docker")  # the engine of the OpenShell gateway's driver
        self.image = f"{self.name}:latest"

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess | None:
        self.commands.append(args)
        if self.dry_run:
            return None
        p = subprocess.run(args, text=True, capture_output=True)
        if check and p.returncode != 0:  # the tool's own message says what to do: keep it in the error
            raise RuntimeError(f"{' '.join(args[:3])} failed (exit {p.returncode}): {(p.stderr or p.stdout).strip()[-1500:]}")
        return p

    def write_files(self) -> list[str]:
        self.dir.mkdir(parents=True, exist_ok=True)
        cfg = load_config()
        profs = profiles(self.m, cfg, self.binaries)
        for pid, p in profs.items():  # JSON is valid YAML
            (self.dir / f"{pid}.yaml").write_text(json.dumps(p, indent=1) + "\n")
        pol, warnings = policy(self.m, self.binaries)
        (self.dir / "policy.yaml").write_text(json.dumps(pol, indent=1) + "\n")
        ctx = self.dir / "image"
        if ctx.exists():
            shutil.rmtree(ctx)
        shutil.copytree(ROOT, ctx / "frontier", ignore=shutil.ignore_patterns(".git", "old", "runs", "__pycache__", "*.pyc"))
        base = self.m.get("sandbox", {}).get("base_image", "node:22-bookworm")
        (ctx / "Dockerfile").write_text(dockerfile(self.h, base))
        return warnings

    def provider_names(self) -> list[str]:
        return [f"{self.name}-{p.stem.removeprefix('frontier-')}" for p in sorted(self.dir.glob("frontier-*.yaml"))]

    def setup(self) -> None:
        for prof in sorted(self.dir.glob("frontier-*.yaml")):
            self._run(["openshell", "profile", "import", "-f", str(prof)], check=False)
            cred = json.loads(prof.read_text())["credentials"][0]["env_vars"][0]
            if not self.dry_run and not os.environ.get(cred):
                raise RuntimeError(f"{cred} has no value on the host: OpenShell injects it from there. Give it an env:, "
                                   f"file: or cmd: source in the mission's [secrets]")
            self._run(["openshell", "provider", "create", "--name", f"{self.name}-{prof.stem.removeprefix('frontier-')}",
                       "--type", prof.stem, "--credential", cred], check=False)
        # OpenShell 0.1.8 no longer builds a Dockerfile given to --from (checked live 2026-11-15): the image is built
        # with the container engine the gateway uses, and passed by name
        self._run([self.engine, "build", "-t", self.image, str(self.dir / "image")])
        args = ["openshell", "sandbox", "create", "--name", self.name, "--from", self.image,
                "--policy", str(self.dir / "policy.yaml"), "--detach"]
        for p in self.provider_names():
            args += ["--provider", p]
        res = self.m.get("sandbox", {})
        if res.get("cpu"):
            args += ["--cpu", str(res["cpu"])]
        if res.get("memory"):
            args += ["--memory", str(res["memory"])]
        self._run(args + ["--", "sleep", "infinity"])
        self.push()

    def push(self) -> None:
        """Workspace and state into the sandbox (uploads cannot be combined with the main command)."""
        self._run(["openshell", "sandbox", "upload", self.name, str(self.info["workspace"]), IN_WS, "--no-git-ignore"])
        self._run(["openshell", "sandbox", "upload", self.name, str(self.info["state"]), IN_STATE, "--no-git-ignore"])

    def session_command(self, n: int) -> list[str]:
        return ["openshell", "sandbox", "exec", "-n", self.name, "--workdir", IN_WS, "--",
                f"{IN_ROOT}/bin/frontier", "mission", "session", IN_STATE, str(n)]

    def pull(self) -> None:
        """Workspace and state back to the host (the host's dashboard, watchdog and commits read them)."""
        self._run(["openshell", "sandbox", "download", self.name, IN_WS, str(self.info["workspace"])], check=False)
        self._run(["openshell", "sandbox", "download", self.name, IN_STATE, str(self.info["state"])], check=False)
        f = pathlib.Path(self.info["state"]) / "mission.json"
        if not self.dry_run and f.exists():  # the session inside rewrote its workspace path: restore the host's
            try:
                s = json.loads(f.read_text())
            except json.JSONDecodeError:
                return
            if s.get("workspace") != str(self.info["workspace"]):
                s["workspace"] = str(self.info["workspace"])
                f.write_text(json.dumps(s, indent=1) + "\n")

    def teardown(self) -> None:
        self.pull()
        self._run(["openshell", "sandbox", "delete", self.name], check=False)
        self._run([self.engine, "rmi", "-f", self.image], check=False)
        for p in self.provider_names():
            self._run(["openshell", "provider", "delete", p], check=False)
