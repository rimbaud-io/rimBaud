#!/usr/bin/env bash
# Copyright 2026 Laurence Sterne www.rimbaud.io
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

# Build Spring and install the systemd unit on :5004. Never binds :5001.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

need_cmd make
need_cmd g++
[ -f "$ROOT/freiquell_server_c++/Makefile" ] || die "missing freiquell_server_c++"

log "Building Spring + springctl"
make -C "$ROOT/freiquell_server_c++" -j"$(nproc 2>/dev/null || echo 2)" all

BIN="$ROOT/freiquell_server_c++/spring"
[ -x "$BIN" ] || die "spring binary missing"
[ -x "$ROOT/freiquell_server_c++/springctl" ] || warn "springctl missing (VPN checkbox lives there)"

PORT=5004
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set +u
  . "$ENV_FILE"
  set -u
  PORT="${SPRING_PORT:-5004}"
fi
[ "$PORT" != "5001" ] || die "SPRING_PORT cannot be 5001"

export FREIQUELL_CONF="$ROOT/conf"
export SPRING_PORT="$PORT"
export SPRING_BIN="$BIN"
DAEMON="$ROOT/freiquell_server_c++/daemon/install.sh"
[ -x "$DAEMON" ] || die "missing $DAEMON"

if [ "$(id -u)" -eq 0 ] || sudo -n true 2>/dev/null; then
  log "Installing Spring systemd unit (system)"
  "$DAEMON" --system
else
  log "Installing Spring systemd user unit"
  "$DAEMON" --user
fi

ok=0
for _ in $(seq 1 20); do
  if curl -sf --max-time 1 http://127.0.0.1:${PORT}/api/health >/dev/null; then
    ok=1
    break
  fi
  sleep 0.3
done
if [ "$ok" -eq 1 ]; then
  log "Spring up on 127.0.0.1:${PORT}"
else
  warn "Spring not answering yet — journalctl -u spring -e  (or --user)"
fi
