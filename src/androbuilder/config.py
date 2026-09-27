"""Two-level configuration: global machine defaults + per-project overrides.

Resolution order (first wins):
  CLI overrides → ``ANDROBUBILDER_*`` env vars → project ``.androbuilder.toml``
  → global ``~/.config/androbuilder/config.toml`` → built-in defaults.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from . import ui

PROJECT_CONFIG_NAME = ".androbuilder.toml"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class AwsConfig:
    region: str = "us-east-1"
    profile: str = "default"


@dataclass
class ComputeConfig:
    ami_id: str = ""
    source_ami: str = ""           # base AMI used by `androbuilder bake-ami`
    bake_instance_type: str = "t3.medium"
    subnet_id: str = ""
    key_name: str = "androbuilder"
    security_group: str = "androbuilder"
    instance_profile: str = "androbuilder-s3"
    instance_tag: str = "androbuilder-builder"
    instance_types: list[str] = field(
        default_factory=lambda: ["c7i.2xlarge", "c8i.2xlarge", "c6i.2xlarge"]
    )


@dataclass
class CacheConfig:
    volume_name: str = ""
    volume_size: int = 30
    mount: str = "/mnt/cache"
    device: str = "/dev/sdf"
    enabled: bool = True


@dataclass
class StorageConfig:
    s3_bucket: str = ""


@dataclass
class SigningConfig:
    keystore: str = "release.keystore"
    alias: str = ""
    store_password: str = ""
    key_password: str = ""
    store_password_env: str = ""
    key_password_env: str = ""

    def resolved_store_password(self) -> str:
        if self.store_password:
            return self.store_password
        if self.store_password_env:
            return os.environ.get(self.store_password_env, "")
        return ""

    def resolved_key_password(self) -> str:
        if self.key_password:
            return self.key_password
        if self.key_password_env:
            return os.environ.get(self.key_password_env, "")
        return self.store_password or os.environ.get(self.store_password_env or "", "")


@dataclass
class DeployConfig:
    fastlane_key: str = "fastlane/play-store-key.json"


@dataclass
class ProjectConfig:
    repo_name: str = ""
    package_name: str = ""


@dataclass
class BuildConfig:
    exclude_dirs: list[str] = field(
        default_factory=lambda: ["node_modules", ".git", ".expo", "android"]
    )
    exclude_exts: list[str] = field(
        default_factory=lambda: [".apk", ".aab", ".gz", ".zip", ".tar", ".tgz", ".rar", ".7z"]
    )


@dataclass
class Config:
    project_dir: Path
    config_path: Path | None
    project: ProjectConfig = field(default_factory=ProjectConfig)
    aws: AwsConfig = field(default_factory=AwsConfig)
    compute: ComputeConfig = field(default_factory=ComputeConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    signing: SigningConfig = field(default_factory=SigningConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)
    build: BuildConfig = field(default_factory=BuildConfig)

    # ── derived paths ────────────────────────────────────────────────────
    @property
    def key_path(self) -> Path:
        return Path.home() / ".ssh" / f"{self.compute.key_name}.pem"

    @property
    def env_file(self) -> Path:
        return self.project_dir / ".env"

    @property
    def keystore_path(self) -> Path:
        return self.project_dir / self.signing.keystore

    @property
    def fastlane_key_path(self) -> Path:
        return self.project_dir / self.deploy.fastlane_key


# ── Paths ─────────────────────────────────────────────────────────────────────

def global_config_path() -> Path:
    override = os.environ.get("ANDROBUBILDER_GLOBAL_CONFIG")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "androbuilder" / "config.toml"


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from *start* until a project marker is found."""
    start = (start or Path.cwd()).resolve()
    for d in [start, *start.parents]:
        if (d / "app.json").exists() or (d / PROJECT_CONFIG_NAME).exists() or (d / "package.json").exists():
            return d
    return start


# ── Loading ───────────────────────────────────────────────────────────────────

def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        ui.fail(f"Invalid TOML in {path}: {e}")
    return {}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_ENV_MAP = {
    "ANDROBUBILDER_REGION": ("aws", "region"),
    "ANDROBUBILDER_PROFILE": ("aws", "profile"),
    "ANDROBUBILDER_AMI_ID": ("compute", "ami_id"),
    "ANDROBUBILDER_SUBNET_ID": ("compute", "subnet_id"),
    "ANDROBUBILDER_KEY_NAME": ("compute", "key_name"),
    "ANDROBUBILDER_SECURITY_GROUP": ("compute", "security_group"),
    "ANDROBUBILDER_INSTANCE_PROFILE": ("compute", "instance_profile"),
    "ANDROBUBILDER_INSTANCE_TAG": ("compute", "instance_tag"),
    "ANDROBUBILDER_S3_BUCKET": ("storage", "s3_bucket"),
    "ANDROBUBILDER_CACHE_VOLUME_NAME": ("cache", "volume_name"),
    "ANDROBUBILDER_CACHE_VOLUME_SIZE": ("cache", "volume_size"),
    "ANDROBUBILDER_REPO_NAME": ("project", "repo_name"),
    "ANDROBUBILDER_PACKAGE_NAME": ("project", "package_name"),
    "ANDROBUBILDER_KEYSTORE": ("signing", "keystore"),
    "ANDROBUBILDER_KEYSTORE_ALIAS": ("signing", "alias"),
    "ANDROBUBILDER_KEYSTORE_PASSWORD": ("signing", "store_password"),
    "ANDROBUBILDER_KEYSTORE_KEY_PASSWORD": ("signing", "key_password"),
    "ANDROBUBILDER_FASTLANE_KEY": ("deploy", "fastlane_key"),
}


