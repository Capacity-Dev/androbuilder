"""`doctor` — verify configuration and AWS infrastructure."""
from __future__ import annotations

from .. import ui
from ..aws import auth, infra
from ..state import State


def run_doctor(state: State, provision: bool = False) -> None:
    from ..state import load

    cfg = load(state)
    ui.header("androbuilder doctor")

    problems: list[str] = []

    # ── Config completeness ──────────────────────────────────────────────
    for label, value in [
        ("compute.ami_id", cfg.compute.ami_id),
        ("compute.subnet_id", cfg.compute.subnet_id),
        ("storage.s3_bucket", cfg.storage.s3_bucket),
        ("project.package_name", cfg.project.package_name),
    ]:
        if value:
            ui.ok(f"{label} = {value}")
        else:
            problems.append(f"{label} is not set")
            ui.warn(f"{label} is not set")

    env_file = cfg.resolve_env_file("release")
    if env_file is not None:
        tag = "" if env_file.name == ".env" else " → shipped as .env"
        ui.ok(f"app env: {env_file.name}{tag}")
    else:
        problems.append("app env missing (.env / .env.production)")
        ui.warn("app env missing (.env / .env.production)")

    if cfg.keystore_path.exists():
        ui.ok(f"keystore '{cfg.signing.keystore}' found")
    else:
        problems.append(f"keystore '{cfg.signing.keystore}' missing")
        ui.warn(f"keystore '{cfg.signing.keystore}' missing")

    if not cfg.signing.alias or not cfg.signing.resolved_store_password():
        problems.append("signing alias/password not set")
        ui.warn("signing alias/password not set")

    if cfg.config_path is None:
        problems.append("no .androbuilder.toml")
        ui.warn("no .androbuilder.toml — run 'androbuilder init'")

    # ── AWS ──────────────────────────────────────────────────────────────
    auth_ok, auth_reason = auth.credentials_ok(cfg)
    if auth_ok:
        ident = auth.client(cfg, "sts").get_caller_identity()
        ui.ok(f"AWS identity: {ident.get('Arn')}")
    else:
        problems.append(f"AWS auth: {auth_reason}")
        ui.warn(f"AWS auth: {auth_reason}")

    if not auth_ok:
        ui.warn("skipping AWS resource checks — run 'androbuilder login' first")
    elif provision and not problems:
        ui.subheader("Provisioning missing resources")
        infra.ensure_key(cfg)
        infra.ensure_security_group(cfg)
        infra.ensure_iam_and_bucket(cfg)
    else:
        _readonly_aws_checks(cfg, problems)

    # ── Result ───────────────────────────────────────────────────────────
    ui.header("doctor result")
    if problems:
        for p in problems:
            ui.console.print(f"  [red]✗[/red] {p}")
        ui.console.print(
            "\n  Run [bold]androbuilder doctor --provision[/bold] to create "
            "missing AWS resources, or fix the config."
        )
        raise SystemExit(1)
    ui.console.print("  [green]All checks passed.[/green]")


def _readonly_aws_checks(cfg, problems: list[str]) -> None:
    ec2 = auth.client(cfg, "ec2")
    try:
        ec2.describe_key_pairs(KeyNames=[cfg.compute.key_name])
        ui.ok(f"SSH key '{cfg.compute.key_name}' exists")
    except Exception:
        problems.append(f"SSH key '{cfg.compute.key_name}' missing")
        ui.warn(f"SSH key '{cfg.compute.key_name}' missing")

    if cfg.compute.subnet_id:
        try:
            ec2.describe_subnets(SubnetIds=[cfg.compute.subnet_id])
            ui.ok(f"subnet '{cfg.compute.subnet_id}' exists")
        except Exception:
            problems.append(f"subnet '{cfg.compute.subnet_id}' not found")
            ui.warn(f"subnet '{cfg.compute.subnet_id}' not found")

    if cfg.compute.ami_id:
        try:
            ec2.describe_images(ImageIds=[cfg.compute.ami_id])
            ui.ok(f"AMI '{cfg.compute.ami_id}' exists")
        except Exception:
            problems.append(f"AMI '{cfg.compute.ami_id}' not found")
            ui.warn(f"AMI '{cfg.compute.ami_id}' not found")

    if cfg.storage.s3_bucket:
        try:
            auth.client(cfg, "s3").head_bucket(Bucket=cfg.storage.s3_bucket)
            ui.ok(f"S3 bucket '{cfg.storage.s3_bucket}' reachable")
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            problems.append(f"S3 bucket '{cfg.storage.s3_bucket}' unreachable ({code})")
            ui.warn(f"S3 bucket '{cfg.storage.s3_bucket}' unreachable ({code})")
