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

# rimBaud install — one command after the tarball is next to install.sh:
#
#   sudo bash install.sh
#
# Unpacks to /opt/rimbaud, installs Docker/hunters/Spring, joins VPN if JOIN.conf
# is present, then a checkbox screen (SPACE = OK, ENTER = done).
# Never CLOSE/OPEN clips. Unique wallets are a checkbox (s).
#
# Fresh Mint from a project archive is the other command (keeps wallets):
#   bash ../install.sh --check
#   bash install.sh restore
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=install_new/_common.sh
. "$INSTALL_DIR/_common.sh"

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \?//'
}

need_linux() {
  [ "$(uname -s)" = Linux ] || die "install is for Linux (got $(uname -s))"
}

maybe_reexec_sudo() {
  case "${1:-}" in
    pack|status|help|-h|--help|desk|restore) return 0 ;;
  esac
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    if ! docker_ok 2>/dev/null; then
      log "re-running with sudo (Docker / systemd)"
      exec sudo -E "$INSTALL_DIR/install.sh" "$@"
    fi
  fi
}

join_vpn_if_present() {
  local f=""
  for c in \
    "${RIMBAUD_KIT_DIR:-}/JOIN.conf" \
    "$ROOT/JOIN.conf" \
    "$ROOT/conf/vpn/JOIN.conf"
  do
    [ -f "$c" ] && { f="$c"; break; }
  done
  [ -n "$f" ] || return 0
  log "VPN join $f (control plane only — hunters keep this box public IP)"
  if "$INSTALL_DIR/vpn.sh" join "$f"; then
    mkdir -p "$ROOT/conf/vpn"
    printf '%s\n' '{"enabled":true,"closes":false}' > "$ROOT/conf/vpn/use_vpn.json"
  else
    warn "VPN join failed — sudo ./install_new/vpn.sh join $f"
  fi
}

full_install() {
  need_linux
  "$INSTALL_DIR/deps.sh"
  "$INSTALL_DIR/dirs.sh"
  if [ -f "$ENV_FILE" ] && grep -q '^CONFIG_PASSWORD=change-me' "$ENV_FILE"; then
    warn "CONFIG_PASSWORD is still change-me — change it in setup (checkbox)"
  fi
  "$INSTALL_DIR/up.sh"
  "$INSTALL_DIR/spring.sh" || warn "Spring install failed — ./install_new/spring.sh later"
  "$INSTALL_DIR/desk.sh" || warn "desk build failed — ./install_new/desk.sh later"
  if [ "$(id -u)" -eq 0 ] || command -v sudo >/dev/null 2>&1; then
    "$INSTALL_DIR/autostart.sh" --system
  else
    "$INSTALL_DIR/autostart.sh"
  fi
  join_vpn_if_present
  if [ -x "$ROOT/rimbaud_vpn/rimbaud-vpn" ]; then
    "$ROOT/rimbaud_vpn/rimbaud-vpn" enroll || warn "VPN enroll failed — hub listener down, or run: sudo $ROOT/rimbaud_vpn/rimbaud-vpn enroll"
  fi
  "$INSTALL_DIR/rustdesk.sh" || warn "RustDesk setup failed — sudo $INSTALL_DIR/rustdesk.sh"
  "$INSTALL_DIR/status.sh" || true
  echo
  log "core install done — checkbox screen next (SPACE = OK, ENTER = finish, s = wallets)"
  if [ -t 0 ] && [ -t 1 ]; then
    python3 "$INSTALL_DIR/checklist.py" || warn "checklist skipped"
  else
    python3 "$INSTALL_DIR/checklist.py" || true
  fi
}

cmd="${1:-all}"
shift || true
maybe_reexec_sudo "$cmd" "$@"

case "$cmd" in
  -h|--help|help) usage; exit 0 ;;
  all|install) full_install ;;
  deps)      "$INSTALL_DIR/deps.sh" ;;
  dirs)      "$INSTALL_DIR/dirs.sh" ;;
  verify)    "$INSTALL_DIR/verify.sh" ;;
  up)        "$INSTALL_DIR/up.sh" ;;
  down)      "$INSTALL_DIR/down.sh" ;;
  status)    "$INSTALL_DIR/status.sh" ;;
  spring)    "$INSTALL_DIR/spring.sh" ;;
  desk)      "$INSTALL_DIR/desk.sh" ;;
  autostart) "$INSTALL_DIR/autostart.sh" "${1:---system}" ;;
  setup)     "$INSTALL_DIR/setup.sh" ;;
  vpn)       "$INSTALL_DIR/vpn.sh" "$@" ;;
  rustdesk)  "$INSTALL_DIR/rustdesk.sh" "$@" ;;
  usb)       "$INSTALL_DIR/make-usb.sh" "$@" ;;
  pack)      "$INSTALL_DIR/pack.sh" "$@" ;;
  restore)   exec "$ROOT/install.sh" "$@" ;;
  *)
    usage
    die "unknown command: $cmd"
    ;;
esac