def _env_overrides() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for env, (section, key) in _ENV_MAP.items():
        val = os.environ.get(env)
        if val is None:
            continue
        out.setdefault(section, {})[key] = val
    types = os.environ.get("ANDROBUBILDER_INSTANCE_TYPES")
    if types:
        out.setdefault("compute", {})["instance_types"] = [t.strip() for t in types.split(",") if t.strip()]
    if "ANDROBUBILDER_CACHE_VOLUME_SIZE" in os.environ:
        try:
            out["cache"]["volume_size"] = int(out["cache"]["volume_size"])
        except (KeyError, ValueError):
            pass
    return out


def _apply_cli(raw: dict, cli: dict[str, Any]) -> dict:
    for key, val in cli.items():
        if val is None:
            continue
        if key == "region":
            raw.setdefault("aws", {})["region"] = val
        elif key == "profile":
            raw.setdefault("aws", {})["profile"] = val
        elif key == "no_cache":
            raw.setdefault("cache", {})["enabled"] = not val
    return raw


def _to_config(raw: dict[str, Any], project_dir: Path, config_path: Path | None) -> Config:
    def section(name: str, cls):
        data = raw.get(name, {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})

    cfg = Config(
        project_dir=project_dir,
        config_path=config_path,
        project=section("project", ProjectConfig),
        aws=section("aws", AwsConfig),
        compute=section("compute", ComputeConfig),
        cache=section("cache", CacheConfig),
        storage=section("storage", StorageConfig),
        signing=section("signing", SigningConfig),
        deploy=section("deploy", DeployConfig),
        build=section("build", BuildConfig),
    )
    if not cfg.project.repo_name:
        cfg.project.repo_name = project_dir.name
    return cfg


def load_config(
    project_dir: Path | None = None,
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Config:
    """Load and merge global + project + env + CLI configuration."""
    project_dir = (project_dir or find_project_root()).resolve()
    proj_path = config_path or (project_dir / PROJECT_CONFIG_NAME)

    raw: dict[str, Any] = {}
    raw = _deep_merge(raw, _read_toml(global_config_path()))
    raw = _deep_merge(raw, _read_toml(proj_path))
    raw = _deep_merge(raw, _env_overrides())
    raw = _apply_cli(raw, cli_overrides or {})

    if not proj_path.exists():
        ui.warn(f"No {PROJECT_CONFIG_NAME} in {project_dir} — run 'androbuilder init'")
    return _to_config(raw, project_dir, proj_path if proj_path.exists() else None)


# ── App env (.env) ────────────────────────────────────────────────────────────

def load_app_env(project_dir: Path) -> dict[str, str]:
    """Parse the project's ``.env`` into a dict (for forwarding EXPO_PUBLIC_*)."""
    env_file = project_dir / ".env"
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ── Init / detection ──────────────────────────────────────────────────────────

def detect_package_name(project_dir: Path) -> str:
    """Read ``expo.android.package`` (or ``expo.ios.bundleIdentifier``) from app.json."""
    import json

    app_json = project_dir / "app.json"
    if not app_json.exists():
        return ""
    try:
        data = json.loads(app_json.read_text())
    except json.JSONDecodeError:
        return ""
    expo = data.get("expo", {})
    return expo.get("android", {}).get("package", "") or expo.get("ios", {}).get("bundleIdentifier", "")


def write_project_config(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as TOML to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def example_config() -> str:
    return """# androbuilder — per-project configuration
# Generated by `androbuilder init`. Keep this file out of version control.

[project]
repo_name = "my-app"
package_name = "com.example.myapp"

[aws]
region = "us-east-1"
profile = "default"

[compute]
# ami_id / subnet_id / security_group / instance_profile are usually set
# once in ~/.config/androbuilder/config.toml (shared across projects).
instance_types = ["c7i.2xlarge", "c8i.2xlarge", "c6i.2xlarge"]

[storage]
s3_bucket = "my-app-builds"

[cache]
volume_name = "my-app-cache"
volume_size = 30
enabled = true

[signing]
keystore = "release.keystore"
alias = "my-alias"
# Prefer env indirection for CI; either works:
store_password = ""
key_password = ""
# store_password_env = "MYAPP_STORE_PASSWORD"
# key_password_env = "MYAPP_KEY_PASSWORD"

[deploy]
fastlane_key = "fastlane/play-store-key.json"
"""
