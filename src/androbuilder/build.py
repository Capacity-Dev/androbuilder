"""Create the source archive, sync it to EC2, and run the remote build."""
from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from fabric import Connection
from tqdm import tqdm

from . import ui
from .aws import transfer
from .aws.instances import setup_cache_on_remote
from .config import Config, load_app_env

__all__ = ["collect_files", "create_archive", "build_env", "ensure_android_sdk", "sync_and_build"]


# ── Archive ───────────────────────────────────────────────────────────────────

def collect_files(src: Path, exclude_dirs: set[str], exclude_exts: set[str]) -> list[tuple[str, str]]:
    """Return ``(abs_path, rel_path)`` for every file to archive."""
    files: list[tuple[str, str]] = []
    for root, dirs, fnames in os.walk(src):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        rel_root = os.path.relpath(root, src)
        for f in fnames:
            if os.path.splitext(f)[1] in exclude_exts:
                continue
            fpath = os.path.join(root, f)
            rel = os.path.join(rel_root, f) if rel_root != "." else f
            files.append((fpath, rel))
    return files


def create_archive(cfg: Config, out_path: str) -> int:
    """Create the source ``.tar.gz``. Returns the compressed size in bytes."""
    exclude_dirs = set(cfg.build.exclude_dirs)
    exclude_exts = set(cfg.build.exclude_exts)
    files = collect_files(cfg.project_dir, exclude_dirs, exclude_exts)
    raw_size = sum(os.path.getsize(f[0]) for f in files)

    if os.path.exists(out_path):
        os.remove(out_path)

    ui.log(f"Compressing {len(files)} files ({ui.fmt_size(raw_size)} raw)...")
    if ui.VERBOSE:
        for _, rel in files[:30]:
            ui.vlog(f"  {rel}")
        if len(files) > 30:
            ui.vlog(f"  … and {len(files) - 30} more")

    with ui.Timer("Compression"):
        with tarfile.open(out_path, "w:gz") as tar:
            for fpath, rel in tqdm(
                files, desc="Compressing", unit="files", disable=ui.NO_PROGRESS
            ):
                tar.add(fpath, arcname=rel)

    size = os.path.getsize(out_path)
    ratio = (size / raw_size * 100) if raw_size else 0
    ui.ok(f"Archive: {ui.fmt_size(size)} ({ratio:.1f}% of original)")
    return size


# ── Build environment ─────────────────────────────────────────────────────────

def build_env(cfg: Config) -> dict[str, str]:
    env = {
        "ANDROID_HOME": "/opt/android-sdk",
        "AWS_DEFAULT_REGION": cfg.aws.region,
        "YARN_CACHE_FOLDER": f"{cfg.cache.mount}/yarn",
    }
    for k, v in load_app_env(cfg.project_dir).items():
        if k.startswith("EXPO_PUBLIC_"):
            env[k] = v
    if ui.VERBOSE:
        ui.vlog(f"Build env: {', '.join(sorted(env))}")
    return env


def ensure_android_sdk(conn: Connection, env: dict, repo_name: str) -> None:
    """Install the Android SDK platform/build-tools required by the generated project."""
    script = """\
cd "$HOME/%s/android" 2>/dev/null || exit 0

COMPILE_SDK=$(grep -rhoE 'compileSdk(VERSION)?[ _=]*[0-9]+' app/build.gradle build.gradle 2>/dev/null | grep -oE '[0-9]+' | sort -u | tail -1)
BUILD_TOOLS=$(grep -rhoE 'buildToolsVersion[ =]*"[0-9.]+"' app/build.gradle 2>/dev/null | grep -oE '[0-9.]+' | sort -u | tail -1)

[ -z "$COMPILE_SDK" ] && [ -z "$BUILD_TOOLS" ] && exit 0

SDKM=
for c in "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "$ANDROID_HOME/cmdline-tools/bin/sdkmanager" "$ANDROID_HOME/tools/bin/sdkmanager"; do
  [ -x "$c" ] && SDKM="$c" && break
done
[ -z "$SDKM" ] && exit 0

NEEDED=""
[ -n "$COMPILE_SDK" ] && NEEDED="$NEEDED platforms;android-$COMPILE_SDK"
[ -n "$BUILD_TOOLS" ] && NEEDED="$NEEDED build-tools;$BUILD_TOOLS"

echo "Required SDK: $NEEDED"
for pkg in $NEEDED; do
  "$SDKM" --list 2>/dev/null | grep -q "$pkg" || "$SDKM" --list 2>/dev/null | grep -q " $pkg "
  if [ $? -eq 0 ]; then
    echo "  Installing $pkg …"
    yes | "$SDKM" "$pkg" >/dev/null 2>&1 || true
  fi
done
""" % repo_name
    result = conn.run(script, hide=True, warn=True, timeout=300, env=env)
    for line in result.stdout.splitlines():
        if line.strip() and not line.startswith(("Installing", "Required", "Warning", "Info")):
            ui.vlog(line.strip())
    ui.vlog("Android SDK components verified")


