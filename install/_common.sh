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

# Shared helpers for install_new/*.sh — source this, don't run it.
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$INSTALL_DIR/.." && pwd)"
if [ "${RIMBAUD_BOOTSTRAP:-1}" != "0" ]; then
  SERVICES_FILE="$INSTALL_DIR/services.bootstrap.list"
else
  SERVICES_FILE="$INSTALL_DIR/services.list"
fi
[ -f "$SERVICES_FILE" ] || SERVICES_FILE="$INSTALL_DIR/services.list"
ENV_EXAMPLE="$INSTALL_DIR/env.example"
ENV_FILE="$ROOT/.env"
REAL_USER="${SUDO_USER:-${USER:-$(id -un)}}"
REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || echo "$HOME")"

cd "$ROOT"

log()  { printf '\033[0;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

as_root() {
  # env so VAR=value works when already root (sudo ./install.sh).
  # "$@" would try to exec "DEBIAN_FRONTEND=noninteractive" as a command.
  if [ "$(id -u)" -eq 0 ]; then
    env "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo env "$@"
  else
    die "need root (or sudo) for: $*"
  fi
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    as_root docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    as_root docker-compose "$@"
  else
    die "Docker Compose not found (need 'docker compose' plugin)"
  fi
}

matrix_services() {
  grep -E -v '^[[:space:]]*(#|$)' "$SERVICES_FILE"
}

docker_ok() {
  as_root docker info >/dev/null 2>&1
}

overlay_files() {
  cat <<'EOF'
hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_derivative.py
hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_api_order_book_data_source.py
hummingbot/data_feed/candles_feed/candles_base.py
hummingbot/core/data_type/order_book_tracker.py
hummingbot/strategy/strategy_v2_base.py
EOF
}
