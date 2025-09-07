#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PKG_DIR="$ROOT_DIR/docs/yc-pack"
DATE_TAG="$(date +%Y%m%d)"
OUT="yc-pack-$DATE_TAG.tar.gz"

cd "$PKG_DIR"

# copy the latest YcCustomBuild.md into this folder for standalone keeping
if [ -f "$ROOT_DIR/docs/YcCustomBuild.md" ]; then
  cp -f "$ROOT_DIR/docs/YcCustomBuild.md" "$PKG_DIR/YcCustomBuild.md"
fi

# ensure executable
chmod +x RG_CHECKS.sh || true

# create archive from within docs to keep relative paths small
cd "$ROOT_DIR/docs"

tar -czf "$OUT" yc-pack/

echo "Packed -> $ROOT_DIR/docs/$OUT"
