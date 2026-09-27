"""EC2 build instance lifecycle and the persistent EBS cache volume."""
from __future__ import annotations

import time

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fabric import Connection

try:
    from botocore.exceptions import LoginRefreshRequired
except ImportError:  # older botocore
    LoginRefreshRequired = BotoCoreError

from .. import ui
from ..config import Config
from . import auth


def _volume_name(cfg: Config) -> str:
    return cfg.cache.volume_name or f"{cfg.project.repo_name}-cache"


def _log_instance_info(instance) -> None:
    info = {
        "ID": instance.id,
        "State": instance.state["Name"],
        "Type": instance.instance_type,
        "AZ": instance.placement["AvailabilityZone"],
        "IP": instance.public_ip_address or "(none)",
        "Launch time": instance.launch_time.strftime("%H:%M:%S"),
    }
    if getattr(instance, "instance_lifecycle", None):
        info["Lifecycle"] = instance.instance_lifecycle
    ui.log("Instance details:")
    for k, v in info.items():
        ui.vlog(f"  {k}: {v}")


# ── Cache volume ──────────────────────────────────────────────────────────────

def ensure_cache_volume(cfg: Config, ec2_client, az: str) -> str:
    name = _volume_name(cfg)
    volumes = ec2_client.describe_volumes(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "status", "Values": ["available", "in-use"]},
        ]
    )["Volumes"]
    if volumes:
        vol = volumes[0]
        ui.ok(f"Cache volume {vol['VolumeId']} ({ui.fmt_size(vol['Size'] * 1024**3)})")
        return vol["VolumeId"]

    ui.log(f"Creating {cfg.cache.volume_size} GB gp3 cache volume …")
    vol = ec2_client.create_volume(
        Size=cfg.cache.volume_size,
        VolumeType="gp3",
        AvailabilityZone=az,
        TagSpecifications=[
            {"ResourceType": "volume", "Tags": [{"Key": "Name", "Value": name}]}
        ],
    )
    ui.ok(f"Cache volume {vol['VolumeId']} created")
    return vol["VolumeId"]


def attach_cache_volume(cfg: Config, ec2_client, volume_id: str, instance_id: str) -> None:
    vol = ec2_client.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]

    for att in vol.get("Attachments", []):
        if att["InstanceId"] == instance_id:
            ui.ok(f"Cache volume already attached ({att['Device']})")
            return

    for att in vol.get("Attachments", []):
        ui.log(f"Detaching from former owner {att['InstanceId']} …")
        ec2_client.detach_volume(VolumeId=volume_id, Force=True)
        ec2_client.get_waiter("volume_available").wait(VolumeIds=[volume_id])

    ui.log(f"Attaching cache volume to {instance_id} …")
    ec2_client.attach_volume(
        VolumeId=volume_id, InstanceId=instance_id, Device=cfg.cache.device
    )

    ui.log("Waiting for block device …")
    started = time.time()
    while time.time() - started < 30:
        atts = ec2_client.describe_volumes(VolumeIds=[volume_id])["Volumes"][0].get("Attachments", [])
        if atts and atts[0].get("Device"):
            ui.ok(f"Cache volume attached as {atts[0]['Device']}")
            return
        time.sleep(3)
    ui.warn("Cache device not confirmed — proceeding anyway")


def _attach_cache_or_terminate(cfg: Config, ec2_client, instance) -> str:
    """Attach cache volume to *instance*; terminate it if setup fails."""
    try:
        vol_id = ensure_cache_volume(cfg, ec2_client, instance.placement["AvailabilityZone"])
        attach_cache_volume(cfg, ec2_client, vol_id, instance.id)
        return vol_id
    except Exception:
        ui.warn(f"Cache setup failed — terminating instance {instance.id}")
        try:
            ec2_client.terminate_instances(InstanceIds=[instance.id])
        except Exception:
            pass
        raise


# ── Instance lifecycle ────────────────────────────────────────────────────────

