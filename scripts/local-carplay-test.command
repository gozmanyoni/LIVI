#!/bin/zsh
set -eu
LIVI_TEST_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVI_TEST_APP="$LIVI_TEST_ROOT/dist/local-test/LIVI.app"
if [[ ! -d "$LIVI_TEST_APP" ]]; then
  print -u2 "Local LIVI test app is missing: $LIVI_TEST_APP"
  exit 1
fi
exec env LIVI_CP_DEBUG=1 LIVI_UI_SIZE=1280x720 "$LIVI_TEST_APP/Contents/MacOS/LIVI"
