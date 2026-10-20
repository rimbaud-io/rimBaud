#!/usr/bin/env bash
# Copyright 2026 Laurence Sterne
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

# Meta desk: build the Mint appliance ISO. pkexec / sudo password dialog.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/build-iso.sh"
ASK="$(cd "$HERE/.." && pwd)/askpass-zenity.sh"
[ -x "$SCRIPT" ] || { echo "FAIL missing $SCRIPT"; exit 1; }

printf 'CMD  hybrid ISO  %s\n' "$SCRIPT"

if [ "$(id -u)" -eq 0 ]; then
  echo "OK   already root"
  exec "$SCRIPT" "$@"
fi
if sudo -n true 2>/dev/null; then
  echo "OK   passwordless sudo"
  exec sudo -n "$SCRIPT" "$@"
fi
if command -v pkexec >/dev/null 2>&1; then
  printf 'CMD  pkexec password dialog (ISO build, several minutes)\n'
  exec pkexec env \
    DISPLAY="${DISPLAY:-}" \
    XAUTHORITY="${XAUTHORITY:-}" \
    DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
    PATH="$PATH" \
    "$SCRIPT" "$@"
fi
if command -v zenity >/dev/null 2>&1 && [ -x "$ASK" ]; then
  printf 'CMD  zenity sudo password\n'
  export SUDO_ASKPASS="$ASK"
  export DISPLAY="${DISPLAY:-:0}"
  exec sudo -A "$SCRIPT" "$@"
fi
echo "FAIL need sudo: sudo $SCRIPT"
exit 1
