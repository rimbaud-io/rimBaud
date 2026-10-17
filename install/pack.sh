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

# Build a rimBaud install tarball. No wallets, no keys, no logs, no this-desk positions.
#
#   ./install_new/pack.sh
#   ./install_new/pack.sh --with-image     # also docker save hummingbot/hummingbot:latest
#   ./install_new/pack.sh /tmp/kit.tar.gz
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

need_cmd rsync
need_cmd tar

WITH_IMAGE=0
OUT=""
for a in "$@"; do
  case "$a" in
    --with-image) WITH_IMAGE=1 ;;
    --help|-h)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *) OUT="$a" ;;
  esac
done

STAMP="$(date -u +%Y%m%d)"
if [ -z "$OUT" ]; then
  OUT="$ROOT/dist/rimbaud-install-${STAMP}.tar.gz"
fi
if [ -e "$OUT" ] && [ ! -w "$OUT" ]; then
  OUT="/tmp/rimbaud-install-${STAMP}.tar.gz"
  warn "cannot overwrite $ROOT/dist kit (root-owned) — writing $OUT"
fi
STAGE="${TMPDIR:-/tmp}/rimbaud-kit-$$"
DEST="$STAGE/rimbaud"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

mkdir -p "$DEST" "$(dirname "$OUT")"

log "Staging kit in $DEST (no secrets)"

rsync -a \
  --exclude '.git/' \
  --exclude '**/.git/' \
  --exclude '**/node_modules/' \
  --exclude '**/__pycache__/' \
  --exclude '**/*.pyc' \
  --exclude '**/*.so' \
  --exclude 'logs/' \
  --exclude 'data/' \
  --exclude 'certs/' \
  --exclude 'gateway-files/' \
  --exclude '/dist/' \
  --exclude '.env' \
  --exclude '.compose.env' \
  --exclude 'conf/matrix.db' \
  --exclude 'conf/*.lock' \
  --exclude 'conf/hl_ip_budget.json' \
  --exclude 'conf/hl_ip_budget.json.lock' \
  --exclude 'conf/brief_token.txt' \
  --exclude 'conf/grok_brief.md' \
  --exclude 'conf/condor_setup.json' \
  --exclude 'conf/vpn/' \
  --exclude 'conf/hl_wallets/' \
  --exclude 'conf/connectors/hyperliquid_perpetual.yml' \
  --exclude 'conf/connectors/hyperliquid_perpetual_testnet.yml' \
  --exclude 'conf-*/connectors/hyperliquid_perpetual.yml' \
  --exclude 'test/' \
  --exclude 'docs/' \
  --exclude 'doku/' \
  --exclude '**/*.tmp' \
  --exclude 'website/' \
  --exclude 'grok_bot/' \
  --exclude 'c++/' \
  --exclude 'brain_c++/' \
  --exclude 'prompts/' \
  --exclude 'manual_config/' \
  --exclude 'setup/' \
  --exclude '/hummingbot/' \
  --exclude '**/.DS_Store' \
  "$ROOT"/ "$DEST"/ || {
  rc=$?
  [ "$rc" -eq 24 ] || die "rsync failed (exit $rc)"
  warn "rsync: some live tmp files vanished (ok)"
}

# Wire UI (built). rsync of dashboard/wire already copied dist if present.
if [ -d "$ROOT/dashboard/wire/dist" ]; then
  mkdir -p "$DEST/dashboard/wire/dist"
  rsync -a "$ROOT/dashboard/wire/dist/" "$DEST/dashboard/wire/dist/"
fi

# Only the Python overlays compose bind-mounts (image has the rest).
while read -r f; do
  [ -n "$f" ] || continue
  [ -f "$ROOT/$f" ] || die "overlay missing on source: $f"
  mkdir -p "$DEST/$(dirname "$f")"
  cp -a "$ROOT/$f" "$DEST/$f"
done < <(overlay_files)

