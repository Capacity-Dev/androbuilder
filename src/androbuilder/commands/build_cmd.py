"""`release` / `debug` / `deploy` build flow."""
from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from .. import build, prereqs, ui, versioning
from ..aws import auth
from ..aws.instances import launch_or_reuse_instance, terminate_instance
from ..state import State

try:
    from botocore.exceptions import LoginRefreshRequired
except ImportError:  # older botocore
    LoginRefreshRequired = BotoCoreError


def run_build(
    state: State,
    build_type: str = "release",
    package_format: str = "apk",
    deploy: bool = False,
) -> None:
    from ..state import load

    cfg = load(state)
    artifact = f"app-{build_type}.{package_format}"
    label = "deploy " if deploy else ""
    ui.console.print()
    ui.console.print(f"  [bold]androbuilder[/bold] — {label}{build_type} {package_format}")
    ui.console.print(f"  {'═' * 44}")

    if state.dry_run:
        ui.log("Dry run — showing effective configuration only")
        _print_config(cfg)
        return

    auth.ensure_aws_auth(cfg)
    sg_id, s3_ok = prereqs.check_prereqs(
        cfg, deploy, build_type, package_format, state.download_from
    )

    instance_id: str | None = None
    success = False
    status = "Failed"
    try:
        instance_id, ip, vol = launch_or_reuse_instance(cfg, sg_id)
        versioning.bump_version(cfg.project_dir)
        info = build.sync_and_build(cfg, ip, build_type, package_format, deploy, vol)
        if info:
            build.retrieve_artifact(
                cfg, info, instance_id, s3_ok, state.download_from, build_type, package_format
            )
        ui.console.print("\n  [green][bold]Build Successful![/bold][/green]")
        ui.console.print(f"  {'═' * 44}\n")
        success = True
        status = "Success"
    except KeyboardInterrupt:
        ui.console.print("\n  [yellow]⚠  Build cancelled by user.[/yellow]")
        status = "Cancelled"
    except (NoCredentialsError, LoginRefreshRequired) as e:
        ui.console.print(f"\n  [red]✗ AWS credentials error: {e}[/red]")
        ui.console.print("  [yellow]  → Run 'androbuilder login' (or 'aws login') and retry.[/yellow]\n")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        ui.console.print(f"\n  [red]✗ AWS API error ({code}): {msg}[/red]\n")
    except Exception as e:
        ui.console.print(f"\n  [red]✗ Build failed: {e}[/red]")
    finally:
        terminate_instance(cfg, instance_id, wait=False)
        if not ui.summary_printed():
            ui.print_summary(artifact, status)

    if not success:
        raise SystemExit(1)


def _print_config(cfg) -> None:
    ui.console.print(f"  project_dir : {cfg.project_dir}")
    ui.console.print(f"  repo_name   : {cfg.project.repo_name}")
    ui.console.print(f"  package_name: {cfg.project.package_name}")
    ui.console.print(f"  region      : {cfg.aws.region}  profile: {cfg.aws.profile}")
    ui.console.print(f"  ami_id      : {cfg.compute.ami_id}")
    ui.console.print(f"  subnet_id   : {cfg.compute.subnet_id}")
    ui.console.print(f"  s3_bucket   : {cfg.storage.s3_bucket}")
    ui.console.print(f"  cache_volume: {cfg.cache.volume_name} (enabled={cfg.cache.enabled})")
    ui.console.print(f"  keystore    : {cfg.signing.keystore} alias={cfg.signing.alias}")
