"""AWS authentication: session/client factories and an expiry-aware guard."""
from __future__ import annotations

import subprocess
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

try:
    from botocore.exceptions import LoginRefreshRequired
except ImportError:  # older botocore
    LoginRefreshRequired = BotoCoreError

from .. import ui
from ..config import Config

_AUTH_RETRIED = False


def get_session(cfg: Config) -> boto3.Session:
    if cfg.aws.profile:
        try:
            return boto3.Session(profile_name=cfg.aws.profile)
        except Exception:
            pass
    return boto3.Session()


def client(cfg: Config, service: str):
    return get_session(cfg).client(service, region_name=cfg.aws.region)


def resource(cfg: Config, service: str):
    return get_session(cfg).resource(service, region_name=cfg.aws.region)


def _login_command(cfg: Config) -> str:
    profile = cfg.aws.profile or "default"
    return f"aws login --profile {profile}" if profile != "default" else "aws login"


def credentials_ok(cfg: Config) -> tuple[bool, str]:
    """Non-fatal credential probe. Returns (ok, human-readable reason)."""
    try:
        client(cfg, "sts").get_caller_identity()
        return True, "ok"
    except (NoCredentialsError, LoginRefreshRequired):
        return False, "session expired — run 'aws login'"
    except BotoCoreError as e:
        # e.g. MissingDependencyException for the login provider (botocore[crt])
        return False, f"credential provider error — ensure 'botocore[crt]' is installed ({e})"
    except ClientError as e:
        code = e.response["Error"]["Code"]
        return False, f"{code}: {e.response['Error'].get('Message', '')}"


def ensure_aws_auth(cfg: Config) -> None:
    """Fatal guard: verify credentials, offering the AWS login flow when interactive."""
    global _AUTH_RETRIED
    ok, reason = credentials_ok(cfg)
    if ok:
        return

    ui.warn(f"AWS credentials not available: {reason}")
    if _AUTH_RETRIED:
        ui.fail("AWS authentication failed after retry.")

    cmd = _login_command(cfg)
    ui.console.print()
    ui.console.print("  [yellow]⚠[/yellow]  AWS credentials need refreshing.")

    # Never block on a prompt when there is no interactive terminal.
    if not sys.stdin.isatty():
        ui.fail(f"AWS credentials required. Run '{cmd}' and retry.")
    try:
        ans = input(f"  Run '{cmd}' now? [Y/n] ").strip().lower()
    except EOFError:
        ui.fail(f"AWS credentials required. Run '{cmd}' and retry.")
    if ans not in ("", "y", "yes"):
        ui.fail(f"AWS credentials required. Run '{cmd}' and retry.")

    ui.log(f"Running: {cmd} …")
    if subprocess.call(cmd.split()) != 0:
        ui.fail(f"'{cmd}' failed. Run it manually, then re-run.")

    _AUTH_RETRIED = True
    boto3.setup_default_session()
    ok, reason = credentials_ok(cfg)
    if not ok:
        ui.fail(f"AWS authentication failed: {reason}")
    ui.ok("AWS credentials refreshed")
