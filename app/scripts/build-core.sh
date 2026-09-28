#!/usr/bin/env bash
# Build the Python core sidecar and place it where Tauri's externalBin expects it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
triple="$(rustc -vV | sed -n 's/^host: //p')"
cd "$repo"
uv run --extra desktop-build pyinstaller --noconfirm --clean \
  --distpath build/core-dist --workpath build/core-work packaging/hyverion_core.spec
mkdir -p "$repo/app/src-tauri/binaries"
cp "build/core-dist/hyverion-core" "$repo/app/src-tauri/binaries/hyverion-core-$triple"
echo "sidecar: app/src-tauri/binaries/hyverion-core-$triple"
