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

# Build native rimBaud desk / chart / meta. Does not start an X11 session.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

need_cmd make
need_cmd g++

log "Building rimbaud-desk + rimbaud-chart"
make -C "$ROOT/freiquell_c++" -j"$(nproc 2>/dev/null || echo 2)" all

log "Building rimbaud-meta"
make -C "$ROOT/rimbaud_meta_c++" -j"$(nproc 2>/dev/null || echo 2)" all

BIN_DIR="${REAL_HOME}/.local/bin"
link_bins() {
  mkdir -p "$BIN_DIR"
  ln -sfn "$ROOT/freiquell_c++/rimbaud-desk" "$BIN_DIR/rimbaud-desk"
  ln -sfn "$ROOT/freiquell_c++/rimbaud-chart" "$BIN_DIR/rimbaud-chart"
  ln -sfn "$ROOT/rimbaud_meta_c++/rimbaud-meta" "$BIN_DIR/rimbaud-meta"
  ln -sfn "$ROOT/rimbaud_meta_c++/rimbaud-meta-desk" "$BIN_DIR/rimbaud-meta-desk"
  ln -sfn "$ROOT/rimbaud_vpn/rimbaud-vpn" "$BIN_DIR/rimbaud-vpn"
}
if [ "$(id -u)" -eq 0 ] && [ "$REAL_USER" != root ] && [ -n "$REAL_HOME" ]; then
  su -s /bin/bash "$REAL_USER" -c "mkdir -p '$BIN_DIR'
    ln -sfn '$ROOT/freiquell_c++/rimbaud-desk' '$BIN_DIR/rimbaud-desk'
    ln -sfn '$ROOT/freiquell_c++/rimbaud-chart' '$BIN_DIR/rimbaud-chart'
    ln -sfn '$ROOT/rimbaud_meta_c++/rimbaud-meta' '$BIN_DIR/rimbaud-meta'
    ln -sfn '$ROOT/rimbaud_meta_c++/rimbaud-meta-desk' '$BIN_DIR/rimbaud-meta-desk'
    ln -sfn '$ROOT/rimbaud_vpn/rimbaud-vpn' '$BIN_DIR/rimbaud-vpn'"
else
  link_bins
fi

if [ -x "$ROOT/freiquell_server_c++/springctl" ]; then
  if [ "$(id -u)" -eq 0 ] && [ "$REAL_USER" != root ] && [ -n "$REAL_HOME" ]; then
    su -s /bin/bash "$REAL_USER" -c "mkdir -p '$BIN_DIR'; ln -sfn '$ROOT/freiquell_server_c++/springctl' '$BIN_DIR/springctl'"
  else
    mkdir -p "$BIN_DIR"
    ln -sfn "$ROOT/freiquell_server_c++/springctl" "$BIN_DIR/springctl"
  fi
fi

log "Binaries"
ls -l "$ROOT/freiquell_c++/rimbaud-desk" "$ROOT/freiquell_c++/rimbaud-chart" \
  "$ROOT/rimbaud_meta_c++/rimbaud-meta" "$ROOT/rimbaud_meta_c++/rimbaud-meta-desk" \
  "$ROOT/rimbaud_vpn/rimbaud-vpn" "$ROOT/freiquell_server_c++/springctl" 2>/dev/null || true
echo
echo "On a graphical session:"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
echo "  rimbaud-desk          # X11 matrix → Spring :5004"
echo "  rimbaud-meta          # read-only console (LAN + VPN sites)"
echo "  rimbaud-meta-desk     # meta X11 — HOME + DEPLOY (LIVE PUSH / USB kit / Mint ISO)"
echo "  springctl             # Spring panel — tick VPN, gold = new HL IP"
echo "  sudo rimbaud-vpn init|add|join|status"