def launch_or_reuse_instance(cfg: Config, sg_id: str) -> tuple[str, str, str]:
    """Find a reusable instance or launch a new one.

    Returns ``(instance_id, public_ip, cache_volume_id)``.
    """
    ui.header("EC2 Instance")
    ec2 = auth.client(cfg, "ec2")
    ec2r = auth.resource(cfg, "ec2")

    instances = list(
        ec2r.instances.filter(
            Filters=[
                {"Name": "tag:Name", "Values": [cfg.compute.instance_tag]},
                {"Name": "instance-state-name", "Values": ["running", "stopped"]},
            ]
        )
    )

    if instances:
        instance = instances[0]
        lifecycle = getattr(instance, "instance_lifecycle", None) or "on-demand"
        _log_instance_info(instance)

        if instance.state["Name"] == "running":
            ui.log(f"Reusing running instance {instance.id}")
            vol_id = _attach_cache_or_terminate(cfg, ec2, instance)
            return instance.id, instance.public_ip_address, vol_id

        if lifecycle == "spot":
            ui.warn("Stopped spot instance cannot be restarted — terminating …")
            with ui.Timer("Terminate old spot"):
                instance.terminate()
                instance.wait_until_terminated()
        else:
            ui.log(f"Starting stopped instance {instance.id} …")
            with ui.Timer("Start instance"):
                instance.start()
                instance.wait_until_running()
                instance.reload()
            ui.ok(f"Instance running at {instance.public_ip_address}")
            vol_id = _attach_cache_or_terminate(cfg, ec2, instance)
            return instance.id, instance.public_ip_address, vol_id

    def _launch(market: str | None, itype: str) -> str:
        args: dict = {
            "ImageId": cfg.compute.ami_id,
            "InstanceType": itype,
            "KeyName": cfg.compute.key_name,
            "MaxCount": 1,
            "MinCount": 1,
            "SecurityGroupIds": [sg_id],
            "SubnetId": cfg.compute.subnet_id,
            "IamInstanceProfile": {"Name": cfg.compute.instance_profile},
            "BlockDeviceMappings": [
                {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 50, "VolumeType": "gp3"}}
            ],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": cfg.compute.instance_tag}],
                }
            ],
        }
        if market:
            args["InstanceMarketOptions"] = {
                "MarketType": market,
                "SpotOptions": {"SpotInstanceType": "one-time"},
            }
        return ec2.run_instances(**args)["Instances"][0]["InstanceId"]

    for itype in cfg.compute.instance_types:
        ui.log(f"Trying {itype} (spot) …")
        try:
            instance_id = _launch("spot", itype)
            instance = ec2r.Instance(instance_id)
            ui.log(f"Waiting for spot allocation {instance_id} …")
            with ui.Timer("Spot allocation"):
                instance.wait_until_running()
                instance.reload()
            _log_instance_info(instance)
            ui.ok(f"Allocated spot {instance_id} at {instance.public_ip_address}")
            vol_id = _attach_cache_or_terminate(cfg, ec2, instance)
            return instance.id, instance.public_ip_address, vol_id
        except ClientError as e:
            ui.warn(f"Spot failed for {itype}: {e}")

        ui.log(f"Trying {itype} (on-demand) …")
        try:
            instance_id = _launch(None, itype)
            instance = ec2r.Instance(instance_id)
            ui.log(f"Waiting for on-demand allocation {instance_id} …")
            with ui.Timer("On-demand allocation"):
                instance.wait_until_running()
                instance.reload()
            _log_instance_info(instance)
            ui.ok(f"Allocated on-demand {instance_id} at {instance.public_ip_address}")
            vol_id = _attach_cache_or_terminate(cfg, ec2, instance)
            return instance.id, instance.public_ip_address, vol_id
        except ClientError as e:
            ui.warn(f"On-demand failed for {itype}: {e}")

    ui.fail("Could not provision builder on any configuration.")


_TERMINATED: set[str] = set()


def terminate_instance(cfg: Config, instance_id: str | None, wait: bool = False) -> None:
    """Request instance termination (billing stops here) and optionally wait.

    Waiting is skipped by default: billing halts as soon as ``terminate_instances``
    is called, so blocking up to 5 minutes only inflates wall-clock time.
    """
    if not instance_id or instance_id in _TERMINATED:
        return

    ui.log(f"Terminating instance {instance_id} …")
    try:
        ec2 = auth.client(cfg, "ec2")
        ec2.terminate_instances(InstanceIds=[instance_id])
        _TERMINATED.add(instance_id)
    except (NoCredentialsError, LoginRefreshRequired, ClientError) as e:
        ui.warn(f"Instance termination request failed ({e})")
        return

    ui.ok("Instance termination requested — billing halting.")
    if wait:
        try:
            ec2.get_waiter("instance_terminated").wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60}
            )
            ui.ok("Instance terminated. Billing halted.")
        except Exception as e:
            ui.warn(f"Termination confirmation timed out ({e})")


