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

from .http import request

CARD_FIELDS = {"model": ["license", "library_name"], "dataset": ["license"], "space": ["sdk"]}


def _token() -> str:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""


def _api():
    try:
        from huggingface_hub import HfApi
    except ImportError as e:
        raise RuntimeError("huggingface_hub is not installed: pip install huggingface_hub") from e
    return HfApi(token=_token())


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
    if not _token():
        return "error: no HF_TOKEN in the environment"
    code, text = request("GET", "https://huggingface.co/api/whoami-v2", {"Authorization": f"Bearer {_token()}"})
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
    repo_id = f"{ns}/{repo_name}"
    visibility = settings.get("publish_visibility", "private")
    kw = {"space_sdk": space_sdk} if repo_type == "space" else {}
    api.create_repo(repo_id, repo_type=repo_type, private=(visibility != "public"), exist_ok=True, **kw)
    info = api.upload_folder(repo_id=repo_id, folder_path=str(path), repo_type=repo_type,
                             commit_message=commit_message or f"Upload {folder}", ignore_patterns=[".git/*", "__pycache__/*"])
    prefix = {"model": "", "dataset": "datasets/", "space": "spaces/"}[repo_type]
    url = f"https://huggingface.co/{prefix}{repo_id}"
    rec = {"t": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "repo_id": repo_id, "type": repo_type,
           "url": url, "visibility": visibility, "folder": folder, "commit": getattr(info, "oid", None)}
    f = ctx.run.log_dir / "publications.json"
    items = json.loads(f.read_text()) if f.exists() else []
    f.write_text(json.dumps(items + [rec], indent=1) + "\n")
    ctx.run.log(event="hf_publish", **rec)
    return json.dumps(rec, indent=1)
