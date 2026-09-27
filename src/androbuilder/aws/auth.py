"""AWS authentication: session/client factories and an expiry-aware guard."""
from __future__ import annotations

import subprocess

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


def ensure_aws_auth(cfg: Config) -> None:
    """Verify credentials; prompt to run the AWS login flow if they're expired."""
    global _AUTH_RETRIED
    sts = client(cfg, "sts")
    try:
        sts.get_caller_identity()
        return
    except (NoCredentialsError, LoginRefreshRequired) as e:
        ui.warn(f"AWS credentials not available: {e}")
    except BotoCoreError as e:
        # e.g. MissingDependencyException for the login provider (botocore[crt])
        ui.fail(
            f"AWS credential provider error: {e}\n"
            "  → Ensure 'botocore[crt]' is installed and run 'androbuilder login'."
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("AccessDeniedException", "ExpiredTokenException", "UnrecognizedClientException"):
            ui.warn(f"AWS access denied ({code}): {e.response['Error']['Message']}")
        else:
            raise

    if _AUTH_RETRIED:
        ui.fail("AWS authentication failed after retry.")

    cmd = _login_command(cfg)
    ui.console.print()
    ui.console.print("  [yellow]⚠[/yellow]  AWS credentials need refreshing.")
    ans = input(f"  Run '{cmd}' now? [Y/n] ").strip().lower()
    if ans not in ("", "y", "yes"):
        ui.fail(f"AWS credentials required. Run '{cmd}' and retry.")

    ui.log(f"Running: {cmd} …")
    if subprocess.call(cmd.split()) != 0:
        ui.fail(f"'{cmd}' failed. Run it manually, then re-run.")

    _AUTH_RETRIED = True
    boto3.setup_default_session()
    client(cfg, "sts").get_caller_identity()
    ui.ok("AWS credentials refreshed")
