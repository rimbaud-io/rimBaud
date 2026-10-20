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

# Build a rimBaud USB stick: ONE command on the new box:
#   sudo bash install.sh
#
# Stick root contains: install.sh, rimbaud-install-*.tar.gz, JOIN.conf, README.txt
# Does not copy wallets. Does not CLOSE/OPEN clips.
#
#   ./install_new/make-usb.sh --list
#   ./install_new/make-usb.sh --payload /tmp/RIMBAUD-USB     # no stick, debug
#   ./install_new/make-usb.sh --verify /tmp/RIMBAUD-USB
#   sudo ./install_new/make-usb.sh --write /dev/sda
set -euo pipefail
if [ ! -t 1 ] && [ -z "${USB_STDBUF:-}" ] && command -v stdbuf >/dev/null 2>&1; then
  export USB_STDBUF=1
  exec stdbuf -oL -eL "$0" "$@"
fi

# --list must not source _common.sh (cd / set -e / extra stdout would break Meta JSON).
cmd_list() {
  PATH="/usr/bin:/bin:${PATH:-}"
  python3 - <<'PY'
import json, os, glob, subprocess
raw = subprocess.check_output(
    ["lsblk", "-Jb", "-o", "NAME,SIZE,TRAN,RM,TYPE,MODEL,MOUNTPOINT,LABEL,FSTYPE,PKNAME"],
    text=True)
j = json.loads(raw)
rows = []
MIN = 2 * 1024 * 1024 * 1024

def kit_on(mnt):
    if not mnt or not os.path.isdir(mnt):
        return False
    if not os.path.isfile(os.path.join(mnt, "install.sh")):
        return False
    return bool(glob.glob(os.path.join(mnt, "rimbaud-install-*.tar.gz")))

def walk(dev, parent_tran="", parent_rm=False):
    name = str(dev.get("name") or "")
    typ = str(dev.get("type") or "")
    tran = str(dev.get("tran") or parent_tran).lower()
    rm = str(dev.get("rm") or "").lower() in ("1", "true") or parent_rm
    size = int(dev.get("size") or 0)
    kids = dev.get("children") or []
    if typ == "disk" and (tran == "usb" or rm) and name.startswith(("sd", "mmcblk")):
        mnt = ""
        fstype = ""
        label = ""
        for c in kids:
            if c.get("mountpoint") and not mnt:
                mnt = c["mountpoint"]
            if c.get("fstype") and not fstype:
                fstype = c["fstype"]
            if c.get("label") and not label:
                label = str(c.get("label") or "")
        if size <= 0:
            note, ready = "no media (empty card reader)", False
        elif size < MIN:
            note, ready = "too small (need >= 2G)", False
        else:
            note, ready = ("kit on stick" if kit_on(mnt) else "ready"), True
        rows.append({
            "name": name,
            "path": "/dev/" + name,
            "size": size,
            "size_h": "%.1fG" % (size / 1e9) if size else "0",
            "model": (dev.get("model") or "USB").strip() or "USB",
            "mount": mnt,
            "fstype": fstype,
            "label": label,
            "ready": ready,
            "note": note,
            "tran": tran or "usb",
        })
    for c in kids:
        walk(c, tran, rm)

for b in j.get("blockdevices") or []:
    walk(b)
print(json.dumps({"ok": True, "disks": rows}, indent=2))
PY
}

case "${1:-}" in
  --list|list) cmd_list; exit 0 ;;
esac

# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

MIN_BYTES=$((2 * 1024 * 1024 * 1024))
LABEL="RIMBAUD"
VPN="$ROOT/rimbaud_vpn/rimbaud-vpn"

# Meta desk log: CMD / OK / FAIL (same as live-push).
say_cmd()  { printf 'CMD  %s\n' "$*"; }
say_ok()   { printf 'OK   %s\n' "$*"; }
say_fail() { printf 'FAIL %s\n' "$*" >&2; }
die()      { say_fail "$*"; exit 1; }
warn()     { printf '!!   %s\n' "$*" >&2; }
log()      { printf '==>  %s\n' "$*"; }

