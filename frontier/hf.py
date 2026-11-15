"""Publishing deliverables on the Hugging Face Hub (models, datasets, static Spaces).

Uses the official `huggingface_hub` client (pip install huggingface_hub: it handles large files) and the token in
HF_TOKEN. Visibility comes from the run's settings (`publish_visibility`, default "private"): publishing publicly is
an outward-facing act that the mission owner chooses. Every publication is recorded in publications.json of the
run's state directory.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re

from .budget import update_json
from .credentials import PLACEHOLDER, auth_header
from .http import request

CARD_FIELDS = {"model": ["license", "library_name"], "dataset": ["license"], "space": ["sdk"]}


def _token() -> str:
    """The token, or a placeholder for a proxy to replace (frontier/credentials.py)."""
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or PLACEHOLDER


def _api():
    try:
        from huggingface_hub import HfApi
    except ImportError as e:
        raise RuntimeError("huggingface_hub is not installed: pip install huggingface_hub") from e
    _bracketless_no_proxy()
    return HfApi(token=_token())


def _bracketless_no_proxy() -> None:
    """httpx refuses a bracketed IPv6 entry in NO_PROXY ("[::1]": InvalidURL, every Hub call fails; race 2026-11-15,
    Kimi Code sets it): the entries are kept without their brackets."""
    for k in ("NO_PROXY", "no_proxy"):
        if "[" in os.environ.get(k, ""):
            os.environ[k] = ",".join(e.strip().strip("[]") for e in os.environ[k].split(","))


def card_problems(folder: pathlib.Path, repo_type: str) -> list[str]:
    """What the repository card (README.md with YAML metadata) lacks; [] when it is complete enough to publish."""
    readme = folder / "README.md"
    if not readme.exists():
        return [f"{repo_type} card missing: {folder.name}/README.md with YAML metadata (see the Hub's card docs)"]
    text = readme.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["README.md has no YAML metadata block between '---' lines at the top"]
    keys = {ln.split(":", 1)[0].strip() for ln in m.group(1).splitlines() if ":" in ln and not ln.startswith(" ")}
    problems = [f"card metadata lacks `{k}`" for k in CARD_FIELDS.get(repo_type, []) if k not in keys]
    if len(text[m.end():].strip()) < 200:
        problems.append("card body is nearly empty: describe content, intended use, how it was produced, limits")
    return problems


def hf_whoami(ctx) -> str:
    code, text = request("GET", "https://huggingface.co/api/whoami-v2", auth_header("huggingface"))
    if code != 200:
        return f"error: HTTP {code} {text[:300]}"
    d = json.loads(text)
    return json.dumps({"name": d.get("name"), "orgs": [o.get("name") for o in d.get("orgs", [])],
                       "token_role": (d.get("auth") or {}).get("accessToken", {}).get("role")}, indent=1)


def hf_publish(ctx, repo_name: str, folder: str, repo_type: str = "model", namespace: str = "",
               commit_message: str = "", space_sdk: str = "static") -> str:
    """Create (if needed) and upload a folder of the workspace to a Hub repository."""
    settings = getattr(ctx.run, "settings", {}) or {}
    if not settings.get("allow_publish", ctx.allow_spend):
        return "refused: publishing is not allowed in this run (no Hugging Face access configured for the mission)"
    if repo_type not in ("model", "dataset", "space"):
        return "error: repo_type must be model, dataset or space"
    path = (ctx.workspace / folder).resolve()
    if not path.is_relative_to(ctx.workspace.resolve()) or not path.is_dir():
        return f"error: {folder} is not a folder of the workspace"
    problems = card_problems(path, repo_type)
    if problems:
        return "refused: fix the card first:\n- " + "\n- ".join(problems)
    api = _api()
    ns = namespace or settings.get("hf_namespace") or api.whoami()["name"]
    repo_id = f"{ns}/{race_name(settings, repo_name)}"
    visibility = settings.get("publish_visibility", "private")
    kw = {"space_sdk": space_sdk} if repo_type == "space" else {}
    api.create_repo(repo_id, repo_type=repo_type, private=(visibility != "public"), exist_ok=True, **kw)
    info = api.upload_folder(repo_id=repo_id, folder_path=str(path), repo_type=repo_type,
                             commit_message=commit_message or f"Upload {folder}", ignore_patterns=[".git/*", "__pycache__/*"])
    prefix = {"model": "", "dataset": "datasets/", "space": "spaces/"}[repo_type]
    url = f"https://huggingface.co/{prefix}{repo_id}"
    rec = {"t": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "repo_id": repo_id, "type": repo_type,
           "url": url, "visibility": visibility, "folder": folder, "commit": getattr(info, "oid", None)}
    rec["race"] = _file_in_race(api, settings, repo_id, repo_type)  # best effort: it never raises
    update_json(ctx.run.log_dir / "publications.json", lambda pubs: pubs.append(rec), [])  # parallel ones all stay
    ctx.run.log(event="hf_publish", **rec)
    return json.dumps(rec, indent=1)


def race_name(settings: dict, name: str) -> str:
    """In a race every repository is named <prefix>-<team>-<name>: teams cannot collide or overwrite each other."""
    prefix = settings.get("hf_prefix")
    if not prefix:
        return name
    head = f"{prefix}-{settings.get('name', 'team')}-"
    return name if name.startswith(head) else head + name


def _file_in_race(api, settings: dict, repo_id: str, repo_type: str) -> dict:
    """Tags of the race on the card, and the repository in the race's private collection. Best effort."""
    out = {}
    if not settings.get("hf_prefix"):
        return out
    try:
        from huggingface_hub import metadata_update

        tags = [settings["hf_prefix"], f"team-{settings.get('name', '')}", f"harness-{settings.get('harness', '')}",
                f"provider-{settings.get('provider', '')}", "frontier-race"]
        from huggingface_hub import RepoCard

        try:  # keep the team's own tags
            own = RepoCard.load(repo_id, repo_type=repo_type, token=_token()).data.to_dict().get("tags") or []
        except Exception:  # noqa: BLE001 - no card yet
            own = []
        tags = list(dict.fromkeys([*own, *tags]))
        metadata_update(repo_id, {"tags": tags}, repo_type=repo_type, overwrite=True, token=_token())
        out["tags"] = tags
    except Exception as e:  # noqa: BLE001
        out["tags_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    try:
        title = settings.get("hf_collection") or settings["hf_prefix"]
        col = api.create_collection(title, namespace=repo_id.split("/")[0], private=True, exists_ok=True)
        api.add_collection_item(col.slug, item_id=repo_id, item_type=repo_type, exists_ok=True)
        out["collection"] = f"https://huggingface.co/collections/{col.slug}"
    except Exception as e:  # noqa: BLE001
        out["collection_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


def publish_delivery(ctx, rec: dict) -> dict:
    """At deliver, in a race: the team's repository as tracked by git (report, code, DELIVERY.md, COMPLIANCE.md,
    DECISIONS.md, COSTS.md) on a private dataset <prefix>-<team>-delivery, with a generated card."""
    import shutil
    import subprocess
    import tempfile

    settings = getattr(ctx.run, "settings", {}) or {}
    if not settings.get("hf_prefix") or not settings.get("allow_publish"):
        return {}
    ws = ctx.workspace
    ls = subprocess.run(["git", "-C", str(ws), "ls-files", "-z", "-co", "--exclude-standard"], capture_output=True, text=True, check=False)
    files = [f for f in ls.stdout.split("\0") if f]  # -z: names with spaces stay whole
    if ls.returncode or not files:  # git busy or broken at that instant: every file but git's own
        files = [str(q.relative_to(ws)) for q in ws.rglob("*") if q.is_file() and ".git" not in q.relative_to(ws).parts]
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "delivery"
        root.mkdir()
        for rel in files:
            src = ws / rel
            if src.is_file() and not rel.startswith((".frontier/", ".git/")):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, root / rel)
        team = settings.get("name", "team")
        card = ["---", "license: other", "tags:", f"- {settings['hf_prefix']}", f"- team-{team}", "- frontier-race", "---", "",
                f"# Delivery of team {team}", "",
                f"Harness `{settings.get('harness')}` on provider `{settings.get('provider')}` "
                f"(tier `{settings.get('tier')}`, reasoning `{settings.get('reasoning')}`).", "",
                f"- Delivered at {rec.get('delivered_at')}, after {rec.get('elapsed_hours')} h.",
                f"- LLM: {rec.get('llm_usd')} USD of {rec.get('llm_cap_usd')}; Runpod: {rec.get('runpod_usd')} USD of "
                f"{rec.get('runpod_cap_usd')}; Scaleway: {rec.get('scaleway_eur')} EUR.",
                f"- Missing deliverables: {', '.join(rec.get('missing') or []) or 'none'}.",
                f"- Other publications: {', '.join(rec.get('publications') or []) or 'none'}.", "",
                "The files are the team's repository at delivery, as tracked by git. See DELIVERY.md for the report, the "
                "code and how to reproduce the results; COSTS.md is written by the framework from its ledgers. "
                "The license of each part is the team's, stated in the repository.", ""]
        if (root / "README.md").exists():
            (root / "README.md").rename(root / "README.team.md")
        (root / "README.md").write_text("\n".join(card))
        api = _api()
        ns = settings.get("hf_namespace") or api.whoami()["name"]
        repo_id = f"{ns}/{race_name(settings, 'delivery')}"
        try:
            api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
            api.upload_folder(repo_id=repo_id, folder_path=str(root), repo_type="dataset", commit_message="Delivery")
        except Exception as e:  # noqa: BLE001 - the delivery itself stands without the copy on the Hub
            return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    return {"repo_id": repo_id, "url": f"https://huggingface.co/datasets/{repo_id}", "files": len(files),
            **_file_in_race(api, settings, repo_id, "dataset")}
