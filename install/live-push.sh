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

# Live-push newest Spring + matrix CODE to a meta client.
# Copies the home tree and the tree the running Spring and hunters use.
# Never copies wallets, .compose.env, VPN keys, logs, or matrix.db.
# Never starts a second Spring. Never CLOSE/OPEN Hyperliquid.
# A paused hunter that is restarted comes back running. Exited hunters stay stopped.
#
#   ./install_new/live-push.sh              # default site usb-stick
#   ./install_new/live-push.sh usb-stick
#   ./install_new/live-push.sh --samba-only  # stage to the share if SSH is down
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

need_cmd rsync
need_cmd ssh
need_cmd python3

SITE="usb-stick"
SAMBA_ONLY=0
for a in "$@"; do
  case "$a" in
    --samba-only) SAMBA_ONLY=1 ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) SITE="$a" ;;
  esac
done

HOSTS="$ROOT/conf/deploy_hosts.json"
JOB="$ROOT/conf/live_push_job.json"
LOG="${LIVE_PUSH_LOG:-/tmp/rimbaud-live-push.log}"
SHARE="${RIMBAUD_SHARE:-$HOME/share}"
KEY="${RIMBAUD_DEPLOY_KEY:-$HOME/.ssh/id_ed25519_rimbaud_deploy}"

