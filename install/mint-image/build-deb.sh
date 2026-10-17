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

# Build the internal rimbaud .deb from this tree. No wallets, no keys, no clips.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../_common.sh
. "$HERE/../_common.sh"

need_cmd python3
need_cmd tar
need_cmd rsync
command -v dpkg-deb >/dev/null 2>&1 || die "need dpkg-deb (apt install dpkg-dev)"

CFG="$HERE/config.json"
PKG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("package") or "rimbaud")' "$CFG")"
VER="$(python3 "$ROOT/rimbaud-archy/archy_db.py" --status 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("version") or "0.1").replace("g","."))' 2>/dev/null || echo 0.1.0)"
VER="${VER:-0.1.0}"
STAGE="${TMPDIR:-/tmp}/rimbaud-deb-$$"
DEST="$STAGE/$PKG"
trap 'rm -rf "$STAGE"' EXIT

# Logs on stderr so callers can capture only the .deb path from stdout.
log() { printf '\033[0;32m==>\033[0m %s\n' "$*" >&2; }

log "Packing $PKG $VER (no secrets)"
mkdir -p "$DEST/DEBIAN" "$DEST/opt" "$DEST/usr/share/applications" \
  "$DEST/etc/xdg/autostart" "$DEST/usr/local/sbin" "$DEST/usr/local/bin" \
  "$DEST/etc/systemd/system" "$DEST/var/lib/rimbaud/apt"

"$HERE/../pack.sh" "$STAGE/kit.tar.gz"
tar -C "$DEST/opt" -xzf "$STAGE/kit.tar.gz"
# pack.sh tarball root is "rimbaud/"
if [ ! -d "$DEST/opt/rimbaud" ]; then
  if [ -d "$DEST/opt/rimbaud/rimbaud" ]; then
    mv "$DEST/opt/rimbaud/rimbaud" "$DEST/opt/rimbaud-tree"
    rm -rf "$DEST/opt/rimbaud"
    mv "$DEST/opt/rimbaud-tree" "$DEST/opt/rimbaud"
  fi
fi
[ -d "$DEST/opt/rimbaud" ] || die "kit did not unpack to /opt/rimbaud"

cp -a "$HERE/overlay/usr/share/applications/." "$DEST/usr/share/applications/"
cp -a "$HERE/overlay/etc/xdg/autostart/." "$DEST/etc/xdg/autostart/"
install -m 0755 "$HERE/overlay/usr/local/bin/rimbaud-meta-desk" "$DEST/usr/local/bin/rimbaud-meta-desk"
install -m 0755 "$HERE/overlay/usr/local/sbin/rimbaud-look.sh" "$DEST/usr/local/sbin/rimbaud-look.sh"
install -m 0755 "$HERE/firstboot/rimbaud-firstboot.sh" "$DEST/usr/local/sbin/rimbaud-firstboot.sh"
install -m 0644 "$HERE/firstboot/rimbaud-firstboot.service" "$DEST/etc/systemd/system/rimbaud-firstboot.service"

SIZE_KB="$(du -sk "$DEST" | awk '{print $1}')"
cat >"$DEST/DEBIAN/control" <<EOF
Package: $PKG
Version: $VER
Section: misc
Priority: optional
Architecture: amd64
Maintainer: rimBaud internal <noreply@local>
Installed-Size: $SIZE_KB
Depends: python3, rsync, curl, ca-certificates, wireguard, wireguard-tools, make, g++, libx11-6
Recommends: docker.io | docker-ce, openssh-server
Description: rimBaud matrix (internal appliance)
 C++ Spring, hunters, Meta desk. Internal use only — not a Linux Mint fork.
 Does not ship wallets or Hyperliquid keys.
EOF

cat >"$DEST/DEBIAN/postinst" <<'EOF'
#!/bin/bash
set -e
if [ -f /etc/rimbaud/in-iso ]; then
  exit 0
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl enable rimbaud-firstboot.service || true
fi
if [ -x /opt/rimbaud/install_new/desk.sh ]; then
  /opt/rimbaud/install_new/desk.sh || true
fi
exit 0
EOF
chmod 0755 "$DEST/DEBIAN/postinst"

OUT="${1:-$ROOT/dist/mint-image/${PKG}_${VER}_amd64.deb}"
mkdir -p "$(dirname "$OUT")"
dpkg-deb --build --root-owner-group "$DEST" "$OUT"
log "deb $OUT"
echo "$OUT"
