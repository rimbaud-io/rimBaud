#!/usr/bin/env bash
# Copyright 2026 rimbaud.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Meta desk: write Mint ISO to USB. pkexec / sudo password dialog.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/write-usb.sh"
ASK="$(cd "$HERE/.." && pwd)/askpass-zenity.sh"
TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "usage: $0 /dev/sdX|/media/user/Ventoy"; exit 2; }
[ -x "$SCRIPT" ] || { echo "missing $SCRIPT"; exit 1; }

MODE=--dd
if [ -d "$TARGET" ]; then
  MODE=--copy
  if [ -w "$TARGET" ]; then
    echo "==> writable dir $TARGET — copy ISO (Ventoy)"
    exec "$SCRIPT" --copy "$TARGET"
  fi
fi

run() {
  if [ "$(id -u)" -eq 0 ]; then
    exec "$SCRIPT" "$MODE" "$TARGET"
  fi
  if sudo -n true 2>/dev/null; then
    exec sudo -n "$SCRIPT" "$MODE" "$TARGET"
  fi
  if command -v pkexec >/dev/null 2>&1; then
    echo "==> pkexec password dialog"
    exec pkexec env \
      DISPLAY="${DISPLAY:-}" \
      XAUTHORITY="${XAUTHORITY:-}" \
      DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
      XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
      PATH="$PATH" \
      "$SCRIPT" "$MODE" "$TARGET"
  fi
  if command -v zenity >/dev/null 2>&1 && [ -x "$ASK" ]; then
    export SUDO_ASKPASS="$ASK"
    export DISPLAY="${DISPLAY:-:0}"
    exec sudo -A "$SCRIPT" "$MODE" "$TARGET"
  fi
  echo "xx need sudo: sudo $SCRIPT $MODE $TARGET"
  exit 1
}
run
