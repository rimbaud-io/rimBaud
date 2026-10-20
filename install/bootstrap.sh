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

# Single entry: unpack tarball to /opt/rimbaud, then run the installer.
# Usage (USB or folder with the .tar.gz):
#   sudo bash install.sh
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  exec sudo -E "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive
export RIMBAUD_KIT_DIR="$HERE"
export RIMBAUD_SKIP_TUI="${RIMBAUD_SKIP_TUI:-1}"
export RIMBAUD_BOOTSTRAP="${RIMBAUD_BOOTSTRAP:-1}"

KIT=""
if ls -1 "$HERE"/rimbaud-install-*.tar.gz >/dev/null 2>&1; then
  KIT=$(ls -1 "$HERE"/rimbaud-install-*.tar.gz | tail -n 1)
fi

if [ -n "$KIT" ]; then
  echo "==> unpack $KIT → /opt/rimbaud"
  mkdir -p /opt
  tar -C /opt -xzf "$KIT"
fi

ROOT="/opt/rimbaud"
if [ ! -x "$ROOT/install_new/install.sh" ]; then
  # already extracted tree (this file lives in the kit)
  if [ -x "$HERE/install_new/install.sh" ]; then
    ROOT="$(cd "$HERE" && pwd)"
  fi
fi
[ -x "$ROOT/install_new/install.sh" ] || {
  echo "xx missing installer. Put rimbaud-install-*.tar.gz next to install.sh"
  exit 1
}

if [ -f "$HERE/JOIN.conf" ]; then
  mkdir -p "$ROOT/conf/vpn"
  cp -a "$HERE/JOIN.conf" "$ROOT/JOIN.conf"
  cp -a "$HERE/JOIN.conf" "$ROOT/conf/vpn/JOIN.conf"
  echo "==> VPN join file staged"
fi

cd "$ROOT"
exec "$ROOT/install_new/install.sh" all
