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

# Point this machine at the rimBaud RustDesk server.
# Does not set a password, does not copy wallets, does not CLOSE/OPEN.
#
#   sudo ./install_new/rustdesk.sh
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

if [ "$(id -u)" -ne 0 ]; then
  exec sudo -E "$0" "$@"
fi

CONF="$ROOT/conf/rustdesk.json"
[ -f "$CONF" ] || die "missing $CONF"

read_conf() {
  python3 - "$CONF" <<'PY'
import json, sys
j = json.load(open(sys.argv[1]))
ident = str(j.get("id_server") or "")
relay = str(j.get("relay_server") or "")
key = str(j.get("key") or "")
ver = str(j.get("deb_version") or "1.4.5")
host, _, port = ident.partition(":")
if not host or not port.isdigit() or not relay or not key:
    sys.exit(2)
if any(c in key for c in "\n\r'\""):
    sys.exit(3)
print(host)
print(port)
print(relay)
print(key)
print(ver)
PY
}

mapfile -t RD < <(read_conf) || die "conf/rustdesk.json needs id_server, relay_server, and key"
[ "${#RD[@]}" -ge 5 ] || die "conf/rustdesk.json is incomplete"
RD_HOST="${RD[0]}"
RD_PORT="${RD[1]}"
RD_RELAY="${RD[2]}"
RD_KEY="${RD[3]}"
RD_VER="${RD[4]}"

log "RustDesk server ${RD_HOST}:${RD_PORT}  relay ${RD_RELAY}"

if [ "$(uname -m)" != "x86_64" ]; then
  warn "RustDesk deb is built for x86_64 (this box is $(uname -m))"
  exit 1
fi

if [ ! -x /usr/bin/rustdesk ]; then
  deb="/tmp/rimbaud-rustdesk-${RD_VER}-x86_64.deb"
  url="https://github.com/rustdesk/rustdesk/releases/download/${RD_VER}/rustdesk-${RD_VER}-x86_64.deb"
  log "install RustDesk ${RD_VER}"
  as_root curl -fsSL -o "$deb" "$url" || die "download failed: $url"
  as_root dpkg -i "$deb" || as_root apt-get install -y -f
  [ -x /usr/bin/rustdesk ] || die "rustdesk package did not install"
else
  log "RustDesk already installed"
fi

write_client() {
  local home="$1" user="$2"
  [ -n "$home" ] && [ -d "$home" ] || return 0
  local dir="$home/.config/rustdesk"
  local toml="$dir/RustDesk2.toml"
  as_root mkdir -p "$dir"
  as_root python3 - "$toml" "$RD_HOST" "$RD_PORT" "$RD_RELAY" "$RD_KEY" <<'PY'
import sys
path, host, port, relay, key = sys.argv[1:]
ident = "%s:%s" % (host, port)
try:
    text = open(path, errors="replace").read()
except OSError:
    text = ""
if ident in text and ("relay-server" in text) and (key in text):
    sys.exit(0)
lines = text.splitlines()
out = []
seen = set()

def put(k, v, bucket):
    raw = "%s = '%s'" % (k, v)
    if k in seen:
        return
    seen.add(k)
    bucket.append(raw)

top = []
opts = []
in_opts = False
for line in lines:
    s = line.strip()
    if s == "[options]":
        in_opts = True
        continue
    if s.startswith("[") and s.endswith("]") and s != "[options]":
        in_opts = False
        continue
    if not s or s.startswith("#"):
        continue
    if "=" not in s:
        continue
    k = s.split("=", 1)[0].strip()
    if k in ("rendezvous_server", "relay-server", "custom-rendezvous-server", "key"):
        continue
    (opts if in_opts else top).append(line.rstrip())
put("rendezvous_server", ident, top)
put("custom-rendezvous-server", ident, opts)
put("relay-server", relay, opts)
put("key", key, opts)
body = "\n".join(top + ["", "[options]"] + opts) + "\n"
open(path, "w").write(body)
PY
  as_root chown -R "$user:$user" "$dir" 2>/dev/null || true
  as_root chmod 700 "$dir" || true
  as_root chmod 600 "$toml" || true
}

homes=""
add_home() {
  local user="$1" home="$2"
  [ -n "$user" ] && [ -n "$home" ] && [ -d "$home" ] || return 0
  case " $homes " in
    *" $home "*) return 0 ;;
  esac
  homes="$homes $home"
  write_client "$home" "$user"
}
add_home root /root
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  add_home "$SUDO_USER" "$(getent passwd "$SUDO_USER" | cut -d: -f6)"
fi
if id forklifter >/dev/null 2>&1; then
  add_home forklifter "$(getent passwd forklifter | cut -d: -f6)"
fi

if as_root systemctl is-active --quiet rustdesk; then
  log "RustDesk service already running"
else
  as_root systemctl enable --now rustdesk
  log "RustDesk service started"
fi