usage() { sed -n '2,16p' "$0" | sed 's/^# \?//'; }

is_system_disk() {
  local dev="$1"
  local src
  src="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
  src="${src%%[0-9]*}"
  [ -n "$src" ] && [ "$dev" = "$src" ]
}

need_wg() {
  if command -v wg >/dev/null 2>&1 && command -v wg-quick >/dev/null 2>&1; then
    return 0
  fi
  log "installing wireguard-tools on this hub (needed to mint JOIN.conf)"
  export DEBIAN_FRONTEND=noninteractive
  as_root apt-get update -y
  as_root apt-get install -y wireguard wireguard-tools
}

pack_kit() {
  local out="$1"
  mkdir -p "$(dirname "$out")"
  if [ -e "$out" ] && [ ! -w "$out" ]; then
    out="/tmp/rimbaud-install-$(date -u +%Y%m%d).tar.gz"
    warn "dist not writable — packing $out"
  fi
  "$INSTALL_DIR/pack.sh" "$out"
  KIT_TAR="$out"
}

write_payload() {
  local dest="$1"
  say_cmd "payload dir $dest"
  mkdir -p "$dest" || die "mkdir $dest"
  say_ok "payload dir $dest"

  local keepjoin=""
  if [ -f "$dest/JOIN.conf" ]; then
    keepjoin="$(mktemp /tmp/rimbaud-join.XXXXXX)"
    cp -a "$dest/JOIN.conf" "$keepjoin"
  fi
  rm -f "$dest"/rimbaud-install-*.tar.gz 2>/dev/null || true

  local kit="/tmp/rimbaud-install-$(date -u +%Y%m%d).tar.gz"
  say_cmd "pack kit (no wallets, no keys, no clips)"
  pack_kit "$kit"
  kit="$KIT_TAR"
  [ -f "$kit" ] || die "pack did not write $kit"
  local ksz
  ksz="$(du -h "$kit" | awk '{print $1}')"
  say_ok "pack kit  $ksz  $(basename "$kit")"

  say_cmd "copy tarball + install.sh + INSTALL.txt"
  cp -a "$kit" "$dest/" || die "copy tarball"
  cp -a "$INSTALL_DIR/bootstrap.sh" "$dest/install.sh" || die "copy install.sh"
  chmod +x "$dest/install.sh" || true
  cp -a "$INSTALL_DIR/INSTALL.txt" "$dest/INSTALL.txt" || die "copy INSTALL.txt"
  say_ok "copy installer files"

  local peer="usb-$(date -u +%Y%m%d%H%M%S)"
  local join=""
  say_cmd "JOIN.conf / VPN peer $peer"
  if [ "$(id -u)" -ne 0 ]; then
    die "need root to mint a new mothership peer — sudo $0 --write <stick>"
  fi
  [ -x "$VPN" ] || die "missing $VPN"
  need_wg || die "wireguard-tools missing on the mothership"
  if [ ! -f "$ROOT/conf/vpn/wg-rimbaud.conf" ]; then
    say_cmd "VPN hub init (10.66.80.0/24 control plane)"
    "$VPN" init
    say_ok "VPN hub init"
  fi
  "$VPN" add "$peer" || die "mothership refused peer $peer — stick not written as a child"
  join="$ROOT/conf/vpn/peers/${peer}.conf"
  [ -f "$join" ] || die "peer file missing: $join"
  grep -Eq 'AllowedIPs\s*=\s*10\.66\.80\.0/24' "$join" || die "JOIN.conf would not stay on the control plane"
  cp -a "$join" "$dest/JOIN.conf"
  chmod 600 "$dest/JOIN.conf" || true
  say_ok "JOIN.conf on stick  $peer  — mothership already expects this child"
  rm -f "$keepjoin" 2>/dev/null || true

  say_cmd "META-HUB.txt + README.txt"
  local hub_ep
  hub_ep="$(cat "$ROOT/conf/vpn/endpoint" 2>/dev/null || true)"
  cat > "$dest/META-HUB.txt" <<EOF
rimBaud meta hub
endpoint  ${hub_ep:-unknown}
vpn       10.66.80.1:5004
control   10.66.80.0/24
peer      ${peer}
EOF

  cat > "$dest/README.txt" <<EOF
rimBaud USB
===========

On the NEW machine, one command:

  sudo bash install.sh

(from this stick — file manager → terminal in this folder, or:)

  sudo bash /media/\$USER/$LABEL/install.sh

Unpacks to /opt/rimbaud, installs Copy-only, joins meta if JOIN.conf
is here, opens 5004 on the VPN iface, then checkboxes.
s = Copy MASTER 0x + API agent key (vault OFF). Transfer Spot→Perps on HL.

Do not paste wallets from another box.
Do not clone JOIN.conf onto two machines.
EOF
  say_ok "hub files"

  say_cmd "verify payload"
  verify_payload "$dest"
  say_ok "verify payload  kit=$(basename "$kit")  join=$([ -f "$dest/JOIN.conf" ] && echo yes || echo no)"
  ls -lh "$dest"
}

