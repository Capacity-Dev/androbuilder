"""Local/remote prerequisite checks run before provisioning an instance."""
from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from . import ui
from .aws import auth, infra
from .config import Config

try:
    from botocore.exceptions import LoginRefreshRequired
except ImportError:  # older botocore
    LoginRefreshRequired = BotoCoreError


def _check_local_s3_download(cfg: Config, key: str) -> bool:
    """Probe whether the local principal can read the bucket.

    ``403`` → lacks ``s3:GetObject`` (use SFTP); ``404`` → authorized, object
    not uploaded yet.
    """
    s3 = auth.client(cfg, "s3")
    bucket = cfg.storage.s3_bucket

    try:
        s3.head_bucket(Bucket=bucket)
    except (NoCredentialsError, LoginRefreshRequired):
        raise
    except ClientError as e:
        code = e.response["Error"].get("Code", "?")
        ui.warn(f"S3 bucket '{bucket}' not reachable locally ({code}) — using SFTP download")
        return False

    try:
        s3.head_object(Bucket=bucket, Key=key)
        ui.ok(f"Local S3 read access confirmed (s3://{bucket}/{key})")
        return True
    except (NoCredentialsError, LoginRefreshRequired):
        raise
    except ClientError as e:
        code = e.response["Error"].get("Code", "?")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey") or status == 404:
            ui.ok("Local S3 read access confirmed (bucket reachable, object pending)")
            return True
        if code in ("403", "AccessDenied") or status == 403:
            ui.warn("Local principal lacks s3:GetObject — using SFTP download")
            return False
        ui.warn(f"S3 read probe inconclusive ({code}) — using SFTP download")
        return False


def check_prereqs(
    cfg: Config,
    deploy_mode: bool = False,
    build_type: str = "release",
    package_format: str = "apk",
    download_from: str = "auto",
) -> tuple[str, bool]:
    """Ensure SSH key, security group, IAM/S3, .env, and build assets exist.

    Returns ``(security_group_id, s3_download_available)``.
    """
    ui.header("Prerequisites")
    ui.log("Checking prerequisites …")

    infra.ensure_key(cfg)
    sg_id = infra.ensure_security_group(cfg)
    infra.ensure_iam_and_bucket(cfg)

    env_file = cfg.resolve_env_file(build_type)
    if env_file is None:
        expected = cfg.build.env_file or (
            ".env.production or .env" if build_type == "release" else ".env"
        )
        ui.fail(f"App env file missing (expected {expected})")
    ui.ok(f"Env file '{env_file.name}' → shipped as .env")

    if not cfg.keystore_path.exists():
        ui.fail(f"Release keystore '{cfg.signing.keystore}' missing from project root!")
    ui.ok(f"Release keystore '{cfg.signing.keystore}' found")

    if deploy_mode:
        if not cfg.fastlane_key_path.exists():
            ui.fail("Deploy requested but fastlane/play-store-key.json is missing!")
        ui.ok("Play Store service account key found")

    if deploy_mode:
        s3_ok = True
    elif download_from == "sftp":
        ui.ok("--download-from sftp: remote SFTP download selected")
        s3_ok = False
    else:
        s3_ok = _check_local_s3_download(cfg, f"app-{build_type}.{package_format}")
        if download_from == "s3" and not s3_ok:
            ui.warn("--download-from s3 forced, but local S3 read is unavailable")

    ui.ok("All prerequisites met")
    return sg_id, s3_ok
