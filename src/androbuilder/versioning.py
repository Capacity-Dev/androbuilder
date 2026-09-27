"""App version bumping (expo ``app.json`` + ``.buildnum``)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import ui


def bump_version(project_dir: Path) -> str:
    """Increment the build number and write it into ``app.json``.

    Returns the new version string (``YYYY.M.D.N``).
    """
    ui.header("Versioning")
    ui.log("Bumping app build number …")

    buildnum_file = project_dir / ".buildnum"
    bn = int(buildnum_file.read_text().strip()) + 1 if buildnum_file.exists() else 1
    buildnum_file.write_text(str(bn))

    version_str = f"{datetime.now().strftime('%Y.%-m.%-d')}.{bn}"
    app_json = project_dir / "app.json"
    if not app_json.exists():
        ui.warn("app.json not found — skipping version write")
        return version_str

    with open(app_json) as f:
        config = json.load(f)
    config.setdefault("expo", {})["version"] = version_str
    with open(app_json, "w") as f:
        json.dump(config, f, indent=2)

    ui.ok(f"Version bumped to: [bold]{version_str}[/bold]")
    return version_str