verify_payload() {
  local dest="$1"
  [ -f "$dest/install.sh" ] || die "missing install.sh"
  local kit
  kit=$(ls -1 "$dest"/rimbaud-install-*.tar.gz 2>/dev/null | tail -n 1)
  [ -n "$kit" ] && [ -f "$kit" ] || die "missing tarball"
  local listing common
  listing="$(tar -tzf "$kit")"
  case "$listing" in
    *rimbaud/install.sh*) ;;
    *) die "tarball missing rimbaud/install.sh" ;;
  esac
  case "$listing" in
    *install_new/_common.sh*) ;;
    *) die "tarball missing _common.sh" ;;
  esac
  # grep -F: case *'env "$@"') requires that to be the END of the file, so it
  # always failed even when as_root already uses env "$@" (sudo ./install.sh).
  common="$(tar -xOf "$kit" rimbaud/install_new/_common.sh)"
  printf '%s\n' "$common" | grep -Fq 'env "$@"' || die "tarball still has sudo env bug"
  bash -n "$dest/install.sh" || die "install.sh syntax"
  if [ -f "$dest/JOIN.conf" ]; then
    grep -q 'AllowedIPs = 10.66.80.0/24' "$dest/JOIN.conf" || die "JOIN.conf must keep 10.66.80.0/24"
    grep -Eiq 'AllowedIPs\s*=\s*0\.0\.0\.0/0' "$dest/JOIN.conf" && die "JOIN.conf routes 0.0.0.0/0 — refuse"
  else
    warn "no JOIN.conf — new box will install but not auto-VPN"
  fi
  log "verify OK  kit=$(basename "$kit")  join=$([ -f "$dest/JOIN.conf" ] && echo yes || echo no)"
}

pick_mount() {
  local dev="$1"
  local m
  m="$(lsblk -nrpo MOUNTPOINT "$dev" 2>/dev/null | awk 'NF{print; exit}')"
  if [ -n "$m" ] && [ -d "$m" ]; then
    echo "$m"
    return 0
  fi
  local part=""
  for p in "${dev}"1 "${dev}p1"; do
    [ -b "$p" ] && { part="$p"; break; }
  done
  [ -n "$part" ] || return 1
  if command -v udisksctl >/dev/null 2>&1; then
    udisksctl mount -b "$part" >/dev/null 2>&1 || true
  fi
  m="$(lsblk -nrpo MOUNTPOINT "$part" 2>/dev/null | awk 'NF{print; exit}')"
  if [ -n "$m" ] && [ -d "$m" ]; then
    echo "$m"
    return 0
  fi
  local mp="/mnt/rimbaud-usb"
  as_root mkdir -p "$mp"
  if as_root mount "$part" "$mp" 2>/dev/null; then
    echo "$mp"
    return 0
  fi
  return 1
}

