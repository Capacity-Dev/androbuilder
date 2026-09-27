"""Unit tests for config merging and legacy import."""
from __future__ import annotations

from pathlib import Path

from androbuilder import config
from androbuilder.commands.init_cmd import _read_legacy_env


def test_deep_merge_nested() -> None:
    base = {"compute": {"region": "us-east-1", "instance_types": ["a"]}}
    over = {"compute": {"instance_types": ["b"]}}
    merged = config._deep_merge(base, over)
    assert merged["compute"]["region"] == "us-east-1"
    assert merged["compute"]["instance_types"] == ["b"]


def test_defaults_from_empty_raw(tmp_path: Path) -> None:
    cfg = config._to_config({}, tmp_path, None)
    assert cfg.aws.region == "us-east-1"
    assert cfg.project.repo_name == tmp_path.name
    assert cfg.signing.keystore == "release.keystore"
    assert "node_modules" in cfg.build.exclude_dirs


def test_signing_password_env_indirection(monkeypatch) -> None:
    monkeypatch.setenv("MYAPP_STORE_PASSWORD", "s3cr3t")
    cfg = config._to_config(
        {"signing": {"store_password_env": "MYAPP_STORE_PASSWORD"}}, Path("/tmp"), None
    )
    assert cfg.signing.resolved_store_password() == "s3cr3t"


def test_legacy_env_import(tmp_path: Path) -> None:
    env = tmp_path / "scripts" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text(
        "REGION=eu-west-1\n"
        "S3_BUCKET=foo-builds\n"
        "INSTANCE_TYPES=c7i.2xlarge,c8i.2xlarge\n"
        "KEYSTORE_PASSWORD=secret\n"
        "ANDROID_PACKAGE_NAME=com.example.app\n"
    )
    data = _read_legacy_env(env)
    assert data["aws"]["region"] == "eu-west-1"
    assert data["storage"]["s3_bucket"] == "foo-builds"
    assert data["compute"]["instance_types"] == ["c7i.2xlarge", "c8i.2xlarge"]
    assert data["signing"]["store_password"] == "secret"
    assert data["project"]["package_name"] == "com.example.app"
