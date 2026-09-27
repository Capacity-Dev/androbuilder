"""SSH connection + artifact transfers (SFTP, rsync/ssh stream, direct S3)."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fabric import Connection
from tqdm import tqdm

from .. import ui
from ..config import Config
from .auth import client

try:
    from botocore.exceptions import LoginRefreshRequired
except ImportError:  # older botocore
    from botocore.exceptions import BotoCoreError as LoginRefreshRequired


def wait_for_ssh(cfg: Config, ip: str, retries: int = 15, delay: int = 8) -> Connection:
    """Retry the SSH connection until the instance accepts commands."""
    for attempt in range(1, retries + 1):
        try:
            conn = Connection(
                host=ip, user="ubuntu", connect_kwargs={"key_filename": str(cfg.key_path)}
            )
            conn.run("echo ready", hide=True, timeout=5)
            ui.ok("SSH connection established")
            return conn
        except Exception as e:
            if attempt < retries:
                ui.warn(f"SSH not ready ({e}) — retry {attempt}/{retries} in {delay}s")
                time.sleep(delay)
            else:
                ui.fail(f"SSH unavailable after {retries} attempts ({e})")
    raise RuntimeError("unreachable")


def upload_to_ec2(conn: Connection, local: str, remote: str) -> None:
    size = os.path.getsize(local)
    ui.log(f"Uploading to EC2 ({conn.host}) — {ui.fmt_size(size)} …")
    start = time.time()
    conn.put(local, remote=remote)
    ui.ok(f"Uploaded {ui.fmt_size(size)} in {time.time() - start:.1f}s")


# ── Direct S3 download ────────────────────────────────────────────────────────

class _S3Progress:
    """tqdm callback for boto3 multipart downloads."""

    def __init__(self, total: int) -> None:
        self._bar = tqdm(
            total=total, unit="B", unit_scale=True, desc="  Downloading",
            disable=ui.NO_PROGRESS,
        )
        self._seen = 0

    def __call__(self, bytes_transferred: int) -> None:
        delta = bytes_transferred - self._seen
        if delta > 0:
            self._bar.update(delta)
        self._seen = bytes_transferred

    def close(self) -> None:
        try:
            self._bar.close()
        except Exception:
            pass


def download_from_s3(cfg: Config, s3_key: str, local: str, retries: int = 3) -> None:
    """Download the artifact directly from S3 (parallel multipart) with retries."""
    s3 = client(cfg, "s3")
    bucket = cfg.storage.s3_bucket
    ui.log(f"Downloading s3://{bucket}/{s3_key} → {local} …")
    start = time.time()

    progress = None
    try:
        size = s3.head_object(Bucket=bucket, Key=s3_key)["ContentLength"]
        progress = _S3Progress(size)
    except (NoCredentialsError, LoginRefreshRequired):
        raise
    except (ClientError, BotoCoreError):
        pass

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            s3.download_file(bucket, s3_key, local, Callback=progress)
            last_err = None
            break
        except (NoCredentialsError, LoginRefreshRequired):
            if progress is not None:
                progress.close()
            raise
        except (ClientError, BotoCoreError, OSError) as e:
            last_err = e
            if attempt < retries:
                ui.warn(f"S3 download failed ({e}) — retry {attempt}/{retries} …")
                time.sleep(2 * attempt)

    if progress is not None:
        progress.close()
    if last_err is not None:
        raise last_err

    _finish(local, start, "Download from S3")


# ── SFTP / rsync / ssh-stream download ────────────────────────────────────────

def _finish(local: str, start: float, label: str) -> None:
    elapsed = time.time() - start
    size = os.path.getsize(local) if os.path.exists(local) else 0
    ui.ok(f"Downloaded {ui.fmt_size(size)} in {elapsed:.1f}s → {local}")
    ui.timings().append((label, elapsed))


def _ensure_remote_rsync(cfg: Config, conn: Connection) -> bool:
    probe = "command -v rsync >/dev/null 2>&1 && echo yes || echo no"
    if "yes" in conn.run(probe, hide=True, warn=True).stdout:
        return True
    ui.log("Installing rsync on EC2 …")
    conn.run(
        "sudo apt-get update -qq && sudo apt-get install -y -qq rsync",
        hide=True, warn=True, timeout=300,
    )
    return "yes" in conn.run(probe, hide=True, warn=True).stdout


def download_from_ec2(cfg: Config, conn: Connection, remote: str, local: str) -> None:
    """Download from EC2, preferring rsync/ssh stream over paramiko SFTP.

    Paramiko SFTP reads ~32 KB per round-trip; on high-latency links rsync or a
    raw ``ssh cat`` stream (OpenSSH's larger channel window) are far faster.
    """
    ui.log(f"Downloading {remote} from EC2 ({conn.host}) …")
    start = time.time()

    ssh_opts = [
        "-i", str(cfg.key_path),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
    ]
    target = f"ubuntu@{conn.host}"

    if shutil.which("rsync") and _ensure_remote_rsync(cfg, conn):
        e_arg = "ssh " + " ".join(shlex.quote(o) for o in ssh_opts)
        ui.log("Transferring via rsync …")
        rc = subprocess.call(
            ["rsync", "-P", "--partial", "-e", e_arg, f"{target}:{remote}", local]
        )
        if rc == 0 and os.path.exists(local) and os.path.getsize(local) > 0:
            _finish(local, start, "Download via SFTP")
            return
        ui.warn(f"rsync exited {rc} — falling back to ssh stream")
    else:
        ui.warn("rsync unavailable — falling back to ssh stream")

    ui.log("Transferring via ssh stream …")
    try:
        with open(local, "wb") as f:
            rc = subprocess.call(["ssh", *ssh_opts, target, f"cat {shlex.quote(remote)}"], stdout=f)
        if rc == 0 and os.path.exists(local) and os.path.getsize(local) > 0:
            _finish(local, start, "Download via SFTP")
            return
        ui.warn(f"ssh stream exited {rc} — falling back to SFTP")
    except OSError as e:
        ui.warn(f"ssh stream failed ({e}) — falling back to SFTP")

    conn.get(remote, local=local)
    _finish(local, start, "Download via SFTP (paramiko)")
