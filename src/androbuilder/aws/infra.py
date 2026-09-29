"""One-time AWS infrastructure: SSH key, security group, IAM role/policy, S3 bucket."""
from __future__ import annotations

import json
import time

from botocore.exceptions import ClientError

from .. import ui
from ..config import Config
from . import auth


def ensure_key(cfg: Config) -> None:
    """Create the project SSH key pair if it doesn't exist locally/on AWS."""
    ec2 = auth.client(cfg, "ec2")
    key_path = cfg.key_path
    key_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        ec2.describe_key_pairs(KeyNames=[cfg.compute.key_name])
        ui.ok(f"SSH key '{cfg.compute.key_name}' found")
    except ClientError:
        ui.log(f"Creating SSH key pair '{cfg.compute.key_name}' …")
        kp = ec2.create_key_pair(KeyName=cfg.compute.key_name)
        key_path.write_text(kp["KeyMaterial"])
        key_path.chmod(0o600)
        ui.ok(f"Key saved to {key_path}")

    if not key_path.exists():
        ui.fail(
            f"SSH key '{cfg.compute.key_name}' exists on AWS but {key_path} is missing locally. "
            "Delete the AWS key or restore the .pem file."
        )


def ensure_security_group(cfg: Config) -> str:
    """Return the security group ID, creating it (SSH/22) if needed."""
    ec2 = auth.client(cfg, "ec2")
    try:
        sgs = ec2.describe_security_groups(GroupNames=[cfg.compute.security_group])
        sg_id = sgs["SecurityGroups"][0]["GroupId"]
        ui.ok(f"Security group '{cfg.compute.security_group}' ({sg_id}) found")
        return sg_id
    except ClientError:
        ui.log(f"Creating security group '{cfg.compute.security_group}' …")
        vpc_id = ec2.describe_subnets(SubnetIds=[cfg.compute.subnet_id])["Subnets"][0]["VpcId"]
        sg = ec2.create_security_group(
            GroupName=cfg.compute.security_group,
            Description="SSH for androbuilder",
            VpcId=vpc_id,
        )
        sg_id = sg["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }
            ],
        )
        ui.ok(f"Security group {sg_id} created")
        return sg_id


def ensure_iam_and_bucket(cfg: Config) -> None:
    """Ensure the IAM role, instance profile, S3 permissions, and bucket exist.

    Accumulates the current bucket into the role's inline policy so each project's
    bucket is authorized without removing previously-added buckets.
    """
    role = cfg.compute.instance_profile
    bucket = cfg.storage.s3_bucket
    if not bucket:
        ui.fail("storage.s3_bucket is not set (run 'androbuilder init')")

    iam = auth.client(cfg, "iam")
    s3 = auth.client(cfg, "s3")

    # Role
    try:
        iam.get_role(RoleName=role)
        ui.ok(f"IAM role '{role}' found")
    except ClientError:
        ui.log(f"Creating IAM role '{role}' …")
        iam.create_role(
            RoleName=role,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )
        ui.ok(f"IAM role '{role}' created")

    # Instance profile + role attachment
    try:
        iam.get_instance_profile(InstanceProfileName=role)
    except ClientError:
        ui.log(f"Creating instance profile '{role}' …")
        iam.create_instance_profile(InstanceProfileName=role)
        time.sleep(2)
        iam.add_role_to_instance_profile(InstanceProfileName=role, RoleName=role)
        time.sleep(8)  # IAM propagation
        ui.ok(f"Instance profile '{role}' created")

    # Accumulate current bucket into the inline policy
    bucket_arn = f"arn:aws:s3:::{bucket}"
    objects_arn = f"{bucket_arn}/*"
    existing: list[str] = []
    try:
        doc = iam.get_role_policy(RoleName=role, PolicyName="s3-upload")["PolicyDocument"]
        for st in doc.get("Statement", []):
            actions = st.get("Action", [])
            actions = actions if isinstance(actions, list) else [actions]
            if "s3:PutObject" in actions:
                res = st.get("Resource", [])
                existing = res if isinstance(res, list) else [res]
    except ClientError:
        pass

    if objects_arn not in existing:
        existing.append(objects_arn)
    if bucket_arn not in existing:
        existing.append(bucket_arn)

    iam.put_role_policy(
        RoleName=role,
        PolicyName="s3-upload",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "ReadWriteObjects",
                        "Effect": "Allow",
                        "Action": ["s3:PutObject", "s3:GetObject"],
                        "Resource": existing,
                    },
                    {
                        "Sid": "ListBucket",
                        "Effect": "Allow",
                        "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                        "Resource": [r for r in existing if not r.endswith("/*")],
                    },
                ],
            }
        ),
    )
    ui.ok(f"S3 permissions updated for '{bucket}'")

    # Bucket
    try:
        s3.head_bucket(Bucket=bucket)
        ui.ok(f"S3 bucket '{bucket}' found")
    except ClientError:
        ui.log(f"Creating S3 bucket '{bucket}' …")
        kwargs: dict = {"Bucket": bucket}
        if cfg.aws.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": cfg.aws.region}
        s3.create_bucket(**kwargs)
        ui.ok(f"S3 bucket '{bucket}' created")
