"""Google Play upload via fastlane."""
from __future__ import annotations

from fabric import Connection
from invoke.exceptions import UnexpectedExit

from . import ui
from .config import Config

_FASTFILE = """default_platform(:android)
platform :android do
  desc "Upload an artifact to the Play Console"
  lane :deploy do
    upload_to_play_store(
      track: "{track}",
      release_status: "{release_status}",
      skip_upload_metadata: true,
      skip_upload_images: true,
      skip_upload_screenshots: true,
      aab: "{aab}"{validate_only}
    )
  end
end"""


def _fastfile(aab: str, track: str, release_status: str, validate_only: bool = False) -> str:
    """Render the Fastfile.

    The version code is intentionally omitted: ``supply`` reads it from the
    bundle, so the AAB's own ``versionCode`` stays the single source of truth.
    """
    extra = ",\n      validate_only: true" if validate_only else ""
    return _FASTFILE.format(
        aab=aab, track=track, release_status=release_status, validate_only=extra
    )


def _appfile(package_name: str) -> str:
    return f'json_key_file "./play-store-key.json"\npackage_name "{package_name}"\n'


def _write_fastlane_config(
    conn: Connection,
    workdir: str,
    package_name: str,
    aab: str,
    track: str,
    release_status: str,
    validate_only: bool,
) -> None:
    conn.run(f"mkdir -p {workdir}/fastlane")
    conn.run(f"cat << 'EOF' > {workdir}/fastlane/Appfile\n{_appfile(package_name)}EOF")
    conn.run(
        f"cat << 'EOF' > {workdir}/fastlane/Fastfile\n"
        f"{_fastfile(aab, track, release_status, validate_only)}\nEOF"
    )


def _ensure_fastlane(conn: Connection) -> None:
    ui.log("Ensuring fastlane is installed …")
    conn.run(
        "command -v fastlane >/dev/null 2>&1 || "
        "(sudo apt install -y -qq ruby-full build-essential && sudo gem install fastlane -N)",
        hide=True,
        warn=True,
    )


def _ensure_awscli(conn: Connection) -> None:
    conn.run(
        "command -v aws >/dev/null 2>&1 || "
        "(sudo apt update -qq && sudo apt install -y -qq awscli)",
        hide=True,
        warn=True,
    )


def extract_error(exc: Exception) -> str:
    """Return the most useful line from a failed remote command."""
    result = getattr(exc, "result", None)
    text = ""
    if result is not None:
        text = f"{getattr(result, 'stderr', '') or ''}\n{getattr(result, 'stdout', '') or ''}"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if "Google Api Error" in ln or ln.startswith("[!]"):
            return ln
    if lines:
        return lines[-1]
    return str(exc).strip() or type(exc).__name__


def _run_fastlane(conn: Connection, workdir: str, env: dict) -> None:
    try:
        with conn.cd(workdir):
            with ui.Timer("Fastlane deploy"):
                conn.run("fastlane deploy", env=env)
    except UnexpectedExit as e:
        msg = extract_error(e)
        hint = ""
        if "permission" in msg.lower():
            hint = (
                "\n  → Grant the service account (json_key .client_email) access in "
                "Play Console → Users & permissions (e.g. \"Release to testing tracks\")."
            )
        ui.fail(f"Play Store upload failed: {msg}{hint}")


# ── Deploy: upload the AAB produced by a build ────────────────────────────────

def upload_to_play_store(cfg: Config, conn: Connection, build_type: str, env: dict) -> None:
    ui.subheader("Play Store Deploy")
    _ensure_fastlane(conn)
    ui.log("Executing Fastlane deployment …")

    aab = f"app/build/outputs/bundle/{build_type}/app-{build_type}.aab"
    _write_fastlane_config(conn, "android", cfg.project.package_name, aab, "internal", "completed", False)
    conn.run("cp fastlane/play-store-key.json android/play-store-key.json")

    _run_fastlane(conn, "android", env)
    ui.ok("Deployed to Play Console (internal track)")


# ── Publish: push an already-built artifact (no rebuild) ──────────────────────

def publish_to_play_store(
    cfg: Config,
    conn: Connection,
    package_format: str = "aab",
    track: str = "internal",
    release_status: str = "completed",
    validate_only: bool = False,
    local_artifact: str | None = None,
) -> None:
    """Upload an existing artifact to the Play Console from a builder instance.

    The artifact is either pulled from S3 (``s3://<bucket>/app-release.<fmt>``)
    or uploaded from ``local_artifact``.
    """
    ui.subheader("Play Store Publish")
    _ensure_fastlane(conn)

    workdir = "/home/ubuntu/publish"
    name = f"app-release.{package_format}"
    conn.run(f"mkdir -p {workdir}")

    if local_artifact:
        ui.log(f"Uploading {local_artifact} …")
        conn.put(str(local_artifact), remote=f"{workdir}/{name}")
    else:
        _ensure_awscli(conn)
        s3_uri = f"s3://{cfg.storage.s3_bucket}/app-release.{package_format}"
        ui.log(f"Downloading {s3_uri} on the instance …")
        conn.run(f"aws s3 cp {s3_uri} {workdir}/{name} --region {cfg.aws.region}")

    conn.put(str(cfg.fastlane_key_path), remote=f"{workdir}/play-store-key.json")

    _write_fastlane_config(
        conn, workdir, cfg.project.package_name, name, track, release_status, validate_only
    )

    ui.log("Executing Fastlane deployment …")
    _run_fastlane(conn, workdir, {"AWS_DEFAULT_REGION": cfg.aws.region})
    if validate_only:
        ui.ok(f"Play credentials validated (track '{track}', nothing published)")
    else:
        ui.ok(f"Published to Play Console (track '{track}')")
