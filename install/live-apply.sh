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

# Run ON the target machine. REMOTE_ROOT already holds the new code.
# Copy that code onto the tree the running Spring and hunters actually use.
# Never copy wallets, connector keystores, logs, data, or .compose.env.
# Never start a second Spring when port 5004 is already taken.
# Never start an exited hunter. Restarting a paused hunter brings it back running.
#
# Exit 0  live Spring is the new binary and running hunters were restarted
# Exit 4  files copied, live Spring still the old process (sudo needed)
# Exit 3  live Spring updated, a hunter restart failed
# Exit 1  copy failed
#
# LIVE_APPLY_SELFTEST=1 prints the detected trees and exits before any change.
set -euo pipefail

REMOTE_ROOT="${REMOTE_ROOT:-}"
LIVE_HINT="${LIVE_HINT:-}"
SELFTEST="${LIVE_APPLY_SELFTEST:-0}"

[ -n "$REMOTE_ROOT" ] || { echo "FAIL REMOTE_ROOT empty"; exit 1; }
[ -d "$REMOTE_ROOT/scripts" ] || { echo "FAIL $REMOTE_ROOT has no scripts"; exit 1; }

me_uid=$(id -u)
me_user=$(id -un)

detect_springs() {
  local f pid
  shopt -s nullglob
  for f in /proc/[0-9]*/cmdline; do
    pid=${f%/cmdline}
    pid=${pid##*/}
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    [ -r "$f" ] || continue
    local -a args=()
    mapfile -d '' args < "$f" 2>/dev/null || true
    [ "${#args[@]}" -gt 0 ] || continue
    [ "$(basename "${args[0]}")" = "spring" ] || continue
    local conf="" port="" i
    for ((i = 0; i < ${#args[@]}; i++)); do
      case "${args[$i]}" in
        --conf) conf="${args[$((i + 1))]:-}" ;;
        --port) port="${args[$((i + 1))]:-}" ;;
      esac
    done
    [ -n "$conf" ] || continue
    local uid user
    uid=$(stat -c %u "/proc/$pid" 2>/dev/null || echo "")
    user=$(stat -c %U "/proc/$pid" 2>/dev/null || echo "?")
    printf '%s %s %s %s %s\n' "$pid" "$uid" "$user" "$conf" "${port:-}"
  done
}

listener_pid=""
listener_uid=""
listener_user=""
listener_conf=""
listener_port=""
best=""
fallback=""
while read -r pid uid user conf port; do
  [ -n "${pid:-}" ] || continue
  if [ "$port" = "5004" ] && [ -z "$best" ]; then
    best="$pid $uid $user $conf $port"
  elif [ -z "$fallback" ]; then
    fallback="$pid $uid $user $conf $port"
  fi
done < <(detect_springs)
pick="${best:-$fallback}"
if [ -n "$pick" ]; then
  # shellcheck disable=SC2086
  read -r listener_pid listener_uid listener_user listener_conf listener_port <<<"$pick"
fi

spring_root=""
if [ -n "$listener_conf" ]; then
  spring_root=$(dirname "$listener_conf")
fi

HUNTERS=(
  hummingbot-hl hummingbot-bn-swing hummingbot-helliot hummingbot-surfn
  hummingbot-shortn hummingbot-dman hummingbot-onlyup hummingbot-dman-maker
  hummingbot-dashboard hummingbot-audit hummingbot-condor
)

compose_dir=""
if command -v docker >/dev/null 2>&1; then
  for n in "${HUNTERS[@]}"; do
    st=$(docker inspect -f '{{.State.Status}}' "$n" 2>/dev/null || echo missing)
    case "$st" in
      running|paused) ;;
      *) continue ;;
    esac
    wd=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$n" 2>/dev/null || true)
    if [ -n "$wd" ] && [ -d "$wd" ]; then
      compose_dir=$wd
      break
    fi
  done
fi

live="$REMOTE_ROOT"
if [ -n "$LIVE_HINT" ] && [ -d "$LIVE_HINT" ]; then
  live="$LIVE_HINT"
elif [ -n "$spring_root" ] && [ -d "$spring_root" ]; then
  live="$spring_root"
elif [ -n "$compose_dir" ] && [ -d "$compose_dir" ]; then
  live="$compose_dir"
fi

echo "LIVE_ROOT=$live"
echo "SPRING_PID=${listener_pid:-none}"
echo "SPRING_USER=${listener_user:-none}"
echo "SPRING_CONF=${listener_conf:-none}"
echo "COMPOSE_DIR=${compose_dir:-none}"
echo "SSH_USER=$me_user"

if [ "$SELFTEST" = "1" ]; then
  exit 0
fi

