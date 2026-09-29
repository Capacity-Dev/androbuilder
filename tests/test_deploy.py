"""Unit tests for fastlane config rendering and error extraction."""
from __future__ import annotations

from androbuilder import deploy


def test_fastfile_omits_version_code() -> None:
    ff = deploy._fastfile("app-release.aab", "internal", "completed")
    assert "version_code" not in ff
    assert 'track: "internal"' in ff
    assert 'release_status: "completed"' in ff
    assert 'aab: "app-release.aab"' in ff
    assert "validate_only" not in ff


def test_fastfile_validate_only() -> None:
    ff = deploy._fastfile("app-release.aab", "production", "draft", validate_only=True)
    assert "validate_only: true" in ff
    assert 'track: "production"' in ff


def test_appfile_points_to_key() -> None:
    af = deploy._appfile("com.example.app")
    assert 'json_key_file "./play-store-key.json"' in af
    assert 'package_name "com.example.app"' in af


def test_extract_error_prefers_google_api_line() -> None:
    class _Result:
        stderr = ""
        stdout = (
            "[12:24:48]: Called from Fastfile at line 6\n"
            "[12:24:48]: Google Api Error: Invalid request - The caller does not have permission\n"
        )

    class _Err(Exception):
        result = _Result()

    assert "Google Api Error" in deploy.extract_error(_Err())


def test_extract_error_falls_back_to_str() -> None:
    assert deploy.extract_error(ValueError("boom")) == "boom"
