"""androbuilder command-line interface (Typer)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from . import __version__, ui
from .config import find_project_root
from .state import State, load

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Build and deploy Expo/React Native Android apps on ephemeral EC2.\n\n"
        "Typical flow:\n\n"
        "- androbuilder init          scaffold .androbuilder.toml\n"
        "- androbuilder doctor        verify config + AWS\n"
        "- androbuilder release aab   build an artifact\n"
        "- androbuilder publish       push the last build to the Play Console"
    ),
)

Format = Literal["apk", "aab", "deploy"]
DownloadFrom = Literal["auto", "s3", "sftp"]
CacheAction = Literal["info", "delete"]
InstancesAction = Literal["list", "terminate-all", "clean"]


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    project: Path | None = typer.Option(
        None, "-C", "--project", help="Project directory (default: auto-detect from CWD)"
    ),
    config_file: Path | None = typer.Option(
        None, "--config", help="Path to .androbuilder.toml"
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Detailed step output"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Skip the EBS cache volume"),
    download_from: DownloadFrom = typer.Option(
        "auto", "--download-from", help="How to fetch the built artifact"
    ),
    env_file: str | None = typer.Option(
        None, "--env-file",
        help="App env file shipped as .env (default: .env.production for release, else .env)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show config, do nothing"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile"),
    region: str | None = typer.Option(None, "--region", help="AWS region"),
    version: bool = typer.Option(False, "--version", help="Show version and exit"),
) -> None:
    if version:
        typer.echo(f"androbuilder {__version__}")
        raise typer.Exit()

    ui.configure(verbose=verbose, no_progress=no_progress)
    project_dir = (project or find_project_root()).resolve()
    ctx.obj = State(
        project_dir=project_dir,
        config_path=config_file,
        verbose=verbose,
        no_progress=no_progress,
        no_cache=no_cache,
        download_from=download_from,
        env_file=env_file,
        dry_run=dry_run,
        profile=profile,
        region=region,
    )


# ── init / doctor / login ─────────────────────────────────────────────────────

@app.command()
def init(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "-y", "--yes", help="Non-interactive: accept defaults"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
    from_env: Path | None = typer.Option(
        None, "--from-env", help="Import defaults from a legacy scripts/.env"
    ),
    reconfigure_global: bool = typer.Option(
        False, "--reconfigure-global", help="Rewrite the global config too"
    ),
) -> None:
    """Scaffold .androbuilder.toml (and the global config)."""
    from .commands import init_cmd

    init_cmd.run_init(
        ctx.obj, yes=yes, force=force, from_env=str(from_env) if from_env else None,
        reconfigure_global=reconfigure_global,
    )


@app.command()
def doctor(
    ctx: typer.Context,
    provision: bool = typer.Option(
        False, "--provision", help="Create missing AWS resources (key/SG/IAM/bucket)"
    ),
) -> None:
    """Verify configuration and AWS infrastructure."""
    from .commands import doctor as doctor_cmd

    doctor_cmd.run_doctor(ctx.obj, provision=provision)


@app.command()
def login(ctx: typer.Context) -> None:
    """Refresh AWS credentials (runs the AWS login flow)."""
    from .aws import auth

    cfg = load(ctx.obj)
    cmd = auth._login_command(cfg)
    ui.log(f"Running: {cmd}")
    import subprocess

    raise typer.Exit(code=subprocess.call(cmd.split()))


# ── build commands ────────────────────────────────────────────────────────────

@app.command()
def release(
    ctx: typer.Context,
    format: Format = typer.Argument(
        "apk",
        help="Artifact to build: 'apk' | 'aab', or 'deploy' (= aab + Play upload)",
    ),
) -> None:
    """Build a release artifact."""
    from .commands import build_cmd

    if format == "deploy":
        build_cmd.run_build(ctx.obj, "release", "aab", deploy=True)
    else:
        build_cmd.run_build(ctx.obj, "release", format)


@app.command()
def debug(
    ctx: typer.Context,
    format: Format = typer.Argument(
        "apk", help="Artifact to build: 'apk' | 'aab' (no 'deploy')"
    ),
) -> None:
    """Build a debug artifact."""
    from .commands import build_cmd

    if format == "deploy":
        ui.fail("debug does not support deploy")
    build_cmd.run_build(ctx.obj, "debug", format)


@app.command()
def deploy(ctx: typer.Context) -> None:
    """Build a release AAB and upload it to the Play Console."""
    from .commands import build_cmd

    build_cmd.run_build(ctx.obj, "release", "aab", deploy=True)


@app.command()
def publish(
    ctx: typer.Context,
    artifact: Path | None = typer.Option(
        None, "--artifact", help="Local AAB/APK to publish (default: pull the last build from S3)"
    ),
    track: str = typer.Option("internal", "--track", help="Play track: internal | alpha | beta | production"),
    release_status: str = typer.Option(
        "completed", "--release-status", help="completed | draft | halted | inProgress"
    ),
    validate_only: bool = typer.Option(
        False, "--validate-only", help="Test Play credentials without publishing"
    ),
) -> None:
    """Publish an already-built artifact to the Play Console (no rebuild)."""
    from .commands import publish_cmd

    publish_cmd.run_publish(
        ctx.obj, artifact=artifact, track=track,
        release_status=release_status, validate_only=validate_only,
    )


# ── bake-ami ──────────────────────────────────────────────────────────────────

@app.command("bake-ami")
def bake_ami(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="AMI name"),
    source_ami: str | None = typer.Option(None, "--source-ami", help="Base AMI id"),
    instance_type: str | None = typer.Option(None, "--instance-type"),
    write_config: bool = typer.Option(
        False, "-w", "--write-config", help="Persist the new AMI id to the global config"
    ),
) -> None:
    """Build a builder AMI with the Android toolchain pre-installed."""
    from .commands import bake_cmd

    bake_cmd.run_bake(
        ctx.obj, name=name, source_ami=source_ami,
        instance_type=instance_type, write_config=write_config,
    )


# ── config subcommands ────────────────────────────────────────────────────────

config_app = typer.Typer(no_args_is_help=True, help="Inspect and edit configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Print the effective (merged) configuration."""
    from .commands import config_cmd

    config_cmd.run_show(ctx.obj)