# rsync -a tries to preserve mtimes. A root-owned symlink in a directory we
# can write (not sticky) cannot have its time set (EPERM) and rsync exits 23
# even when the link target already matches. Unlink only those files and
# retry once so the new link is ours. Never remove a directory.
rsync_code() {
  local out rc=0
  out=$(rsync "$@" 2>&1) || rc=$?
  if [ -n "$out" ]; then printf '%s\n' "$out"; fi
  if [ "$rc" -eq 0 ]; then return 0; fi
  if [ "$rc" -ne 23 ]; then return "$rc"; fi
  local path n=0
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if [ -L "$path" ] || [ -f "$path" ]; then
      if rm -f -- "$path"; then
        echo "OK   removed $path (could not set its time)"
        n=$((n + 1))
      else
        echo "FAIL cannot remove $path"
      fi
    fi
  done < <(printf '%s\n' "$out" | sed -n 's/.*failed to set times on "\([^"]*\)".*/\1/p')
  if [ "$n" -eq 0 ]; then
    echo "FAIL rsync exit 23"
    return 23
  fi
  rsync "$@"
}

copy_tree() {
  local src="$1" dst="$2"
  [ "$src" != "$dst" ] || return 0
  [ -d "$src" ] || { echo "FAIL missing source $src"; return 1; }
  mkdir -p "$dst"
  local d
  for d in scripts dashboard condor freiquell_server_c++ freiquell_c++ \
           rimbaud_meta_c++ rimbaud_vpn install_new version rimbaud-archy iphone; do
    [ -d "$src/$d" ] || continue
    mkdir -p "$dst/$d"
    rsync_code -a --delete --omit-dir-times --no-owner --no-group \
      --exclude '__pycache__/' --exclude '*.pyc' \
      "$src/$d/" "$dst/$d/"
  done
  if [ -f "$src/docker-compose.yml" ]; then
    rsync_code -a --no-owner --no-group "$src/docker-compose.yml" "$dst/docker-compose.yml"
  fi
  if [ -f "$src/docker-compose.v2-sleep.yml" ]; then
    rsync_code -a --no-owner --no-group "$src/docker-compose.v2-sleep.yml" "$dst/docker-compose.v2-sleep.yml"
  fi
  local f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$src/$f" ] || continue
    mkdir -p "$dst/$(dirname "$f")"
    rsync_code -a --no-owner --no-group "$src/$f" "$dst/$f"
  done <<'EOF'
hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_derivative.py
hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_api_order_book_data_source.py
hummingbot/data_feed/candles_feed/candles_base.py
hummingbot/core/data_type/order_book_tracker.py
hummingbot/strategy/strategy_v2_base.py
EOF
  if [ -f "$src/conf/archy.json" ]; then
    mkdir -p "$dst/conf"
    rsync_code -a --no-owner --no-group "$src/conf/archy.json" "$dst/conf/archy.json"
  fi
  if [ -f "$src/conf/archy_deployed.json" ]; then
    mkdir -p "$dst/conf"
    rsync_code -a --no-owner --no-group "$src/conf/archy_deployed.json" "$dst/conf/archy_deployed.json"
  fi
  echo "OK   code copied → $dst  (conf wallets left alone)"
}

DESTS=()
add_dest() {
  local d="$1"
  [ -n "$d" ] && [ -d "$d" ] || return 0
  [ "$d" = "$REMOTE_ROOT" ] && return 0
  local x
  for x in "${DESTS[@]:-}"; do
    [ "$x" = "$d" ] && return 0
  done
  DESTS+=("$d")
}
add_dest "$live"
add_dest "$spring_root"
add_dest "$compose_dir"

for d in "${DESTS[@]:-}"; do
  [ -n "$d" ] || continue
  copy_tree "$REMOTE_ROOT" "$d"
done
if [ "${#DESTS[@]}" -eq 0 ]; then
  echo "OK   live tree is $REMOTE_ROOT"
fi

