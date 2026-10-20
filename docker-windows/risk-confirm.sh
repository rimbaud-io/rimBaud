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

# Show RISK-NOTICE.txt and store a local acceptance. No network call.
# The box starts empty. A terminal accepts only "I UNDERSTAND" or "ICH VERSTEHE".
# A later start skips the prompt when the saved text and its sha256 still match.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NOTICE="$HERE/RISK-NOTICE.txt"

record_dest() {
  if [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' /var/lib/rimbaud/risk-notice.json
  else
    printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/rimbaud/risk-notice.json"
  fi
}

if [ "${1:-}" = "--path" ]; then
  record_dest
  exit 0
fi

[ -f "$NOTICE" ] || { echo "missing $NOTICE" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required to save the risk notice" >&2; exit 1; }

rimbaud_version() {
  local d="$HERE" v
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    if [ -f "$d/conf/archy.json" ]; then
      v=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8")).get("version") or "")' "$d/conf/archy.json" 2>/dev/null || true)
      if [ -n "$v" ]; then
        printf '%s\n' "$v"
        return 0
      fi
    fi
    if [ -f "$d/VERSION" ]; then
      v=$(head -n 1 "$d/VERSION" | tr -d '[:space:]')
      if [ -n "$v" ]; then
        printf '%s\n' "$v"
        return 0
      fi
    fi
    d=$(dirname "$d")
  done
  printf '%s\n' unknown
}

candidates() {
  printf '%s\n' /var/lib/rimbaud/risk-notice.json
  if [ -n "${SUDO_USER:-}" ]; then
    local home
    home=$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)
    if [ -n "$home" ]; then
      printf '%s\n' "$home/.config/rimbaud/risk-notice.json"
    fi
  fi
  if [ "$(id -u)" -ne 0 ]; then
    record_dest
  fi
}

set +e
RIMBAUD_RISK_MODE=check \
RIMBAUD_NOTICE="$NOTICE" \
RIMBAUD_RISK_CANDIDATES="$(candidates)" \
python3 - <<'PY'
import hashlib, json, os, sys
from pathlib import Path

notice_path = Path(os.environ["RIMBAUD_NOTICE"])
raw = notice_path.read_bytes()
text = raw.decode("utf-8")
digest = hashlib.sha256(raw).hexdigest()

def matches(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and data.get("notice_sha256") == digest and data.get("notice") == text

for line in os.environ.get("RIMBAUD_RISK_CANDIDATES", "").splitlines():
    line = line.strip()
    if line and matches(Path(line)):
        sys.exit(0)
sys.exit(10)
PY
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  if [ -t 1 ]; then
    echo "Risk notice already accepted on this computer."
  fi
  exit 0
fi
if [ "$rc" -ne 10 ]; then
  exit "$rc"
fi

confirm_ui() {
  local err rc choice line
  err=$(mktemp)
  if [ -n "${DISPLAY:-}" ] && command -v zenity >/dev/null 2>&1; then
    set +e
    zenity --text-info \
      --filename="$NOTICE" \
      --title="rimBaud risk notice" \
      --checkbox="I understand. Only money I can afford to lose belongs in the account. / Ich verstehe. Auf das Konto gehört nur Geld, dessen Verlust verkraftbar ist." \
      --width=720 --height=520 \
      --ok-label="Continue" \
      2>"$err"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      rm -f "$err"
      return 0
    fi
    if ! grep -Eqi 'display|authori|unable to init|could not connect|gtk-warning' "$err"; then
      rm -f "$err"
      return 1
    fi
  fi
  rm -f "$err"
  if command -v whiptail >/dev/null 2>&1 && [ -t 0 ] && [ -t 1 ]; then
    set +e
    choice=$(whiptail --title "rimBaud risk notice" --scrolltext --checklist \
      "$(cat "$NOTICE")" 24 78 1 \
      "accept" "I understand / Ich verstehe" OFF \
      3>&1 1>&2 2>&3)
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      return 1
    fi
    case "$choice" in
      *accept*) return 0 ;;
    esac
    return 1
  fi
  if [ -t 0 ] && [ -t 1 ]; then
    cat "$NOTICE"
    printf '\nType I UNDERSTAND or ICH VERSTEHE to continue.\n'
    IFS= read -r line || return 1
    case "$line" in
      "I UNDERSTAND"|"ICH VERSTEHE") return 0 ;;
    esac
    return 1
  fi
  echo "rimBaud risk notice needs a desktop or a terminal. Hunters were not started." >&2
  return 1
}

confirm_ui || exit 1

dest="$(record_dest)"
RIMBAUD_RISK_MODE=write \
RIMBAUD_NOTICE="$NOTICE" \
RIMBAUD_RISK_DEST="$dest" \
RIMBAUD_VERSION="$(rimbaud_version)" \
python3 - <<'PY'
import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

notice_path = Path(os.environ["RIMBAUD_NOTICE"])
raw = notice_path.read_bytes()
text = raw.decode("utf-8")
digest = hashlib.sha256(raw).hexdigest()
dest = Path(os.environ["RIMBAUD_RISK_DEST"])
version = os.environ.get("RIMBAUD_VERSION") or "unknown"
payload = {
    "accepted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "version": version,
    "notice_sha256": digest,
    "notice": text,
}
dest.parent.mkdir(parents=True, exist_ok=True)
if dest.parent.as_posix() == "/var/lib/rimbaud":
    os.chmod(dest.parent, 0o755)
blob = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
tmp = dest.with_suffix(".json.tmp")
tmp.write_text(blob, encoding="utf-8")
os.chmod(tmp, 0o644)
os.replace(tmp, dest)
os.chmod(dest, 0o644)
PY
echo "Saved the risk notice at $dest"