format_stick() {
  local dev="$1"
  say_cmd "format $dev FAT32 label=$LABEL"
  as_root umount "${dev}"* 2>/dev/null || true
  as_root parted -s "$dev" mklabel msdos || die "parted mklabel $dev"
  as_root parted -s "$dev" mkpart primary fat32 1MiB 100% || die "parted mkpart $dev"
  as_root partprobe "$dev" || true
  sleep 1
  local part="${dev}1"
  [ -b "$part" ] || part="${dev}p1"
  [ -b "$part" ] || die "no partition after format"
  as_root mkfs.vfat -F 32 -n "$LABEL" "$part" || die "mkfs.vfat $part"
  sleep 0.5
  if command -v udisksctl >/dev/null 2>&1; then
    udisksctl mount -b "$part" >/dev/null || true
  fi
  say_ok "format $dev  $part"
}

cmd_write() {
  local target="${1:-}"
  [ -n "$target" ] || die "usage: $0 --write /dev/sdX"
  say_cmd "USB kit write  $target  (no wallets, no HL close)"
  printf '     [ ] check stick (>= 2G, not system disk)\n'
  printf '     [ ] mount existing FS or format FAT32 RIMBAUD\n'
  printf '     [ ] pack kit (pack.sh, no secrets)\n'
  printf '     [ ] copy install.sh + INSTALL.txt + tarball\n'
  printf '     [ ] JOIN.conf (VPN peer)\n'
  printf '     [ ] META-HUB.txt + README.txt\n'
  printf '     [ ] verify payload\n'
  printf '     [ ] sync\n'

  say_cmd "check stick $target"
  local mp=""
  if [ -d "$target" ]; then
    mp="$target"
    say_ok "check stick  directory $mp"
  elif [ -b "$target" ]; then
    is_system_disk "$target" && die "refusing system disk $target"
    local sz
    sz="$(lsblk -nbdo SIZE "$target" 2>/dev/null || echo 0)"
    [ "${sz:-0}" -ge "$MIN_BYTES" ] || die "$target too small or not ready (size ${sz:-0})"
    say_ok "check stick  $target  $((sz / 1000000000))G"
    say_cmd "mount $target"
    if ! mp="$(pick_mount "$target")"; then
      say_ok "mount skipped — no filesystem, will format"
      format_stick "$target"
      say_cmd "mount after format"
      mp="$(pick_mount "$target")" || die "could not mount after format"
    fi
    say_ok "mount $mp"
  else
    die "not a device or directory: $target"
  fi
  [ -d "$mp" ] || die "mount missing: $mp"

  if [ -w "$mp" ]; then
    write_payload "$mp"
  else
    say_cmd "stick not writable as user — copy via sudo"
    as_root mkdir -p "$mp"
    local tmp="/tmp/RIMBAUD-USB-$$"
    write_payload "$tmp"
    as_root cp -a "$tmp"/. "$mp"/ || die "sudo copy onto $mp"
    rm -rf "$tmp"
    say_cmd "verify on stick $mp"
    verify_payload "$mp"
    say_ok "verify on stick"
  fi
  say_cmd "sync"
  sync
  say_ok "sync"
  say_ok "USB kit ready  $mp  — on the new box:  sudo bash install.sh"
}

cmd="${1:-}"
case "$cmd" in
  --payload|payload)
    shift || true
    write_payload "${1:-/tmp/RIMBAUD-USB}"
    ;;
  --verify|verify)
    shift || true
    verify_payload "${1:-/tmp/RIMBAUD-USB}"
    ;;
  --write|write)
    shift || true
    cmd_write "${1:-}"
    ;;
  -h|--help|help) usage ;;
  *) usage; die "unknown command: ${cmd:-}" ;;
esac