@config_app.command("get")
def config_get(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dotted key, e.g. storage.s3_bucket"),
) -> None:
    """Print one value."""
    from .commands import config_cmd

    config_cmd.run_get(ctx.obj, key)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dotted key, e.g. storage.s3_bucket"),
    value: str = typer.Argument(..., help="New value"),
) -> None:
    """Set a project value."""
    from .commands import config_cmd

    config_cmd.run_set(ctx.obj, key, value)


# ── housekeeping ──────────────────────────────────────────────────────────────

@app.command()
def cache(
    ctx: typer.Context,
    action: CacheAction = typer.Argument("info", help="info to inspect, delete to remove"),
) -> None:
    """Show or delete the persistent EBS cache volume."""
    from .aws import auth

    cfg = load(ctx.obj)
    name = cfg.cache.volume_name or f"{cfg.project.repo_name}-cache"
    ec2 = auth.client(cfg, "ec2")
    vols = ec2.describe_volumes(
        Filters=[{"Name": "tag:Name", "Values": [name]}]
    )["Volumes"]
    if action == "info":
        if not vols:
            ui.warn(f"No cache volume tagged '{name}'")
            return
        for v in vols:
            att = v.get("Attachments", [{}])[0] if v.get("Attachments") else {}
            ui.console.print(
                f"  {v['VolumeId']}  {v['Size']}GB  {v['State']}  "
                f"az={v['AvailabilityZone']}  attached={att.get('InstanceId', '-')}"
            )
    else:
        if not vols:
            ui.warn(f"No cache volume tagged '{name}'")
            return
        for v in vols:
            ec2.delete_volume(VolumeId=v["VolumeId"])
            ui.ok(f"Deleted {v['VolumeId']}")


@app.command()
def instances(
    ctx: typer.Context,
    action: InstancesAction = typer.Argument(
        "list", help="list running/stopped builders, or terminate-all (alias: clean) to kill them"
    ),
) -> None:
    """List or terminate build instances."""
    from .aws import auth

    cfg = load(ctx.obj)
    ec2 = auth.client(cfg, "ec2")
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [cfg.compute.instance_tag]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]},
        ]
    )
    ids = [
        i["InstanceId"]
        for r in resp["Reservations"]
        for i in r["Instances"]
    ]
    if action == "list":
        if not ids:
            ui.ok("No build instances")
        for i in ids:
            ui.console.print(f"  {i}")
    else:
        if not ids:
            ui.ok("Nothing to terminate")
            return
        ec2.terminate_instances(InstanceIds=ids)
        ui.ok(f"Terminating {len(ids)} instance(s): {', '.join(ids)}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
