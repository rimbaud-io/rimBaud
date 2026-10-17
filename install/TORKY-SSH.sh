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

# Run ON torky as forklifter. Installs gerdy's live-push SSH pubkey.
# Does not restart hunters. Does not CLOSE/OPEN Hyperliquid.
set -euo pipefail
HOST="$(hostname -s 2>/dev/null || hostname)"
[ "$HOST" = "torky" ] || { echo "xx run on torky (this is $HOST)"; exit 1; }
[ "$(id -un)" = "forklifter" ] || echo "!! expected user forklifter, got $(id -un)"

PUB=""
for p in \
  "$HOME/desk-share/rimbaud-deploy.pub" \
  /tmp/rimbaud-deploy.pub \
  "$HOME/hummingbird/hummingbot/conf/vpn/rimbaud-deploy.pub"
do
  [ -f "$p" ] && PUB="$p" && break
done
if [ -z "$PUB" ]; then
  echo "xx no rimbaud-deploy.pub"
  echo "   mount gerdy share:  ~/desk-share/rimbaud-deploy.pub"
  echo "   or copy the one line from gerdy ~/.ssh/id_ed25519_rimbaud_deploy.pub into /tmp/"
  exit 1
fi

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
KEY="$(tr -d '\r' < "$PUB" | awk 'NF{print; exit}')"
[ -n "$KEY" ] || { echo "xx empty pubkey in $PUB"; exit 1; }
if grep -Fqx "$KEY" "$HOME/.ssh/authorized_keys"; then
  echo "==> key already in ~/.ssh/authorized_keys"
else
  echo "$KEY" >> "$HOME/.ssh/authorized_keys"
  echo "==> appended $PUB"
fi
chmod 600 "$HOME/.ssh/authorized_keys"

echo "==> sshd listeners:"
ss -ltn 2>/dev/null | awk '/:22|:2222/ {print}' || netstat -ltn 2>/dev/null | awk '/:22/ {print}' || true
echo "==> this box:"
hostname
ip -4 addr show | awk '/inet /{print "  "$2"  "$NF}'
echo
echo "back on gerdy Meta: PUSH latest"
echo "LAN ssh should work: forklifter@192.168.1.123"
echo "VPN 10.66.80.2:22 is often closed — live-push will use LAN if VPN times out."
echo "positions untouched."
