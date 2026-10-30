#!/bin/zsh
set -eu
LIVI_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$LIVI_SCRIPT_DIR/install/pi5-fast/collect-pi-logs.py" "$@"
