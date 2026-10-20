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

# Write the rimBaud Mint hybrid ISO to a USB stick.
#   sudo ./install_new/mint-image/write-usb.sh --dd /dev/sdX
#   sudo ./install_new/mint-image/write-usb.sh --copy /mnt/Ventoy
#   ./install_new/mint-image/write-usb.sh --iso     # print latest ISO path
# Destroys the target with --dd. Does not CLOSE/OPEN HL.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../_common.sh
. "$HERE/../_common.sh"

OUTDIR="$ROOT/dist/mint-image"
STATUS="$OUTDIR/STATUS.json"
MIN_GB=8

latest_iso() {
  python3 - "$STATUS" "$OUTDIR" <<'PY'
import json, os, sys, glob
status, outdir = sys.argv[1], sys.argv[2]
if os.path.isfile(status):
    try:
        j = json.load(open(status))
        p = j.get("iso") or ""
        if p and os.path.isfile(p):
            print(p); raise SystemExit
    except SystemExit:
        raise
    except Exception:
        pass
cands = sorted(glob.glob(os.path.join(outdir, "rimbaud-mint-*.iso")), key=os.path.getmtime)
if cands:
    print(cands[-1])
PY
}

# The ISO is a snapshot. Write refuses one older than the installer that gets baked in.
stale_source() {
  local iso="$1" iso_m src m
  iso_m="$(stat -c %Y "$iso")"
  local files=(
    "$ROOT/install_new/install.sh"
    "$ROOT/install_new/bootstrap.sh"
    "$ROOT/install_new/pack.sh"
    "$ROOT/install_new/mint-image/build-iso.sh"
    "$ROOT/install_new/mint-image/build-deb.sh"
    "$ROOT/install_new/mint-image/firstboot/rimbaud-firstboot.sh"
    "$ROOT/install_new/mint-image/preseed/rimbaud.seed"
    "$ROOT/rimbaud_vpn/rimbaud-vpn"
    "$ROOT/rimbaud_vpn/enroll.py"
  )
  for src in "${files[@]}"; do
    [ -f "$src" ] || continue
    m="$(stat -c %Y "$src")"
    if [ "$m" -gt "$iso_m" ]; then
      printf '%s\n' "$src"
      return 0
    fi
  done
  return 1
}

ensure_current_iso() {
  local stale=""
  if [ -n "${ISO:-}" ] && [ -f "$ISO" ]; then
    stale="$(stale_source "$ISO" || true)"
  else
    stale="no ISO yet"
  fi
  [ -n "$stale" ] || return 0
  log "refusing old image — newer than the ISO: $stale"
  log "building a new hybrid ISO, then writing that one"
  [ "$(id -u)" -eq 0 ] || die "need root to build the new ISO: sudo $0 --dd $TARGET"
  "$HERE/build-iso.sh"
  ISO="$(latest_iso || true)"
  [ -n "${ISO:-}" ] && [ -f "$ISO" ] || die "build did not produce an ISO — old image was not written"
  stale="$(stale_source "$ISO" || true)"
  [ -z "$stale" ] || die "built ISO is still older than $stale — old image was not written"
  log "new ISO $(du -h "$ISO" | awk '{print $1}')  $ISO"
}

usage() { sed -n '2,8p' "$0" | sed 's/^# \?//'; }

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "${1:-}" = "--iso" ]; then
  ISO="$(latest_iso || true)"
  [ -n "${ISO:-}" ] || die "no ISO — Meta DEPLOY → Mint ISO → Build, or sudo $HERE/build-iso.sh"
  echo "$ISO"
  exit 0
fi

MODE=""
TARGET=""
ISO_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dd) MODE=dd; TARGET="${2:-}"; shift 2 || true ;;
    --copy) MODE=copy; TARGET="${2:-}"; shift 2 || true ;;
    --iso-file) ISO_ARG="${2:-}"; shift 2 || true ;;
    *) if [ -z "$MODE" ]; then MODE=dd; TARGET="$1"; shift; else die "unexpected $1"; fi ;;
  esac
done

ISO="${ISO_ARG:-$(latest_iso || true)}"
[ -n "$MODE" ] && [ -n "$TARGET" ] || die "usage: $0 --dd /dev/sdX | --copy /mnt/Ventoy"
ensure_current_iso
[ -n "${ISO:-}" ] && [ -f "$ISO" ] || die "no current ISO — build failed, nothing was written"

log "ISO $(du -h "$ISO" | awk '{print $1}')  $ISO"

if [ "$MODE" = "copy" ]; then
  [ -d "$TARGET" ] || die "copy target is not a directory: $TARGET"
  [ -w "$TARGET" ] || die "copy target not writable: $TARGET"
  log "copy ISO onto $TARGET (Ventoy / extra partition)"
  cp -v "$ISO" "$TARGET/"
  cp -v "$ISO.sha256" "$TARGET/" 2>/dev/null || true
  sync
  log "done — boot the stick, pick this ISO in Ventoy"
  exit 0
fi

# --dd : whole-disk hybrid write
[ -b "$TARGET" ] || die "not a block device: $TARGET"
[ "$(id -u)" -eq 0 ] || die "need root: sudo $0 --dd $TARGET"

# Refuse system disk
SRC="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
SRC="${SRC%%[0-9]*}"
[ "$TARGET" != "$SRC" ] || die "refusing to overwrite the running system disk $TARGET"

BYTES="$(blockdev --getsize64 "$TARGET" 2>/dev/null || echo 0)"
NEED=$((MIN_GB * 1000 * 1000 * 1000))
if [ "${BYTES:-0}" -lt "$NEED" ]; then
  die "$TARGET is $((BYTES/1000000000))G — need >= ${MIN_GB}G for the Mint ISO"
fi

# Unmount partitions
for p in "$TARGET"*; do
  [ -e "$p" ] || continue
  umount "$p" 2>/dev/null || true
done

log "dd $ISO → $TARGET  (destroys the stick)"
dd if="$ISO" of="$TARGET" bs=4M status=progress conv=fsync oflag=direct || \
  dd if="$ISO" of="$TARGET" bs=4M status=progress conv=fsync
sync
log "done — boot USB: live = 'Start rimBaud live', install = 'Install rimBaud (unattended)'"
echo "$TARGET"
