"""Build a reusable 'builder' AMI with the Android toolchain pre-installed.

Installs: JDK 17, Android SDK (cmdline-tools + platforms + build-tools),
Node 22, Yarn, AWS CLI, Ruby + fastlane.
"""
from __future__ import annotations

from . import ui
from .aws import auth, infra
from .aws.transfer import wait_for_ssh
from .config import Config

UBUNTU_SSM_PARAM = (
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
)
CMDLINE_TOOLS_URL = (
    "https://dl.google.com/android/repository/"
    "commandlinetools-linux-11076708_latest.zip"
)

# Android components baked in; the build step installs extra platform/build-tools
# on demand via `ensure_android_sdk`.
ANDROID_PLATFORM = "android-35"
BUILD_TOOLS = "35.0.0"

_INSTALL_SCRIPT = """\
set -e
export DEBIAN_FRONTEND=noninteractive

echo "== Installing base packages =="
sudo apt-get update -y
sudo apt-get install -y openjdk-17-jdk unzip zip git curl wget build-essential ruby-full awscli

echo "== Installing Node 22 + Yarn =="
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g yarn

echo "== Installing Android SDK =="
export ANDROID_HOME=/opt/android-sdk
sudo mkdir -p "$ANDROID_HOME/cmdline-tools"
cd /tmp
wget -q %s -O cmdline.zip
sudo unzip -q cmdline.zip -d "$ANDROID_HOME/cmdline-tools"
sudo rm -rf "$ANDROID_HOME/cmdline-tools/latest"
sudo mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
sudo chown -R ubuntu:ubuntu "$ANDROID_HOME"

SDKM="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
yes | "$SDKM" --licenses >/dev/null 2>&1 || true
"$SDKM" "platform-tools" "platforms;%s" "build-tools;%s" >/dev/null

echo "== Installing fastlane =="
sudo gem install fastlane -N || true

echo "== Persisting environment =="
echo 'export ANDROID_HOME=/opt/android-sdk' | sudo tee /etc/profile.d/android.sh >/dev/null
echo 'export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools' \\
  | sudo tee -a /etc/profile.d/android.sh >/dev/null
echo 'export ANDROID_HOME=/opt/android-sdk' >> "$HOME/.bashrc"

echo "== Baking complete =="
""" % (CMDLINE_TOOLS_URL, ANDROID_PLATFORM, BUILD_TOOLS)


def _resolve_source_ami(cfg: Config, override: str | None) -> str:
    if override:
        return override
    if cfg.compute.source_ami:
        return cfg.compute.source_ami
    ui.log("Resolving latest Ubuntu 22.04 AMI via SSM …")
    ssm = auth.client(cfg, "ssm")
    return ssm.get_parameter(Name=UBUNTU_SSM_PARAM)["Parameter"]["Value"]


def bake_ami(
    cfg: Config,
    *,
    name: str,
    source_ami: str | None = None,
    instance_type: str | None = None,
    write_config: bool = False,
) -> str:
    """Launch a temporary instance, install the toolchain, and register an AMI.

    Returns the new AMI ID.
    """
    ui.header("Bake Builder AMI")
    ec2 = auth.client(cfg, "ec2")
    ec2r = auth.resource(cfg, "ec2")

    infra.ensure_key(cfg)
    sg_id = infra.ensure_security_group(cfg)
    base = _resolve_source_ami(cfg, source_ami)
    itype = instance_type or cfg.compute.bake_instance_type
    ui.log(f"Base AMI: {base}  ({itype})")

    inst = ec2r.create_instances(
        ImageId=base,
        InstanceType=itype,
        KeyName=cfg.compute.key_name,
        MaxCount=1,
        MinCount=1,
        SecurityGroupIds=[sg_id],
        SubnetId=cfg.compute.subnet_id,
        BlockDeviceMappings=[
            {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 50, "VolumeType": "gp3"}}
        ],
        TagSpecifications=[
            {"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "androbuilder-amibaker"}]}
        ],
    )[0]
    ui.log(f"Instance {inst.id} — waiting for running …")
    inst.wait_until_running()
    inst.reload()
    ui.ok(f"Running at {inst.public_ip_address}")

    conn = wait_for_ssh(cfg, inst.public_ip_address)
    ui.log("Installing toolchain (this takes several minutes) …")
    with ui.Timer("Toolchain install"):
        conn.run(_INSTALL_SCRIPT, hide=False, timeout=1800)
    ok_marker = "Baking complete"
    conn.close()
    ui.ok(ok_marker)

    ui.log(f"Creating AMI '{name}' …")
    ami_id = ec2.create_image(
        InstanceId=inst.id,
        Name=name,
        Description="androbuilder base: Ubuntu 22.04 + JDK17 + Android SDK + Node 22 + Yarn + awscli + fastlane",
        NoReboot=False,
    )["ImageId"]
    ui.log(f"AMI {ami_id} — waiting for availability (5-10 min) …")
    ec2.get_waiter("image_available").wait(
        ImageIds=[ami_id], WaiterConfig={"Delay": 30, "MaxAttempts": 60}
    )
    ui.ok(f"AMI {ami_id} available")

    ui.log(f"Terminating build instance {inst.id} …")
    inst.terminate()
    ui.ok("Instance terminated")

    if write_config:
        _write_ami_to_global_config(ami_id)

    print()
    print(f"  {'═' * 44}")
    print(f"  New AMI: {ami_id}")
    print(f"  Set compute.ami_id = \"{ami_id}\" in your project/global config.")
    print(f"  {'═' * 44}\n")
    return ami_id


def _write_ami_to_global_config(ami_id: str) -> None:
    """Persist the baked AMI id into the global config file."""
    import tomllib

    import tomli_w

    from .config import global_config_path

    path = global_config_path()
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    data.setdefault("compute", {})["ami_id"] = ami_id
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    ui.ok(f"Wrote compute.ami_id to {path}")
