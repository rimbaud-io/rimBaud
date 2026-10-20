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

# systemd: hunters compose + Spring. Default --system when root.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

MODE=user
if [ "${1:-}" = "--system" ] || [ "$(id -u)" -eq 0 ]; then
  MODE=system
fi

mapfile -t SVCS < <(matrix_services)
START_CMD="/usr/bin/docker compose --project-directory $ROOT up -d ${SVCS[*]}"
STOP_CMD="/usr/bin/docker compose --project-directory $ROOT stop ${SVCS[*]}"

UNIT=$(sed \
  -e "s|@ROOT@|$ROOT|g" \
  -e "s|@START@|$START_CMD|g" \
  -e "s|@STOP@|$STOP_CMD|g" \
  -e "s|@USER@|$REAL_USER|g" \
  "$INSTALL_DIR/rimbaud-hunters.service.in")

if [ "$MODE" = "system" ]; then
  TARGET=/etc/systemd/system/rimbaud-hunters.service
  log "Installing $TARGET"
  printf '%s\n' "$UNIT" | as_root tee "$TARGET" >/dev/null
  as_root systemctl daemon-reload
  as_root systemctl enable --now rimbaud-hunters.service
  as_root systemctl disable --now freiquell.service 2>/dev/null || true
  as_root systemctl --no-pager --full status rimbaud-hunters.service | head -n 16 || true
else
  mkdir -p "$REAL_HOME/.config/systemd/user"
  TARGET="$REAL_HOME/.config/systemd/user/rimbaud-hunters.service"
  log "Installing $TARGET"
  printf '%s\n' "$UNIT" > "$TARGET"
  systemctl --user daemon-reload
  loginctl enable-linger "$REAL_USER" 2>/dev/null || warn "could not enable lingering"
  systemctl --user enable --now rimbaud-hunters.service
  systemctl --user --no-pager --full status rimbaud-hunters.service | head -n 16 || true
fi

log "Autostart enabled ($MODE) — hunters. Spring unit is install_new/spring.sh"