# ── Remote build orchestration ────────────────────────────────────────────────

def _patch_gradle_signing(cfg: Config, conn: Connection) -> None:
    """Insert the release signing config into android/app/build.gradle."""
    ks = cfg.signing
    store_pass = ks.resolved_store_password()
    key_pass = ks.resolved_key_password()
    if not (store_pass and key_pass and ks.alias):
        ui.fail("Signing config incomplete (keystore/alias/passwords). Run 'androbuilder init'.")

    release_block = (
        "\\n        release {\\n"
        f"            storeFile file('{ks.keystore}')\\n"
        f"            storePassword '{store_pass}'\\n"
        f"            keyAlias '{ks.alias}'\\n"
        f"            keyPassword '{key_pass}'\\n"
        "        }"
    )
    patch_lines = [
        "with open('android/app/build.gradle') as f: c = f.read()",
        'release = "' + release_block + '"',
        "c = c.replace(\"keyPassword 'android'\\n        }\", \"keyPassword 'android'\\n        }\" + release)",
        "c = c.replace('signingConfig signingConfigs.debug', 'signingConfig signingConfigs.release')",
        "with open('android/app/build.gradle', 'w') as f: f.write(c)",
    ]
    patch = "\n".join(patch_lines) + "\n"
    conn.run(f"cat << 'PYEOF' > /tmp/patch_gradle.py\n{patch}PYEOF\npython3 /tmp/patch_gradle.py")
    ui.vlog(f"Signing config injected ({ks.keystore} / {ks.alias})")


def _patch_gradle_versioncode(cfg: Config, conn: Connection) -> None:
    """Make versionCode read from a -P property (keeps build.gradle stable → cache hits)."""
    conn.run(
        '''python3 -c "
import re
with open('android/app/build.gradle', 'r') as f: c = f.read()
c = re.sub(r'versionCode\\\\s+\\\\d+', \\\"versionCode project.findProperty('versionCode') as Integer ?: 1\\\", c)
with open('android/app/build.gradle', 'w') as f: f.write(c)
"'''
    )
    ui.vlog("versionCode set to read from Gradle -P property")


