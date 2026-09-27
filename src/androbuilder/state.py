"""Shared CLI state and config loading helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config, ui
from .config import Config


@dataclass
class State:
    project_dir: Path
    config_path: Path | None
    verbose: bool = False
    no_progress: bool = False
    no_cache: bool = False
    download_from: str = "auto"
    dry_run: bool = False
    profile: str | None = None
    region: str | None = None


def load(state: State) -> Config:
    ui.configure(verbose=state.verbose, no_progress=state.no_progress)
    overrides: dict = {"no_cache": state.no_cache}
    if state.profile:
        overrides["profile"] = state.profile
    if state.region:
        overrides["region"] = state.region
    return config.load_config(state.project_dir, state.config_path, overrides)
