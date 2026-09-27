"""`bake-ami` command wrapper."""
from __future__ import annotations

import time

from .. import bake
from ..aws import auth
from ..state import State


def run_bake(
    state: State,
    *,
    name: str | None = None,
    source_ami: str | None = None,
    instance_type: str | None = None,
    write_config: bool = False,
) -> None:
    from ..state import load

    cfg = load(state)
    auth.ensure_aws_auth(cfg)
    ami_name = name or f"androbuilder-{time.strftime('%Y%m%d-%H%M%S')}"
    bake.bake_ami(
        cfg,
        name=ami_name,
        source_ami=source_ami,
        instance_type=instance_type,
        write_config=write_config,
    )
