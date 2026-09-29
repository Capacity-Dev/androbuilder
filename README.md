# androbuilder

Build and deploy **Expo / React Native Android** apps on **ephemeral EC2**, with a
**persistent EBS cache** so repeat builds are fast — without keeping a machine running.

```
androbuilder release apk        # build a release APK to ./app-release.apk
androbuilder release aab        # build an AAB
androbuilder release deploy     # build an AAB and upload it to Google Play
androbuilder debug apk          # debug build
```

Each run: provisions a spot (or on-demand) builder, syncs your source, runs
`yarn install` + `expo prebuild` + `gradlew`, uploads the artifact to S3, downloads
it locally, and **terminates the instance**. Gradle caches and the yarn offline
cache live on a small EBS volume that is reattached to the next builder — so
nothing is lost when the instance dies.

## Install

Requires Python ≥ 3.11 and the AWS CLI (configured, e.g. via `aws login`).

```bash
pipx install androbuilder-cli   # or: uv tool install androbuilder-cli
# from a clone:
pipx install .
```

Runtime deps: `boto3`, `botocore`, `fabric`, `typer`, `rich`, `tqdm`, `tomli-w`.

## Quick start

From the root of your Expo project:

```bash
androbuilder init          # detect package name, prompt for AWS + keystore, write config
androbuilder doctor        # verify config; --provision creates key/SG/IAM/bucket
androbuilder release apk   # build
```

## Configuration

Two levels, merged first-wins:

1. **Global** — `~/.config/androbuilder/config.toml` (shared across projects):
   AWS region/profile, AMI, subnet, SSH key, security group, IAM instance profile,
   instance types.
2. **Project** — `.androbuilder.toml` in the project root (**gitignored**):
   repo/package name, S3 bucket, cache volume, signing keystore + passwords.

Precedence: **CLI flags → `ANDROBUBILDER_*` env vars → project config → global config → defaults.**

`androbuilder init` imports an existing legacy `scripts/.env` if present.

### Secrets

By default signing passwords live in the gitignored `.androbuilder.toml`. For CI,
prefer env indirection:

```toml
[signing]
keystore = "release.keystore"
alias = "my-alias"
store_password_env = "MYAPP_STORE_PASSWORD"
key_password_env = "MYAPP_KEY_PASSWORD"
```

AWS credentials are never stored by androbuilder — boto3 uses the standard chain;
`androbuilder login` just wraps `aws login`.

### App env (`.env`)

The selected env file is uploaded to the instance **as `.env`**:

- **release** builds prefer `.env.production` (fallback `.env`);
- **debug** builds use `.env`;
- override with `build.env_file` in `.androbuilder.toml`, or
  `androbuilder --env-file <path> release`.

`EXPO_PUBLIC_*` vars from the selected file are also forwarded to the remote
build environment.

## The builder AMI

androbuilder needs an AMI with JDK 17, the Android SDK, Node, Yarn, the AWS CLI,
and fastlane. Build one:

```bash
androbuilder bake-ami -w      # -w writes the resulting ami_id to the global config
```

It launches a temporary instance, installs the toolchain, registers an AMI, and
terminates the instance. `androbuilder release` also auto-installs any extra
Android SDK platform/build-tools the generated project requires.

## Commands

| Command | Description |
|---|---|
| `androbuilder init` | Scaffold `.androbuilder.toml` (+ global config) |
| `androbuilder doctor [--provision]` | Verify config/AWS; optionally create resources |
| `androbuilder login` | Refresh AWS credentials |
| `androbuilder release [apk\|aab\|deploy]` | Build a release artifact |
| `androbuilder debug [apk\|aab]` | Build a debug artifact |
| `androbuilder deploy` | Alias for `release deploy` |
| `androbuilder bake-ami` | Build the toolchain AMI |
| `androbuilder config show\|get\|set` | Inspect/edit config |
| `androbuilder cache [info\|delete]` | Manage the EBS cache volume |
| `androbuilder instances [list\|terminate-all]` | Housekeeping |

Global flags: `-C/--project`, `--config`, `-v/--verbose`, `--no-progress`,
`--no-cache`, `--download-from auto|s3|sftp`, `--profile`, `--region`, `--dry-run`.

## How artifact retrieval works

- **S3 direct (default when locally readable):** the instance is terminated first,
  then the artifact is downloaded straight from S3 — billing stops during download.
- **SFTP:** the file is pulled over `rsync` → `ssh cat` → paramiko SFTP (in that
  order), then the instance is terminated.

Force a mode with `--download-from s3|sftp`.

## Security notes

- Keystores and `play-store-key.json` are gitignored; never commit them.
- The builder security group only needs SSH (22) from your IP — tighten the
  `0.0.0.0/0` default created by `init` if you can.
- Once published to Play, your signing keystore cannot change. Back it up.

## Distribution

Published on **PyPI** (install via `pipx`/`uv`). Packaging assets for **Homebrew**,
**winget**, and **apt** live under `packaging/` — see [`docs/PUBLISHING.md`](docs/PUBLISHING.md).

## License

MIT
