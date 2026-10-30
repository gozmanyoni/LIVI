#!/bin/bash
set -euo pipefail
LIVI_BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVI_SOURCE_DIR="$(cd "$LIVI_BUILD_DIR/../../.." && pwd)"
LIVI_MFI_DIR="${LIVI_MFI_DIR:-}"
if [[ -z "$LIVI_MFI_DIR" ]]; then
  if [[ "$(uname -s)" == Darwin ]]; then
    LIVI_MFI_DIR="$HOME/Library/Application Support/LIVI/mfi"
  else
    LIVI_MFI_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/LIVI/mfi"
  fi
fi
for name in certificate.p7b identity.pk8; do
  if [[ ! -s "$LIVI_MFI_DIR/$name" ]]; then
    printf 'Missing %s; point LIVI_MFI_DIR at the approved identity folder.\n' "$name" >&2
    exit 1
  fi
done
if ! git -C "$LIVI_SOURCE_DIR" merge-base --is-ancestor e7b20b818f052958afc95dcf91acc25c70643c02 HEAD; then
  printf 'This builder requires LIVI baseline e7b20b81 in its history. Fetch full history or review template compatibility.\n' >&2
  exit 1
fi
python3 "$LIVI_BUILD_DIR/prepare.py"
docker build --platform linux/arm64 -t livi-pi5-image-builder:local "$LIVI_BUILD_DIR"
docker run --rm --name livi-pi5-image-build --privileged --platform linux/arm64 \
  --mount "type=bind,source=$LIVI_SOURCE_DIR/.cache/pi5-fast,target=/inputs" \
  --mount "type=bind,source=$LIVI_SOURCE_DIR/dist/pi5-fastboot,target=/output" \
  --mount "type=bind,source=$LIVI_BUILD_DIR,target=/builder,readonly" \
  --mount "type=bind,source=$LIVI_SOURCE_DIR,target=/source,readonly" \
  --mount "type=bind,source=$LIVI_MFI_DIR,target=/auth,readonly" \
  livi-pi5-image-builder:local bash /builder/build.sh
