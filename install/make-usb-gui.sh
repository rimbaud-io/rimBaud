#!/usr/bin/env bash
# Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
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

# Meta desk DEPLOY helper.
# Always root. A writable mount used to skip sudo, and then the stick kept an
# old JOIN.conf instead of a new peer the mothership already knows.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/make-usb.sh"
ASK="$HERE/askpass-zenity.sh"
TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "FAIL usage: $0 /dev/sdX|/media/usb/stick"; exit 2; }
[ -x "$SCRIPT" ] || { echo "FAIL missing $SCRIPT"; exit 1; }

printf 'CMD  USB kit  %s\n' "$TARGET"

run_pkexec() {
  printf 'CMD  pkexec password dialog\n'
  exec pkexec env \
    DISPLAY="${DISPLAY:-}" \
    XAUTHORITY="${XAUTHORITY:-}" \
    DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
    PATH="$PATH" \
    USB_STDBUF="${USB_STDBUF:-}" \
    "$SCRIPT" --write "$TARGET"
}

if sudo -n true 2>/dev/null; then
  printf 'OK   passwordless sudo\n'
  exec sudo -n "$SCRIPT" --write "$TARGET"
fi

if command -v pkexec >/dev/null 2>&1; then
  run_pkexec
fi

if command -v zenity >/dev/null 2>&1 && [ -x "$ASK" ]; then
  printf 'CMD  zenity sudo password\n'
  export SUDO_ASKPASS="$ASK"
  export DISPLAY="${DISPLAY:-:0}"
  exec sudo -A "$SCRIPT" --write "$TARGET"
fi

echo "FAIL need sudo for $TARGET"
echo "     sudo $SCRIPT --write $TARGET"
exit 1
