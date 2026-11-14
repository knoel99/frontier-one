"""Configuration: config/providers.toml, overridable with FRONTIER_CONFIG."""

from __future__ import annotations

import os
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = pathlib.Path(path or os.environ.get("FRONTIER_CONFIG") or ROOT / "config" / "providers.toml")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("limits", {})
    return cfg
