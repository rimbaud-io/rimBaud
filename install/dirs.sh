#!/usr/bin/env bash
# Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
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

# Runtime folders + empty wallets. Never overwrites an existing .env or keys.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

log "Creating runtime dirs under $ROOT"
mkdir -p \
  "$ROOT/logs" \
  "$ROOT/data" \
  "$ROOT/certs" \
  "$ROOT/conf/connectors" \
  "$ROOT/conf/scripts" \
  "$ROOT/conf/strategies" \
  "$ROOT/conf/controllers" \
  "$ROOT/conf/vpn" \
  "$ROOT/conf-v2-sleep/connectors" \
  "$ROOT/conf-swing/connectors" \
  "$ROOT/conf-helliot/connectors" \
  "$ROOT/conf-surfn/connectors" \
  "$ROOT/conf-shortn/connectors" \
  "$ROOT/conf-dman/connectors" \
  "$ROOT/conf-dman-maker/connectors" \
  "$ROOT/conf-onlyup/connectors" \
  "$ROOT/dashboard/wire/dist"

for d in \
  conf/connectors conf/scripts conf/strategies conf/controllers \
  conf-v2-sleep/connectors \
  conf-swing/connectors conf-helliot/connectors conf-surfn/connectors \
  conf-shortn/connectors conf-dman/connectors conf-dman-maker/connectors \
  conf-onlyup/connectors
do
  [ -f "$ROOT/$d/__init__.py" ] || : > "$ROOT/$d/__init__.py"
done
[ -f "$ROOT/conf-v2-sleep/connectors/.keep" ] || : > "$ROOT/conf-v2-sleep/connectors/.keep"
chmod 700 "$ROOT/conf/vpn" 2>/dev/null || true

if [ -f "$ENV_FILE" ]; then
  log ".env already exists (left untouched)"
else
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "Wrote $ENV_FILE — set CONFIG_PASSWORD before saving keys"
fi

if [ ! -f "$ROOT/conf/bot_accounts.json" ]; then
  cat > "$ROOT/conf/bot_accounts.json" <<'JSON'
{
  "updated": null,
  "source": "install_new",
  "bots": [
    {"id": "hummingbot-hl", "name": "HL Copy", "container": "hummingbot-hl", "yml": "/conf/connectors/hyperliquid_perpetual.yml", "use_vault": false, "address": "", "notes": "Main wallet. Unique on this box."},
    {"id": "hummingbot-bn-swing", "name": "PMM Simple", "container": "hummingbot-bn-swing", "yml": "/conf-swing/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."},
    {"id": "hummingbot-helliot", "name": "Helliot", "container": "hummingbot-helliot", "yml": "/conf-helliot/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."},
    {"id": "hummingbot-surfn", "name": "Surfn", "container": "hummingbot-surfn", "yml": "/conf-surfn/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."},
    {"id": "hummingbot-shortn", "name": "Shorty", "container": "hummingbot-shortn", "yml": "/conf-shortn/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."},
    {"id": "hummingbot-dman", "name": "Dman", "container": "hummingbot-dman", "yml": "/conf-dman/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."},
    {"id": "hummingbot-onlyup", "name": "Only Up", "container": "hummingbot-onlyup", "yml": "/conf-onlyup/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."},
    {"id": "hummingbot-dman-maker", "name": "DMan Maker", "container": "hummingbot-dman-maker", "yml": "/conf-dman-maker/connectors/hyperliquid_perpetual.yml", "use_vault": true, "address": "", "notes": "Unique HL subaccount."}
  ]
}
JSON
  log "Seeded empty conf/bot_accounts.json"
fi

if [ ! -f "$ROOT/conf/desk_bias.json" ]; then
  printf '%s\n' '{"bias":"FLAT","closes":false,"source":"install_new","updated":null}' > "$ROOT/conf/desk_bias.json"
fi

if [ ! -f "$ROOT/conf/site.json" ]; then
  host="$(hostname -s 2>/dev/null || hostname || echo matrix)"
  python3 - "$ROOT/conf/site.json" "$host" <<'PY'
import json, sys, uuid, os
path, host = sys.argv[1], sys.argv[2]
site = {
    "site_id": "site-" + uuid.uuid4().hex[:12],
    "name": host,
    "host": "127.0.0.1",
    "port": 5004,
    "lan": "192.168.0.0/16",
    "role": "matrix",
    "read_only_meta": True,
    "closes": False,
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(site, f, indent=2)
    f.write("\n")
PY
  log "Wrote conf/site.json for this box"
fi

if [ ! -f "$ROOT/conf/meta.json" ]; then
  cat > "$ROOT/conf/meta.json" <<'JSON'
{
  "ok": true,
  "read_only": true,
  "closes": false,
  "positions_untouched": true,
  "lan": "192.168.0.0/16",
  "poll_ms": 2500,
  "sites": [
    {"id": "local", "name": "this matrix", "host": "127.0.0.1", "port": 5004, "role": "local-readonly"}
  ]
}
JSON
fi

TPL="$INSTALL_DIR/templates/hyperliquid_perpetual.yml"
if [ -f "$TPL" ]; then
  for rel in \
    conf/connectors/hyperliquid_perpetual.yml \
    conf-swing/connectors/hyperliquid_perpetual.yml \
    conf-helliot/connectors/hyperliquid_perpetual.yml \
    conf-surfn/connectors/hyperliquid_perpetual.yml \
    conf-shortn/connectors/hyperliquid_perpetual.yml \
    conf-dman/connectors/hyperliquid_perpetual.yml \
    conf-onlyup/connectors/hyperliquid_perpetual.yml \
    conf-dman-maker/connectors/hyperliquid_perpetual.yml
  do
    if [ ! -f "$ROOT/$rel" ]; then
      cp "$TPL" "$ROOT/$rel"
      log "Seeded $rel (placeholders, no keys)"
    fi
  done
fi

log "Dirs ready"
