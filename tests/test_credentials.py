"""Credentials: declared services, declared sources, one resolution at the boundary, nothing written down."""

from __future__ import annotations

import json
import os

import pytest

from frontier import credentials, http, mission, sandbox
from test_mission import _mission


@pytest.fixture
def clean_env(monkeypatch):
    """Every variable a test may set is recorded, so that credentials.load's writes are undone afterwards."""
    for v in ("OPENAI_API_KEY", "GEMINI_API_KEY", "SCW_SECRET_KEY", "RUNPOD_API_KEY", "HF_TOKEN", "LAMBDA_API_KEY",
              "SCW_DEFAULT_PROJECT_ID"):
        monkeypatch.setenv(v, "")
        monkeypatch.delenv(v)
    return monkeypatch


def test_every_source_kind(tmp_path, clean_env):
    (tmp_path / "key").write_text("from-file\n")
    clean_env.setenv("HOLDER", "from-env")
    assert credentials.resolve("env:HOLDER") == "from-env"
    assert credentials.resolve(f"file:{tmp_path / 'key'}") == "from-file"
    assert credentials.resolve("cmd:printf from-cmd | tr a-z A-Z") == "FROM-CMD"  # any secret manager's CLI
    assert credentials.resolve("proxy") is None and credentials.resolve("env:UNSET_VARIABLE") is None
    for bad in ("cmd:exit 3", f"file:{tmp_path / 'none'}", "vault:x"):
        with pytest.raises(ValueError):
            credentials.resolve(bad)


def test_load_gives_standard_names_and_records_only_origins(tmp_path, cfg, clean_env):
    (tmp_path / "scw").write_text("scw-secret-value")
    (tmp_path / "vault").write_text("sk-secret-value")  # stands for a secret manager: the command names, never holds, it
    f = _mission(tmp_path)
    f.write_text(f.read_text() + f'''[secrets]
OPENAI_API_KEY = "cmd:cat {tmp_path / 'vault'}"
SCW_SECRET_KEY = "file:{tmp_path / 'scw'}"
RUNPOD_API_KEY = "proxy"
HF_TOKEN = "cmd:false"
''')
    info = mission.prepare(f, ping=False, offers=False)
    assert os.environ["OPENAI_API_KEY"] == "sk-secret-value" and os.environ["SCW_SECRET_KEY"] == "scw-secret-value"
    origin = info["settings"]["credentials"]
    assert origin["OPENAI_API_KEY"] == "cmd" and origin["SCW_SECRET_KEY"] == "file" and origin["RUNPOD_API_KEY"] == "proxy"
    assert origin["HF_TOKEN"].startswith("error") and not info["settings"]["allow_publish"]
    for root in (info["state"], info["workspace"]):  # values never written down
        for p in root.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                text = p.read_text(errors="replace")
                assert "sk-secret-value" not in text and "scw-secret-value" not in text, p


def test_headers_come_from_the_config(cfg, clean_env, monkeypatch):
    seen = {}
    monkeypatch.setattr(http, "request", lambda method, url, headers=None, body=None, timeout=0: seen.update(headers) or (200, "{}"))
    http.scw_raw("GET", "/x")
    assert seen == {"x-auth-token": credentials.PLACEHOLDER}  # no value here: a proxy injects it
    clean_env.setenv("SCW_SECRET_KEY", "k")
    http.scw_raw("GET", "/x")
    assert seen["x-auth-token"] == "k"  # Scaleway takes the bare key
    assert credentials.auth_header("runpod", cfg) == {"authorization": f"Bearer {credentials.PLACEHOLDER}"}
    assert credentials.auth_header("google", cfg) == {"x-goog-api-key": credentials.PLACEHOLDER}


def test_a_new_cloud_is_a_config_entry(tmp_path, cfg, clean_env, monkeypatch):
    cfg = json.loads(json.dumps(cfg))
    cfg["services"]["lambda"] = {"env": "LAMBDA_API_KEY", "hosts": ["cloud.lambda.ai"], "settings": ["LAMBDA_REGION"]}
    monkeypatch.setattr(mission, "load_config", lambda: cfg)
    clean_env.setenv("LAMBDA_API_KEY", "lam")
    m = {"llm": {"provider": "openai"}, "lambda": {"budget_usd": 1}}
    assert "LAMBDA_API_KEY" in credentials.sources(m, cfg)
    assert credentials.load(m, cfg)["LAMBDA_API_KEY"] == "env"
    prof = sandbox.profiles(m, cfg, [])["frontier-lambda"]
    assert prof["endpoints"][0]["host"] == "cloud.lambda.ai" and prof["credentials"][0]["env_vars"] == ["LAMBDA_API_KEY"]
    assert prof["credentials"][0]["auth_style"] == "bearer"