def sync_and_build(
    cfg: Config,
    ip: str,
    build_type: str,
    package_format: str,
    deploy_mode: bool,
    cache_volume_id: str = "",
) -> str:
    """Sync the project to EC2, build it, and return the artifact path."""
    repo = cfg.project.repo_name
    ui.header("Remote Build")
    ui.log(f"Connecting to {ip} …")
    conn = transfer.wait_for_ssh(cfg, ip)

    ui.subheader("Build Cache")
    setup_cache_on_remote(cfg, conn, cache_volume_id)

    # ── Archive & upload ─────────────────────────────────────────────────
    ui.subheader("Sync to EC2")
    local_tar = f"/tmp/{repo}.tar.gz"
    remote_tar = f"/home/ubuntu/{repo}.tar.gz"
    create_archive(cfg, local_tar)
    transfer.upload_to_ec2(conn, local_tar, remote_tar)

    ui.log("Extracting archive on EC2 …")
    with ui.Timer("Extract on EC2"):
        conn.run(f"tar -xzf {remote_tar} -C ~/{repo}/ && rm {remote_tar}")
    ui.ok("Extraction complete")
    os.remove(local_tar)

    # ── Play Store key (deploy only; validated in doctor) ────────────────
    if deploy_mode:
        conn.run(f"mkdir -p ~/{repo}/fastlane")
        conn.put(str(cfg.fastlane_key_path), remote=f"/home/ubuntu/{repo}/fastlane/play-store-key.json")
        ui.ok("Play Store key uploaded")

    env = build_env(cfg)

    with conn.cd(f"~/{repo}"):
        ui.log("Running: yarn install --frozen-lockfile")
        with ui.Timer("yarn install"):
            conn.run("yarn install --frozen-lockfile", env=env)

        ui.log("Running: npx expo prebuild --platform android --no-install")
        with ui.Timer("expo prebuild"):
            conn.run("npx expo prebuild --platform android --no-install", env=env)

        ensure_android_sdk(conn, env, repo)

        conn.run(f"cp ~/{repo}/{cfg.signing.keystore} ~/{repo}/android/app/{cfg.signing.keystore}")
        _patch_gradle_signing(cfg, conn)
        _patch_gradle_versioncode(cfg, conn)

        with conn.cd("android"):
            conn.run("echo 'sdk.dir=/opt/android-sdk' > local.properties")
            gradle_task = (
                f"bundle{build_type.capitalize()}"
                if package_format == "aab"
                else f"assemble{build_type.capitalize()}"
            )
            artifact_dir = "bundle" if package_format == "aab" else "apk"
            artifact_path = (
                f"android/app/build/outputs/{artifact_dir}/{build_type}/"
                f"app-{build_type}.{package_format}"
            )
            ts = str(int(time.time()))
            ui.log(f"Running: ./gradlew {gradle_task} (versionCode {ts}) …")
            with ui.Timer("Gradle build"):
                conn.run(
                    f"./gradlew {gradle_task} --build-cache --parallel --max-workers=8 "
                    f"--daemon -PversionCode={ts}",
                    env=env,
                )
            ui.ok(f"Build finished → {artifact_path}")

        # ── S3 upload ────────────────────────────────────────────────────
        ui.subheader("Upload to S3")
        ui.log("Ensuring AWS CLI is available …")
        conn.run(
            "command -v aws >/dev/null 2>&1 || "
            "(sudo apt update -qq && sudo apt install -y -qq awscli)",
            hide=True,
            warn=True,
        )
        s3_key = f"app-{build_type}.{package_format}"
        s3_uri = f"s3://{cfg.storage.s3_bucket}/{s3_key}"
        ui.log(f"Uploading {s3_uri} …")
        with ui.Timer("S3 upload"):
            conn.run(f"aws s3 cp {artifact_path} {s3_uri} --region {cfg.aws.region}", env=env)
        ui.ok(f"Artifact uploaded to {s3_uri}")

        # ── Deploy (fastlane) ────────────────────────────────────────────
        if deploy_mode:
            from . import deploy as _deploy

            _deploy.upload_to_play_store(cfg, conn, build_type, env)

    # ── Artifact retrieval is left to the caller ─────────────────────────
    # (so the instance can be released as early as possible — e.g. terminated
    # before a direct S3 download).
    if deploy_mode:
        conn.close()
        return None

    return {
        "conn": conn,
        "s3_key": s3_key,
        "remote_src": f"/home/ubuntu/{repo}/{artifact_path}",
        "artifact_path": artifact_path,
    }


def retrieve_artifact(
    cfg: Config,
    info: dict,
    instance_id: str,
    s3_ok: bool,
    download_from: str,
    build_type: str,
    package_format: str,
) -> None:
    """Fetch the built artifact, releasing the instance as early as possible.

    S3 mode: terminate the instance first, then download directly from the bucket
    (billing stops while the local download runs). SFTP mode: pull first, then
    terminate.
    """
    from .aws.instances import terminate_instance

    conn = info["conn"]
    local_dest = str(cfg.project_dir / f"app-{build_type}.{package_format}")

    if download_from == "sftp":
        mode = "sftp"
    elif download_from == "s3":
        mode = "s3"
    else:
        mode = "s3" if s3_ok else "sftp"

    ui.subheader("Download Artifact")
    if mode == "s3":
        ui.log("Source: S3 direct — terminating instance first")
        terminate_instance(cfg, instance_id, wait=False)
        try:
            conn.close()
        except Exception:
            pass
        try:
            transfer.download_from_s3(cfg, info["s3_key"], local_dest)
        except Exception as e:
            raise RuntimeError(
                f"S3 download failed after instance termination ({e}); "
                "re-run to rebuild (SFTP fallback unavailable)"
            ) from e
    else:
        ui.log("Source: SFTP over EC2")
        transfer.download_from_ec2(cfg, conn, info["remote_src"], local_dest)
        conn.close()
        terminate_instance(cfg, instance_id, wait=False)