# Drop live desk state so the other box does not inherit wallets / bias / clips.
find "$DEST/conf" -maxdepth 1 -type f \( \
    -name '*_status.json' -o -name '*_trades.json' -o -name '*_bias.json' \
    -o -name 'hl_copy_*.json' -o -name 'wire_runtime.json' -o -name 'wire_graph.json' \
    -o -name 'wire_home.json' -o -name 'wire_health.json' \
    -o -name 'force_*.json' -o -name 'force_*.txt' -o -name 'bot_sl.json' \
    -o -name 'onlyup_moon.json' -o -name 'dman_tune.json' \
    -o -name 'condor_setup.json' -o -name 'condor_gauge.json' -o -name 'condor_feed.json' \
    -o -name 'pmm_knob_live.json' -o -name 'koordinator_cmd.json' \
    -o -name 'cpp_usb_job.json' -o -name 'flask_writes.json' \
    -o -name 'site.json' \
  \) -delete 2>/dev/null || true

rm -f \
  "$DEST/conf/bot_accounts.json" \
  "$DEST/conf/connectors/hyperliquid_perpetual.yml" \
  "$DEST/conf/connectors/hyperliquid_perpetual_testnet.yml"
find "$DEST" -path '*/connectors/hyperliquid_perpetual.yml' -delete 2>/dev/null || true

# Do not ship this machine's Copy wallet default.
if [ -f "$DEST/docker-compose.yml" ]; then
  sed -i 's/HL_COPY_WALLET=\${HL_COPY_WALLET:-[^}]*}/HL_COPY_WALLET=${HL_COPY_WALLET:-}/g' \
    "$DEST/docker-compose.yml"
fi

chmod +x "$DEST/install_new/"*.sh "$DEST/rimbaud_vpn/"* 2>/dev/null || true
cp -a "$INSTALL_DIR/bootstrap.sh" "$DEST/install.sh"
chmod +x "$DEST/install.sh" "$DEST/install_new/bootstrap.sh"
[ -x "$DEST/install.sh" ] || die "pack missed root install.sh"
[ -x "$DEST/install_new/make-usb.sh" ] || die "pack missed make-usb.sh"
cp -a "$INSTALL_DIR/INSTALL.txt" "$DEST/INSTALL.txt"
[ -x "$DEST/rimbaud_vpn/rimbaud-vpn" ] || die "pack missed rimbaud_vpn/rimbaud-vpn"
[ -f "$DEST/rimbaud_vpn/spring-proxy.py" ] || die "pack missed spring-proxy.py"
[ -f "$DEST/freiquell_server_c++/src/ctl.cpp" ] || die "pack missed springctl source"

if [ ! -d "$DEST/dashboard/wire/dist" ] || [ -z "$(ls -A "$DEST/dashboard/wire/dist" 2>/dev/null || true)" ]; then
  warn "dashboard/wire/dist is empty — Wire web UI will be blank until rebuilt"
fi

if [ "$WITH_IMAGE" -eq 1 ]; then
  mkdir -p "$DEST/install_new/images"
  log "docker save hummingbot/hummingbot:latest (several GB)"
  as_root docker save -o "$DEST/install_new/images/hummingbot-latest.tar" hummingbot/hummingbot:latest
fi

# up.sh looks for .tar — if we gzipped, write a loader note. Prefer uncompressed for docker load.
# (skip image by default)

log "Writing $OUT"
tar -C "$STAGE" -czf "$OUT" rimbaud
BYTES="$(wc -c < "$OUT" | tr -d ' ')"
log "Kit $(du -h "$OUT" | awk '{print $1}')  ($BYTES bytes)"
echo
echo "Copy to the other machine:"
echo "  scp $OUT user@mint:/tmp/"
echo "  ssh user@mint"
echo "  # one command — unpacks to /opt, then checkboxes:"
echo "  sudo bash -c 'mkdir -p /tmp/rb && tar -C /tmp/rb -xzf /tmp/$(basename "$OUT") && /tmp/rb/rimbaud/install.sh'"
