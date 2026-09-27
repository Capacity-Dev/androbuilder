"""`config` — show / get / set project configuration."""
from __future__ import annotations

import tomllib

import tomli_w

from .. import ui
from ..config import PROJECT_CONFIG_NAME
from ..state import State


def _flatten(cfg, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for section in (
        "project", "aws", "compute", "cache", "storage", "signing", "deploy", "build"
    ):
        obj = getattr(cfg, section, None)
        if obj is None:
            continue
        for key, val in vars(obj).items():
            if key in ("store_password", "key_password") and val:
                val = "********"
            out[f"{section}.{key}"] = str(val)
    return out


def run_show(state: State) -> None:
    from ..state import load

    cfg = load(state)
    ui.header("Effective configuration")
    ui.console.print(f"  [bold]source:[/bold] {cfg.config_path or '(no project config)'}")
    for k, v in _flatten(cfg).items():
        ui.console.print(f"  {k:32s} = {v}")


def run_get(state: State, key: str) -> None:
    from ..state import load

    cfg = load(state)
    flat = _flatten(cfg)
    if key not in flat:
        ui.fail(f"Unknown key '{key}'")
    print(flat[key])


def run_set(state: State, key: str, value: str) -> None:
    """Set a project config value (dotted key) and write ``.androbuilder.toml``."""
    if "." not in key:
        ui.fail("Key must be dotted, e.g. 'storage.s3_bucket'")
    section, name = key.split(".", 1)
    path = state.project_dir / PROJECT_CONFIG_NAME

    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

    coerced: object = value
    if name in ("volume_size",):
        try:
            coerced = int(value)
        except ValueError:
            ui.fail(f"'{name}' expects an integer")

    data.setdefault(section, {})[name] = coerced
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    ui.ok(f"{key} = {coerced}  →  {path}")
