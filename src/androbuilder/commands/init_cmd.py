"""`init` — scaffold ``.androbuilder.toml`` for a project (and global config)."""
from __future__ import annotations

from pathlib import Path

from .. import config, ui
from ..config import PROJECT_CONFIG_NAME, global_config_path
from ..state import State

_LEGACY_MAP = {
    "REGION": ("aws", "region"),
    "AMI_ID": ("compute", "ami_id"),
    "SUBNET_ID": ("compute", "subnet_id"),
    "KEY_NAME": ("compute", "key_name"),
    "SG_NAME": ("compute", "security_group"),
    "IAM_ROLE_NAME": ("compute", "instance_profile"),
    "INSTANCE_NAME": ("compute", "instance_tag"),
    "S3_BUCKET": ("storage", "s3_bucket"),
    "CACHE_VOLUME_NAME": ("cache", "volume_name"),
    "CACHE_VOLUME_SIZE": ("cache", "volume_size"),
    "REPO_NAME": ("project", "repo_name"),
    "ANDROID_PACKAGE_NAME": ("project", "package_name"),
    "KEYSTORE_FILE": ("signing", "keystore"),
    "KEYSTORE_ALIAS": ("signing", "alias"),
    "KEYSTORE_PASSWORD": ("signing", "store_password"),
    "KEYSTORE_KEY_PASSWORD": ("signing", "key_password"),
}


def _read_legacy_env(path: Path) -> dict:
    """Map a legacy ``scripts/.env`` into the new dotted structure."""
    out: dict = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k == "INSTANCE_TYPES":
            out.setdefault("compute", {})["instance_types"] = [
                t.strip() for t in v.split(",") if t.strip()
            ]
            continue
        if k in _LEGACY_MAP:
            section, key = _LEGACY_MAP[k]
            out.setdefault(section, {})[key] = v
    return out


def _ask(prompt: str, default: str = "", yes: bool = False) -> str:
    if yes:
        return default
    ans = input(f"  {prompt} [{default}]: ").strip()
    return ans or default


def _ensure_gitignore(project_dir: Path) -> None:
    gi = project_dir / ".gitignore"
    entry = PROJECT_CONFIG_NAME
    text = gi.read_text() if gi.exists() else ""
    if entry not in text.splitlines():
        with open(gi, "a") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write(f"\n# androbuilder secrets\n{entry}\n")
        ui.ok(f"Added {entry} to .gitignore")


def run_init(
    state: State,
    *,
    yes: bool = False,
    force: bool = False,
    from_env: str | None = None,
    reconfigure_global: bool = False,
) -> None:
    project_dir = state.project_dir
    ui.header("androbuilder init")

    cfp = project_dir / PROJECT_CONFIG_NAME
    if cfp.exists() and not force:
        ui.warn(f"{cfp} already exists. Re-run with --force to overwrite.")

    legacy_path = Path(from_env) if from_env else (project_dir / "scripts" / ".env")
    legacy = _read_legacy_env(legacy_path)
    if legacy:
        ui.ok(f"Imported defaults from {legacy_path}")

    repo = legacy.get("project", {}).get("repo_name") or project_dir.name
    pkg = legacy.get("project", {}).get("package_name") or config.detect_package_name(project_dir)

    # ── Global config (shared infra), only if missing ────────────────────
    gpath = global_config_path()
    if reconfigure_global or not gpath.exists():
        ui.subheader("Shared AWS/infra settings (global)")
        g = legacy
        global_data = {
            "aws": {
                "region": _ask("AWS region", g.get("aws", {}).get("region", "us-east-1"), yes),
                "profile": _ask("AWS profile", g.get("aws", {}).get("profile", "default"), yes),
            },
            "compute": {
                "ami_id": _ask("Builder AMI id", g.get("compute", {}).get("ami_id", ""), yes),
                "subnet_id": _ask("Subnet id", g.get("compute", {}).get("subnet_id", ""), yes),
                "key_name": _ask("SSH key name", g.get("compute", {}).get("key_name", "androbuilder"), yes),
                "security_group": _ask(
                    "Security group name", g.get("compute", {}).get("security_group", "androbuilder"), yes
                ),
                "instance_profile": _ask(
                    "IAM instance profile",
                    g.get("compute", {}).get("instance_profile", "androbuilder-s3"),
                    yes,
                ),
                "instance_types": g.get("compute", {}).get(
                    "instance_types", ["c7i.2xlarge", "c8i.2xlarge", "c6i.2xlarge"]
                ),
            },
        }
        config.write_project_config(gpath, global_data)
        ui.ok(f"Wrote global config {gpath}")

    # ── Project config ───────────────────────────────────────────────────
    ui.subheader("Project settings")
    s = legacy.get("signing", {})
    project_data = {
        "project": {
            "repo_name": _ask("Repo name", repo, yes),
            "package_name": _ask("Android package name", pkg, yes),
        },
        "compute": {
            "instance_tag": _ask(
                "Instance tag",
                legacy.get("compute", {}).get("instance_tag", f"{repo}-builder"),
                yes,
            ),
        },
        "storage": {
            "s3_bucket": _ask(
                "S3 bucket", legacy.get("storage", {}).get("s3_bucket", f"{repo}-builds"), yes
            )
        },
        "cache": {
            "volume_name": _ask(
                "Cache volume name", legacy.get("cache", {}).get("volume_name", f"{repo}-cache"), yes
            ),
            "volume_size": int(legacy.get("cache", {}).get("volume_size", 30) or 30),
            "enabled": True,
        },
        "signing": {
            "keystore": _ask("Keystore file", s.get("keystore", "release.keystore"), yes),
            "alias": _ask("Keystore alias", s.get("alias", ""), yes),
            "store_password": _ask("Keystore store password", s.get("store_password", ""), yes),
            "key_password": _ask("Keystore key password", s.get("key_password", ""), yes),
        },
        "deploy": {"fastlane_key": "fastlane/play-store-key.json"},
    }
    config.write_project_config(cfp, project_data)
    ui.ok(f"Wrote {cfp}")

    _ensure_gitignore(project_dir)

    ui.console.print()
    ui.console.print("  Next steps:")
    ui.console.print("    androbuilder doctor            # verify config + AWS resources")
    ui.console.print("    androbuilder doctor --provision # create missing key/SG/IAM/bucket")
    ui.console.print("    androbuilder release apk        # build")
