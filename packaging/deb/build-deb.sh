#!/usr/bin/env bash
# Build a .deb that installs androbuilder into an isolated venv at
# /opt/androbuilder and exposes /usr/bin/androbuilder.
#
# Requires: python3-venv and `fpm`  (gem install fpm)
#
#   ./packaging/deb/build-deb.sh 0.1.0
#
# Then serve it from a signed apt repository (aptly / reprepro / S3 + GPG),
# or attach it to a GitHub Release.
set -euo pipefail

VERSION="${1:-0.1.0}"
ARCH="${2:-amd64}"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo ">> Staging venv at /opt/androbuilder"
mkdir -p "$BUILD/opt/androbuilder" "$BUILD/usr/bin"
python3 -m venv "$BUILD/opt/androbuilder"
"$BUILD/opt/androbuilder/bin/pip" install --no-cache-dir --upgrade pip
"$BUILD/opt/androbuilder/bin/pip" install --no-cache-dir "androbuilder-cli==${VERSION}"
ln -s /opt/androbuilder/bin/androbuilder "$BUILD/usr/bin/androbuilder"

echo ">> Building .deb with fpm"
fpm -s dir -t deb \
  -n androbuilder -v "$VERSION" -a "$ARCH" \
  --license MIT \
  --url "https://github.com/Capacity-Dev/androbuilder" \
  --description "Build and deploy Expo/React Native Android apps on ephemeral EC2" \
  --depends python3 \
  --deb-no-default-config-files \
  -C "$BUILD" opt usr \
  -p "androbuilder_${VERSION}_${ARCH}.deb"

echo ">> Done: androbuilder_${VERSION}_${ARCH}.deb"