job_set() {
  python3 - "$JOB" "$@" <<'PY'
import json, os, sys, time
path = sys.argv[1]
row = {}
if os.path.isfile(path):
    try:
        row = json.load(open(path))
    except Exception:
        row = {}
if not isinstance(row, dict):
    row = {}
i = 2
while i < len(sys.argv):
    k = sys.argv[i]
    v = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
    row[k] = v
    i += 2
row["updated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
try:
    with open(tmp, "w") as f:
        json.dump(row, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
except OSError:
    pass
PY
}

# Meta desk already redirects stdout into $LOG. CLI still tees.
if [ "${LIVE_PUSH_FROM_META:-0}" != "1" ] && [ -t 1 ]; then
  : > "$LOG"
  exec > >(tee -a "$LOG") 2>&1
fi

log()  { printf '==> %s\n' "$*"; }
warn() { printf '!!  %s\n' "$*" >&2; }
die()  { printf 'FAIL %s\n' "$*" >&2; exit 1; }

step() {
  local title="$1"
  shift
  printf 'CMD  %s\n' "$title"
  if "$@"; then
    printf 'OK   %s\n' "$title"
    return 0
  else
    local rc=$?
    printf 'FAIL %s  (exit %d)\n' "$title" "$rc"
    return "$rc"
  fi
}

step_soft() {
  local title="$1"
  shift
  printf 'CMD  %s\n' "$title"
  if "$@"; then
    printf 'OK   %s\n' "$title"
  else
    local rc=$?
    printf 'FAIL %s  (exit %d)  — continuing\n' "$title" "$rc"
  fi
  return 0
}

printf 'CMD  live-push site=%s  (code only, no wallets, no HL close)\n' "$SITE"
job_set state running site "$SITE" log "$LOG" message "starting"
printf 'OK   job file %s\n' "$JOB"

load_host() {
  python3 - "$HOSTS" "$SITE" <<'PY'
import json, sys
path, site = sys.argv[1], sys.argv[2]
try:
    j = json.load(open(path))
except Exception:
    sys.exit(2)
row = j.get(site) or j.get("sites", {}).get(site) if isinstance(j, dict) else None
if not isinstance(row, dict):
    # allow {sites:[{id,ssh,root}]}
    if isinstance(j, dict):
        for s in j.get("sites") or []:
            if isinstance(s, dict) and s.get("id") == site:
                row = s
                break
if not isinstance(row, dict):
    sys.exit(3)
def g(k, d=""):
    v = row.get(k)
    print("" if v is None else str(v) if d == "" else (str(v) if v else d))
print(row.get("name") or site)
print(row.get("ssh") or "")
print(row.get("ssh_lan") or "")
print(row.get("root") or "")
print(row.get("identity") or "")
print(row.get("live_root") or "")
PY
}

if [ ! -f "$HOSTS" ]; then
  die "missing $HOSTS"
fi
mapfile -t H < <(load_host) || true
if [ "${#H[@]}" -lt 5 ]; then
  die "site '$SITE' not in $HOSTS"
fi
NAME="${H[0]}"
SSH_VPN="${H[1]}"
SSH_LAN="${H[2]}"
REMOTE_ROOT="${H[3]}"
IDENT="${H[4]:-$KEY}"
LIVE_HINT="${H[5]:-}"
[ -n "$REMOTE_ROOT" ] || die "deploy_hosts.json $SITE missing root"

ensure_key() {
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  if [ ! -f "$IDENT" ]; then
    log "creating deploy key $IDENT"
    ssh-keygen -t ed25519 -N "" -f "$IDENT" -C "rimbaud-live-push" >/dev/null
  fi
  chmod 600 "$IDENT"
  if [ -d "$SHARE" ] && [ -w "$SHARE" ]; then
    cp -f "${IDENT}.pub" "$SHARE/rimbaud-deploy.pub"
  fi
}

RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude '**/.git/'
  --exclude '**/node_modules/'
  --exclude '**/__pycache__/'
  --exclude '**/*.pyc'
  --exclude 'logs/'
  --exclude 'data/'
  --exclude 'certs/'
  --exclude 'gateway-files/'
  --exclude 'dist/'
  --exclude '.env'
  --exclude '.compose.env'
  --exclude 'conf/'
  --exclude 'matrix.db'
  --exclude 'matrix.db-*'
  --exclude 'conf/vpn/'
  --exclude 'conf/hl_wallets/'
  --exclude 'conf/connectors/'
  --exclude 'conf-*/connectors/'
)

push_tree() {
  local dest="$1"
  step "rsync code → $dest" rsync -az --delete \
    "${RSYNC_EXCLUDES[@]}" \
    --include '/scripts/' \
    --include '/scripts/**' \
    --include '/dashboard/' \
    --include '/dashboard/**' \
    --include '/condor/' \
    --include '/condor/**' \
    --include '/freiquell_server_c++/' \
    --include '/freiquell_server_c++/**' \
    --include '/freiquell_c++/' \
    --include '/freiquell_c++/**' \
    --include '/rimbaud_meta_c++/' \
    --include '/rimbaud_meta_c++/**' \
    --include '/rimbaud_vpn/' \
    --include '/rimbaud_vpn/**' \
    --include '/install_new/' \
    --include '/install_new/**' \
    --include '/version/' \
    --include '/version/**' \
    --include '/iphone/' \
    --include '/iphone/**' \
    --include '/docker-compose.yml' \
    --include '/docker-compose.v2-sleep.yml' \
    "$ROOT"/ "$dest"
  while read -r f; do
    [ -n "$f" ] || continue
    [ -f "$ROOT/$f" ] || continue
    mkdir -p "$(dirname "$dest/$f")"
    step_soft "rsync overlay $(basename "$f")" rsync -az "$ROOT/$f" "$dest/$f"
  done < <(overlay_files)
}

stage_samba() {
  local dest="$SHARE/live-push"
  mkdir -p "$dest"
  push_tree "$dest/"
  mkdir -p "$dest/conf"
  if [ -f "$ROOT/conf/archy.json" ]; then
    cp -f "$ROOT/conf/archy.json" "$dest/conf/archy.json"
  fi
  cat > "$SHARE/APPLY-ON-REMOTE.sh" <<EOF
#!/usr/bin/env bash
# Run ON the machine after a live-push staged to the operator share.
# Copies CODE only. Leaves wallets in conf alone.
# Updates the tree the running Spring uses. Does not start a second Spring.
set -euo pipefail
SRC="\$HOME/desk-share/live-push"
DST="\$HOME/hummingbird/hummingbot"
[ -d "\$SRC/scripts" ] || { echo "xx mount share first: sudo mount -t cifs //192.168.1.10/share \$HOME/desk-share -o guest,uid=\$(id -u),gid=\$(id -g),iocharset=utf8"; exit 1; }
rsync -a --delete --no-owner --no-group \\
  --exclude 'conf/' --exclude 'logs/' --exclude 'data/' --exclude 'certs/' \\
  --exclude '.compose.env' --exclude '.env' \\
  --exclude 'matrix.db' --exclude 'matrix.db-*' \\
  "\$SRC"/ "\$DST"/
if [ -f "\$SRC/conf/archy.json" ]; then
  mkdir -p "\$DST/conf"
  cp -f "\$SRC/conf/archy.json" "\$DST/conf/archy.json"
fi
if [ -f "\$SRC/conf/archy_deployed.json" ]; then
  mkdir -p "\$DST/conf"
  cp -f "\$SRC/conf/archy_deployed.json" "\$DST/conf/archy_deployed.json"
fi
set +e
REMOTE_ROOT="\$DST" bash "\$DST/install_new/live-apply.sh"
rc=\$?
set -e
if [ "\$rc" -ne 0 ]; then
  echo "FAIL apply exit \$rc — positions stay on Hyperliquid"
  exit "\$rc"
fi
echo "==> done. positions stay on Hyperliquid."
EOF
  chmod +x "$SHARE/APPLY-ON-REMOTE.sh"
  cp -f "$ROOT/install_new/REMOTE-SSH.sh" "$SHARE/REMOTE-SSH.sh"
  chmod +x "$SHARE/REMOTE-SSH.sh"
  if [ -f "${IDENT}.pub" ]; then
    cp -f "${IDENT}.pub" "$SHARE/rimbaud-deploy.pub"
  fi
  printf 'OK   staged %s  +  APPLY-ON-REMOTE.sh  +  REMOTE-SSH.sh\n' "$dest"
}

ssh_ok() {
  local spec="$1"
  ssh -i "$IDENT" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=6 \
      -o StrictHostKeyChecking=accept-new "$spec" 'echo ok' >/dev/null 2>&1
}

ssh_probe_why() {
  local spec="$1" out rc
  set +e
  out=$(ssh -i "$IDENT" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=6 \
      -o StrictHostKeyChecking=accept-new "$spec" 'echo ok' 2>&1)
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    echo ok
    return 0
  fi
  echo "$out" | tr '\n' ' ' | sed 's/  */ /g; s/^ *//; s/ *$//'
  return 1
}

remote_apply() {
  local spec="$1"
  local qroot qhint
  qroot=$(printf '%q' "$REMOTE_ROOT")
  qhint=$(printf '%q' "$LIVE_HINT")
  ssh -i "$IDENT" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=8 \
      -o ServerAliveInterval=15 -o ServerAliveCountMax=40 \
      -o StrictHostKeyChecking=accept-new "$spec" \
      "REMOTE_ROOT=$qroot LIVE_HINT=$qhint bash -s" \
      < "$ROOT/install_new/live-apply.sh"
}

step "ensure deploy key" ensure_key
if [ "$SAMBA_ONLY" -eq 1 ]; then
  step "stage samba share" stage_samba
  job_set state ok site "$SITE" message "staged samba — run APPLY-ON-REMOTE.sh on remote"
  printf 'OK   SSH skipped (--samba-only). On remote: bash ~/desk-share/APPLY-ON-REMOTE.sh\n'
  exit 0
fi

TARGET=""
for spec in "$SSH_VPN" "$SSH_LAN"; do
  [ -n "$spec" ] || continue
  printf 'CMD  ssh probe %s\n' "$spec"
  why="$(ssh_probe_why "$spec" || true)"
  if [ "$why" = "ok" ]; then
    printf 'OK   ssh probe %s\n' "$spec"
    TARGET="$spec"
    break
  else
    printf 'FAIL ssh probe %s  (%s)\n' "$spec" "${why:-not accepted}"
  fi
done

if [ -z "$TARGET" ]; then
  printf 'FAIL SSH not accepted — on remote run: bash ~/desk-share/REMOTE-SSH.sh\n'
  step "stage samba share (fallback)" stage_samba
  job_set state wait-ssh site "$SITE" message "no SSH — staged samba + REMOTE-SSH.sh"
  echo
  echo "On remote:"
  echo "  bash ~/desk-share/REMOTE-SSH.sh"
  echo "  then PUSH latest again from meta"
  echo "or apply staged code without SSH:"
  echo "  bash ~/desk-share/APPLY-ON-REMOTE.sh"
  exit 2
fi

printf 'CMD  pushing %s via %s → %s\n' "$NAME" "$TARGET" "$REMOTE_ROOT"
RSYNC_RSH="ssh -i $IDENT -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
rsync_one() {
  local label="$1" src="$2" dst="$3"
  step "rsync $label" rsync -az --delete "${RSYNC_EXCLUDES[@]}" -e "$RSYNC_RSH" "$src" "$dst"
}
rsync_one scripts "$ROOT/scripts/" "$TARGET:$REMOTE_ROOT/scripts/"
rsync_one dashboard "$ROOT/dashboard/" "$TARGET:$REMOTE_ROOT/dashboard/"
rsync_one condor "$ROOT/condor/" "$TARGET:$REMOTE_ROOT/condor/"
rsync_one spring "$ROOT/freiquell_server_c++/" "$TARGET:$REMOTE_ROOT/freiquell_server_c++/"
rsync_one desk "$ROOT/freiquell_c++/" "$TARGET:$REMOTE_ROOT/freiquell_c++/"
rsync_one meta "$ROOT/rimbaud_meta_c++/" "$TARGET:$REMOTE_ROOT/rimbaud_meta_c++/"
rsync_one vpn "$ROOT/rimbaud_vpn/" "$TARGET:$REMOTE_ROOT/rimbaud_vpn/"
rsync_one install_new "$ROOT/install_new/" "$TARGET:$REMOTE_ROOT/install_new/"
rsync_one version "$ROOT/version/" "$TARGET:$REMOTE_ROOT/version/"
if [ -d "$ROOT/rimbaud-archy" ]; then
  rsync_one archy "$ROOT/rimbaud-archy/" "$TARGET:$REMOTE_ROOT/rimbaud-archy/"
else
  printf 'CMD  rsync archy\nOK   skipped (no dir)\n'
fi
if [ -d "$ROOT/iphone" ]; then
  rsync_one iphone "$ROOT/iphone/" "$TARGET:$REMOTE_ROOT/iphone/"
else
  printf 'CMD  rsync iphone\nOK   skipped (no dir)\n'
fi
step "rsync docker-compose.yml" rsync -az -e "$RSYNC_RSH" "$ROOT/docker-compose.yml" "$TARGET:$REMOTE_ROOT/docker-compose.yml"
if [ -f "$ROOT/docker-compose.v2-sleep.yml" ]; then
  step_soft "rsync docker-compose.v2-sleep.yml" rsync -az -e "$RSYNC_RSH" "$ROOT/docker-compose.v2-sleep.yml" "$TARGET:$REMOTE_ROOT/docker-compose.v2-sleep.yml"
fi
while read -r f; do
  [ -f "$ROOT/$f" ] || continue
  step_soft "mkdir overlay $(dirname "$f")" \
    ssh -n -i "$IDENT" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=8 \
        -o StrictHostKeyChecking=accept-new "$TARGET" "mkdir -p '$REMOTE_ROOT/$(dirname "$f")'"
  step_soft "rsync overlay $(basename "$f")" rsync -az -e "$RSYNC_RSH" "$ROOT/$f" "$TARGET:$REMOTE_ROOT/$f"
done < <(overlay_files)
printf 'OK   tree copied via %s\n' "$TARGET"

ARCHY_PY="$ROOT/rimbaud-archy/archy_db.py"
VER=""
printf 'CMD  read archy version\n'
if [ -x "$ROOT/rimbaud-archy/rimbaud-archy" ]; then
  VER="$("$ROOT/rimbaud-archy/rimbaud-archy" --status 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true)"
elif [ -f "$ARCHY_PY" ]; then
  VER="$(python3 "$ARCHY_PY" --status 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true)"
fi
if [ -n "$VER" ]; then
  printf 'OK   archy version %s\n' "$VER"
  step_soft "rsync conf/archy.json" rsync -az -e "$RSYNC_RSH" "$ROOT/conf/archy.json" "$TARGET:$REMOTE_ROOT/conf/archy.json"
else
  printf 'FAIL read archy version  — continuing without ledger\n'
fi

printf 'CMD  update the live Spring tree and restart what we can\n'
set +e
apply_out=$(remote_apply "$TARGET" 2>&1)
apply_rc=$?
set -e
printf '%s\n' "$apply_out"
LIVE_OUT=$(printf '%s\n' "$apply_out" | sed -n 's/^LIVE_ROOT=//p' | head -1 || true)

if [ "$apply_rc" -eq 0 ] && [ -n "$VER" ]; then
  record_archy() { python3 "$ARCHY_PY" --record-deploy "$SITE" "$NAME" "$TARGET" "$VER" >/dev/null; }
  step_soft "record deploy $VER → $SITE" record_archy
  echo "{\"version\":\"$VER\",\"site\":\"$SITE\",\"ts\":\"$(date -u +'%Y-%m-%d %H:%M:%S UTC')\"}" > /tmp/archy_deployed.json
  step_soft "rsync conf/archy_deployed.json" rsync -az -e "$RSYNC_RSH" /tmp/archy_deployed.json "$TARGET:$REMOTE_ROOT/conf/archy_deployed.json"
  if [ -n "$LIVE_OUT" ] && [ "$LIVE_OUT" != "$REMOTE_ROOT" ]; then
    step_soft "rsync live conf/archy_deployed.json" rsync -az -e "$RSYNC_RSH" /tmp/archy_deployed.json "$TARGET:$LIVE_OUT/conf/archy_deployed.json"
  fi
fi

case "$apply_rc" in
  0)
    job_set state ok site "$SITE" host "$TARGET" message "pushed $NAME $VER — live spring and running hunters restarted, positions untouched"
    printf 'OK   pushed %s %s — live spring restarted, positions stay\n' "$NAME" "$VER"
    ;;
  4)
    job_set state fail site "$SITE" host "$TARGET" message "code copied on $NAME; root Spring still old — sudo systemctl restart spring"
    printf 'FAIL root Spring still old — sudo systemctl restart spring on %s\n' "$NAME"
    exit 4
    ;;
  3)
    job_set state fail site "$SITE" host "$TARGET" message "live spring updated on $NAME; a hunter restart failed"
    printf 'FAIL live spring updated; a hunter restart failed on %s\n' "$NAME"
    exit 3
    ;;
  *)
    job_set state fail site "$SITE" host "$TARGET" message "live apply failed on $NAME (exit $apply_rc)"
    printf 'FAIL live apply exit %s on %s\n' "$apply_rc" "$NAME"
    exit 1
    ;;
esac
