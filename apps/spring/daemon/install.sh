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

# Portable Spring systemd installer. Same steps on any Linux box.
#
#   ./daemon/install.sh           # system unit if we can, else --user
#   ./daemon/install.sh --user    # linger user unit (no root)
#   ./daemon/install.sh --system  # /etc/systemd/system (needs sudo)
#
# Env: FREIQUELL_CONF  SPRING_PORT (default 5004, never 5001)  SPRING_BIN
# Does not touch Flask :5001 or hunter containers.

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
MODE=auto
for a in "$@"; do
  case "$a" in
    --user) MODE=user ;;
    --system) MODE=system ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0
      ;;
  esac
done

PORT=${SPRING_PORT:-5004}
if [[ "$PORT" == "5001" ]]; then
  echo "install.sh: refusing port 5001 — that is live Flask" >&2
  exit 1
fi

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing $1" >&2; exit 1; }; }
need systemctl
need sed
need make

echo "== build spring =="
make -C "$ROOT" all -j"$(nproc 2>/dev/null || echo 2)"

BIN=${SPRING_BIN:-}
if [[ -z "$BIN" ]]; then
  if [[ -x "$ROOT/spring" ]]; then BIN=$ROOT/spring
  elif [[ -x "$ROOT/freiquell-server" ]]; then BIN=$ROOT/freiquell-server
  else echo "spring binary not found in $ROOT" >&2; exit 1; fi
fi
BIN=$(readlink -f "$BIN")

CONF=${FREIQUELL_CONF:-}
if [[ -z "$CONF" ]]; then
  for g in "$ROOT/../conf" "$ROOT/conf" ./conf ../conf; do
    if [[ -f "$g/wire_registry.json" || -f "$g/bot_sl.json" ]]; then CONF=$g; break; fi
  done
fi
[[ -n "$CONF" ]] || { echo "cannot find conf/ (wire_registry.json). Set FREIQUELL_CONF" >&2; exit 1; }
CONF=$(readlink -f "$CONF")
WORKDIR=$(dirname "$BIN")
RUNUSER=$(id -un)

if [[ "$MODE" == "auto" ]]; then
  if [[ -w /etc/systemd/system ]] || sudo -n true 2>/dev/null; then MODE=system
  else
    echo "no root write to /etc/systemd/system — installing --user unit"
    MODE=user
  fi
fi

if [[ "$MODE" == "system" ]]; then
  UNIT_DIR=/etc/systemd/system
  LOG_DIR=/var/log/spring
  LOG=$LOG_DIR/spring.log
  SYSTEMCTL=(systemctl)
  SUDO=()
  if [[ ! -w "$UNIT_DIR" ]]; then SUDO=(sudo); fi
  USER_LINE="User=$RUNUSER"
  WANTED_BY=multi-user.target
else
  UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
  LOG_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/spring
  LOG=$LOG_DIR/spring.log
  SYSTEMCTL=(systemctl --user)
  SUDO=()
  USER_LINE="# User= (user unit)"
  WANTED_BY=default.target
fi

echo "== paths =="
echo "  mode    $MODE"
echo "  bin     $BIN"
echo "  conf    $CONF"
echo "  port    $PORT"
echo "  unit    $UNIT_DIR/spring.service"
echo "  log     $LOG"
echo "  user    $RUNUSER"

"${SUDO[@]}" mkdir -p "$UNIT_DIR" "$LOG_DIR"
"${SUDO[@]}" touch "$LOG"
"${SUDO[@]}" chmod 644 "$LOG" 2>/dev/null || true
if [[ "$MODE" == "user" ]]; then
  mkdir -p "$LOG_DIR"
  touch "$LOG"
  chmod 644 "$LOG"
fi

TMP=$(mktemp)
sed \
  -e "s|@SPRING_BIN@|$BIN|g" \
  -e "s|@CONF@|$CONF|g" \
  -e "s|@PORT@|$PORT|g" \
  -e "s|@WORKDIR@|$WORKDIR|g" \
  -e "s|@LOG@|$LOG|g" \
  -e "s|@USER_LINE@|$USER_LINE|g" \
  -e "s|@WANTED_BY@|$WANTED_BY|g" \
  "$HERE/spring.service.in" > "$TMP"
"${SUDO[@]}" cp "$TMP" "$UNIT_DIR/spring.service"
rm -f "$TMP"

# Stop a leftover command-line Spring so the unit can bind :PORT.
if command -v pgrep >/dev/null 2>&1; then
  while read -r p; do
    [[ -n "$p" ]] || continue
    comm=$(tr '\0' ' ' < "/proc/$p/comm" 2>/dev/null || true)
    [[ "$comm" == spring ]] || continue
    echo "  stopping leftover command spring pid $p"
    kill -TERM "$p" 2>/dev/null || true
  done < <(pgrep -x spring || true)
  sleep 0.4
fi

"${SYSTEMCTL[@]}" daemon-reload
if [[ "$MODE" == "user" ]]; then
  loginctl enable-linger "$RUNUSER" 2>/dev/null || \
    echo "note: could not enable lingering — user unit may stop on logout"
fi
"${SYSTEMCTL[@]}" enable --now spring.service

# Remember source for springctl.
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/freiquell"
echo daemon > "${XDG_CONFIG_HOME:-$HOME/.config}/freiquell/springctl.source"

echo "== status =="
"${SYSTEMCTL[@]}" --no-pager --full status spring.service | head -n 20 || true
echo
echo "Spring daemon installed. Flask :5001 and hunters were not touched."
echo "  logs:    tail -f $LOG"
echo "  ctl:     $ROOT/springctl     (switch DAEMON / COMMAND)"
echo "  stop:    ${SYSTEMCTL[*]} stop spring"
echo "  remove:  $HERE/uninstall.sh${MODE:+ --$MODE}"