relaunch_ui() {
  local needle="$1"
  local f pid
  shopt -s nullglob
  for f in /proc/[0-9]*/cmdline; do
    pid=${f%/cmdline}
    pid=${pid##*/}
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    local uid
    uid=$(stat -c %u "/proc/$pid" 2>/dev/null || echo "")
    [ "$uid" = "$me_uid" ] || continue
    [ -r "$f" ] || continue
    local -a args=()
    mapfile -d '' args < "$f" 2>/dev/null || true
    [ "${#args[@]}" -gt 0 ] || continue
    [ "$(basename "${args[0]}")" = "$needle" ] || continue
    local display xauth xdg cwd bin
    display=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^DISPLAY=//p' | head -1 || true)
    if [ -z "$display" ]; then
      echo "!!  $needle pid $pid has no DISPLAY — left running"
      continue
    fi
    xauth=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^XAUTHORITY=//p' | head -1 || true)
    xdg=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^XDG_RUNTIME_DIR=//p' | head -1 || true)
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || echo "$HOME")
    bin="${args[0]}"
    case "$bin" in
      /*) ;;
      *) bin="$cwd/$bin" ;;
    esac
    if [ ! -x "$bin" ]; then
      echo "!!  $needle binary missing ($bin) — left running"
      continue
    fi
    kill -TERM "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      sleep 0.2
    fi
    if kill -0 "$pid" 2>/dev/null; then
      echo "FAIL $needle pid $pid did not stop"
      continue
    fi
    (
      cd "$cwd" 2>/dev/null || cd "$HOME"
      [ -n "$xauth" ] && export XAUTHORITY="$xauth"
      [ -n "$xdg" ] && export XDG_RUNTIME_DIR="$xdg"
      export DISPLAY="$display"
      setsid "$bin" >>"/tmp/${needle}.log" 2>&1 </dev/null &
    )
    echo "OK   relaunched $needle"
  done
}

relaunch_ui rimbaud-desk
relaunch_ui springctl

start_user_spring() {
  local root="$1"
  local bin="$root/freiquell_server_c++/spring"
  [ -x "$bin" ] || { echo "FAIL no spring binary at $bin"; return 1; }
  mkdir -p "$HOME/.local/share/spring"
  setsid "$bin" --debug --conf "$root/conf" --port 5004 \
    >>"$HOME/.local/share/spring/spring.log" 2>&1 </dev/null &
  echo "OK   started spring on $root/conf pid $!"
}

restart_own_spring() {
  local pid="$1" root="$2"
  local bin="$root/freiquell_server_c++/spring"
  if systemctl --user is-active spring.service >/dev/null 2>&1; then
    systemctl --user restart spring.service
    echo "OK   systemctl --user restart spring.service"
    return 0
  fi
  [ -x "$bin" ] || { echo "FAIL no spring binary at $bin"; return 1; }
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
    sleep 0.2
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "FAIL could not stop spring pid $pid"
    return 1
  fi
  mkdir -p "$HOME/.local/share/spring"
  setsid "$bin" --debug --conf "$root/conf" --port 5004 \
    >>"$HOME/.local/share/spring/spring.log" 2>&1 </dev/null &
  echo "OK   restarted our spring pid $!"
}

port_open() {
  python3 - <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", 5004))
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

spring_rc=0
if [ -n "$listener_pid" ] && [ "$listener_uid" = "$me_uid" ]; then
  restart_own_spring "$listener_pid" "${spring_root:-$live}" || spring_rc=$?
elif [ -n "$listener_pid" ]; then
  unit=$(grep -oE '[A-Za-z0-9@_.-]+\.service' "/proc/$listener_pid/cgroup" 2>/dev/null | grep -v '^user@' | tail -1 || true)
  [ -n "$unit" ] || unit=spring.service
  echo "CMD  sudo -n systemctl restart $unit"
  if sudo -n systemctl restart "$unit"; then
    echo "OK   sudo restarted $unit"
  else
    echo "FAIL root Spring pid $listener_pid ($unit) still the old process — sudo needs a password"
    echo "FAIL on this machine: sudo systemctl restart $unit"
    spring_rc=4
  fi
elif port_open; then
  echo "FAIL port 5004 is open and no Spring command line was readable"
  spring_rc=4
else
  start_user_spring "$live" || spring_rc=$?
fi

verified=0
if [ "$spring_rc" -eq 0 ]; then
  if python3 - <<'PY'
import json, sys, time, urllib.request
for _ in range(20):
    try:
        with urllib.request.urlopen("http://127.0.0.1:5004/api/health", timeout=2) as r:
            h = json.load(r)
        hk = h.get("hl_key")
        if isinstance(hk, dict) and hk.get("state"):
            print("OK   live spring hl_key %s  conf %s" % (hk.get("state"), h.get("conf", "")))
            sys.exit(0)
    except Exception:
        pass
    time.sleep(0.5)
print("FAIL live spring has no hl_key — process is still the old binary")
sys.exit(1)
PY
  then
    verified=1
  fi
fi

hunter_rc=0
if ! command -v docker >/dev/null 2>&1; then
  echo "!!  no docker — hunters left as they are"
else
  any=0
  for n in "${HUNTERS[@]}"; do
    st=$(docker inspect -f '{{.State.Status}}' "$n" 2>/dev/null || echo missing)
    case "$st" in
      running|paused)
        wd=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$n" 2>/dev/null || true)
        if [ -n "$compose_dir" ] && [ -n "$wd" ] && [ "$wd" != "$compose_dir" ]; then
          echo "!!  $n is in $wd — left"
          continue
        fi
        echo "CMD  docker restart $n ($st)"
        if docker restart "$n" >/dev/null; then
          echo "OK   docker restart $n"
          any=1
        else
          echo "FAIL docker restart $n"
          hunter_rc=1
        fi
        ;;
      missing) ;;
      *) echo "OK   $n is $st — left stopped" ;;
    esac
  done
  if [ "$any" -eq 0 ] && [ "$hunter_rc" -eq 0 ]; then
    echo "OK   no running hunters to restart — none were started"
  fi
fi

if [ "$verified" != "1" ]; then
  echo "FAIL root Spring still old — sudo systemctl restart spring on this machine"
  exit 4
fi
if [ "$hunter_rc" != "0" ]; then
  echo "FAIL live spring updated; a hunter restart failed. Positions stay."
  exit 3
fi
echo "OK   live spring and running hunters restarted. Positions stay."
exit 0
