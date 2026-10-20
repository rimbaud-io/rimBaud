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

# First boot on a rimBaud Mint appliance. Does not CLOSE/OPEN HL.
set -euo pipefail
LOG=/var/log/rimbaud-firstboot.log
exec >>"$LOG" 2>&1
echo "==> $(date -u +'%Y-%m-%d %H:%M:%S UTC') rimBaud first-boot"

# Live ISO: do not install onto the USB. Still join the mothership so meta
# can see this machine. The installed disk enrolls again on its own first boot.
live_session() {
  grep -Eqw 'boot=casper|maybe-ubiquity' /proc/cmdline 2>/dev/null && return 0
  [ -d /cdrom/casper ] || [ -d /lib/live/mount ]
}
enroll_when_lan() {
  local i
  if [ ! -x /opt/rimbaud/rimbaud_vpn/rimbaud-vpn ]; then
    echo "no rimbaud-vpn — cannot join mothership"
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
    if ip -4 -o addr show scope global 2>/dev/null | grep -q 'inet '; then
      break
    fi
    sleep 2
  done
  /opt/rimbaud/rimbaud_vpn/rimbaud-vpn enroll || echo "enroll failed — hub not answering yet"
}
if live_session; then
  echo "live session — join mothership, do not install onto the USB"
  enroll_when_lan || true
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
# SSH before the long install. Meta still cannot GET Spring until enroll runs.
systemctl enable --now ssh || true
apt-get update -y || true
if ! dpkg -s rimbaud >/dev/null 2>&1; then
  apt-get install -y rimbaud || true
fi

rm -f /etc/rimbaud/in-iso /etc/rimbaud/firstboot-pending
if [ -x /opt/rimbaud/install_new/install.sh ]; then
  RIMBAUD_BOOTSTRAP=1 /opt/rimbaud/install_new/install.sh || true
fi
if [ -x /opt/rimbaud/rimbaud_vpn/rimbaud-vpn ]; then
  /opt/rimbaud/rimbaud_vpn/rimbaud-vpn enroll || echo "enroll failed — hub not answering yet"
fi

if id forklifter >/dev/null 2>&1; then
  chage -d 0 forklifter || true
  usermod -aG docker,sudo forklifter 2>/dev/null || true
fi

systemctl enable --now ssh || true
systemctl disable rimbaud-firstboot.service || true
echo "==> first-boot done — run /opt/rimbaud/install_new/setup.sh for unique wallets"