# ── Remote cache setup ────────────────────────────────────────────────────────

def setup_cache_on_remote(cfg: Config, conn: Connection, volume_id: str) -> bool:
    """Mount the EBS cache volume and symlink caches on the instance.

    ``node_modules`` is intentionally NOT placed on the cache volume (that breaks
    React Native CMake builds); only ``~/.gradle`` and the yarn offline cache are.
    """
    if not cfg.cache.enabled or not volume_id:
        ui.warn("Cache disabled — clean build")
        conn.run(f'rm -rf "$HOME/{cfg.project.repo_name}"; mkdir -p "$HOME/{cfg.project.repo_name}"',
                 hide=True, timeout=120)
        return False

    script = """\
VOLUME_ID="%s"
CACHE_DEV=""
CACHE_MNT="%s"

for link in /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_*; do
  [ -L "$link" ] || continue
  case "$link" in
    *"$VOLUME_ID"*) CACHE_DEV=$(readlink -f "$link"); break ;;
  esac
done

if [ -z "$CACHE_DEV" ]; then
  for _ in 1 2 3; do
    for d in /dev/sdf /dev/xvdf /dev/nvme1n1 /dev/nvme2n1; do
      [ -b "$d" ] && CACHE_DEV="$d" && break 2
    done
    sleep 3
  done
fi

if [ -z "$CACHE_DEV" ]; then
  ROOT_SRC=$(findmnt -n -o SOURCE / 2>/dev/null)
  for d in $(lsblk -ndo NAME 2>/dev/null); do
    [ -b "/dev/$d" ] || continue
    echo "$d" | grep -q "p[0-9]$" && continue
    [ "/dev/$d" = "$ROOT_SRC" ] && continue
    [ "/dev/$d" = "/dev/nvme0n1" ] && continue
    CACHE_DEV="/dev/$d" && break
  done
fi

REPO_DIR="$HOME/%s"

if [ -n "$CACHE_DEV" ] && [ -b "$CACHE_DEV" ]; then
  sudo mkdir -p "$CACHE_MNT"
  if ! mount | grep -q "$CACHE_MNT"; then
    sudo mount "$CACHE_DEV" "$CACHE_MNT" 2>/dev/null
    if [ $? -ne 0 ]; then
      sudo mkfs.ext4 -F "$CACHE_DEV" >/dev/null 2>&1
      sudo mount "$CACHE_DEV" "$CACHE_MNT"
      sudo mkdir -p "$CACHE_MNT/gradle" "$CACHE_MNT/yarn" "$CACHE_MNT/expo"
      sudo chown ubuntu:ubuntu "$CACHE_MNT" "$CACHE_MNT/gradle" "$CACHE_MNT/yarn" "$CACHE_MNT/expo"
    fi
  fi
  sudo mkdir -p "$CACHE_MNT/gradle" "$CACHE_MNT/yarn" "$CACHE_MNT/expo" 2>/dev/null || true
  sudo chown ubuntu:ubuntu "$CACHE_MNT/gradle" "$CACHE_MNT/yarn" "$CACHE_MNT/expo" 2>/dev/null || true
  ln -sfn "$CACHE_MNT/gradle" "$HOME/.gradle"
  rm -rf "$REPO_DIR"
  mkdir -p "$REPO_DIR"
  echo CACHE_ACTIVE
else
  rm -rf "$REPO_DIR"
  mkdir -p "$REPO_DIR"
  echo CACHE_MISSING
fi
""" % (volume_id, cfg.cache.mount, cfg.project.repo_name)

    result = conn.run(script, hide=True, warn=True, timeout=120)
    if "CACHE_ACTIVE" in result.stdout:
        ui.ok("Build cache ready (EBS volume mounted)")
        return True
    ui.ok("No cache volume — clean build")
    return False
