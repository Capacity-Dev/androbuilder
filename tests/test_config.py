"""Unit tests for config merging and legacy import."""
from __future__ import annotations

import tarfile
from pathlib import Path

from androbuilder import build, config
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


def test_resolve_env_file_prefers_production_for_release(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / ".env.production").write_text("A=2\n")
    cfg = config._to_config({}, tmp_path, None)
    assert cfg.resolve_env_file("release").name == ".env.production"
    assert cfg.resolve_env_file("debug").name == ".env"


def test_resolve_env_file_explicit_override(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / "custom.env").write_text("A=3\n")
    cfg = config._to_config({"build": {"env_file": "custom.env"}}, tmp_path, None)
    assert cfg.resolve_env_file("release").name == "custom.env"


def test_resolve_env_file_missing_returns_none(tmp_path: Path) -> None:
    cfg = config._to_config({}, tmp_path, None)
    assert cfg.resolve_env_file("release") is None


def test_archive_ships_env_production_as_dot_env(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".env").write_text("A=dev\n")
    (tmp_path / ".env.production").write_text("A=prod\n")
    cfg = config._to_config({}, tmp_path, None)
    out = tmp_path / "src.tar.gz"
    build.create_archive(cfg, str(out), "release")

    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert ".env" in names
        assert ".env.production" in names
        assert tar.extractfile(".env").read().decode() == "A=prod\n"
