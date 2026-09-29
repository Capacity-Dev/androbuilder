"""`publish` — push an already-built artifact to the Play Console (no rebuild)."""
from __future__ import annotations

from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from invoke.exceptions import UnexpectedExit

from .. import deploy, ui
from ..aws import auth, infra, transfer
from ..aws.instances import launch_or_reuse_instance, terminate_instance
from ..state import State

try:
    from botocore.exceptions import LoginRefreshRequired
except ImportError:  # older botocore
    LoginRefreshRequired = BotoCoreError


def run_publish(
    state: State,
    artifact: Path | None = None,
    track: str = "internal",
    release_status: str = "completed",
    validate_only: bool = False,
    package_format: str = "aab",
) -> None:
    from ..state import load

    cfg = load(state)

    ui.console.print()
    label = "validate-only" if validate_only else f"publish → {track}"
    ui.console.print(f"  [bold]androbuilder[/bold] — {label}")
    ui.console.print(f"  {'═' * 44}")

    if not cfg.project.package_name:
        ui.fail("project.package_name is not set — run 'androbuilder init'")
    if not cfg.fastlane_key_path.exists():
        ui.fail(f"Play service account key missing: {cfg.fastlane_key_path}")

    source = str(artifact) if artifact else f"s3://{cfg.storage.s3_bucket}/app-release.{package_format}"
    if artifact and not Path(artifact).exists():
        ui.fail(f"Artifact not found: {artifact}")

    if state.dry_run:
        ui.log("Dry run — resolved source only")
        ui.console.print(f"  artifact : {source}")
        ui.console.print(f"  track    : {track} (status={release_status})")
        ui.console.print(f"  package  : {cfg.project.package_name}")
        return

    auth.ensure_aws_auth(cfg)
    infra.ensure_key(cfg)
    sg_id = infra.ensure_security_group(cfg)
    if not artifact:
        infra.ensure_iam_and_bucket(cfg)  # instance needs s3:GetObject to pull the AAB

    instance_id: str | None = None
    success = False
    try:
        instance_id, ip, _vol = launch_or_reuse_instance(cfg, sg_id)
        conn = transfer.wait_for_ssh(cfg, ip)
        deploy.publish_to_play_store(
            cfg, conn, package_format, track, release_status, validate_only,
            local_artifact=str(artifact) if artifact else None,
        )
        ui.console.print("\n  [green][bold]Publish Successful![/bold][/green]")
        ui.console.print(f"  {'═' * 44}\n")
        success = True
    except KeyboardInterrupt:
        ui.console.print("\n  [yellow]⚠  Publish cancelled by user.[/yellow]")
    except (NoCredentialsError, LoginRefreshRequired) as e:
        ui.console.print(f"\n  [red]✗ AWS credentials error: {e}[/red]")
        ui.console.print("  [yellow]  → Run 'androbuilder login' (or 'aws login') and retry.[/yellow]\n")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        ui.console.print(f"\n  [red]✗ AWS API error ({code}): {msg}[/red]\n")
    except SystemExit:
        raise
    except UnexpectedExit as e:
        ui.console.print(f"\n  [red]✗ Command failed: {deploy.extract_error(e)}[/red]")
    except Exception as e:
        ui.console.print(f"\n  [red]✗ Publish failed: {e}[/red]")
    finally:
        terminate_instance(cfg, instance_id, wait=False)

    if not success:
        raise SystemExit(1)
