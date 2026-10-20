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

# Remaster official Linux Mint Cinnamon LTS into an internal rimBaud appliance ISO.
# Hybrid BIOS+UEFI. Not a distro fork — do not brand as Linux Mint.
# Does not CLOSE/OPEN Hyperliquid. Does not copy wallets.
#
#   sudo ./install_new/mint-image/build-iso.sh
#   sudo ./install_new/mint-image/build-iso.sh --status
#   ./install_new/mint-image/build-iso.sh --cubic   # print Cubic GUI steps
#
# Output: dist/mint-image/rimbaud-mint-*-cinnamon-amd64.iso + .sha256 + STATUS.json
set -euo pipefail
# Line-buffer logs so Meta's MINT ISO overlay updates live (pipe is not a tty).
if command -v stdbuf >/dev/null 2>&1 && [ -z "${RIMBAUD_STDBUF:-}" ]; then
  export RIMBAUD_STDBUF=1
  exec stdbuf -oL -eL "$0" "$@"
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../_common.sh
. "$HERE/../_common.sh"

CFG="$HERE/config.json"
CACHE="$ROOT/dist/mint-image/cache"
WORK="$ROOT/dist/mint-image/work"
OUTDIR="$ROOT/dist/mint-image"
STATUS="$OUTDIR/STATUS.json"

cfg() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$CFG" "$1"
}

usage() { sed -n '2,14p' "$0" | sed 's/^# \?//'; }

write_status() {
  python3 - "$STATUS" "$@" <<'PY'
import json, os, sys, time
path = sys.argv[1]
phase = sys.argv[2] if len(sys.argv) > 2 else "idle"
msg = sys.argv[3] if len(sys.argv) > 3 else ""
pct = None
label_de = ""
if len(sys.argv) > 4 and str(sys.argv[4]).strip() != "":
    try:
        pct = int(float(sys.argv[4]))
    except Exception:
        pct = None
if len(sys.argv) > 5:
    label_de = sys.argv[5]
extra = {}
if os.path.isfile(path):
    try:
        extra = json.load(open(path))
    except Exception:
        extra = {}
if not isinstance(extra, dict):
    extra = {}
if pct is None:
    try:
        pct = int(extra.get("pct") or 0)
    except Exception:
        pct = 0
pct = max(0, min(100, pct))
if not label_de:
    label_de = str(extra.get("label_de") or "")
extra.update({
    "ok": phase in ("done", "idle"),
    "phase": phase,
    "msg": msg,
    "pct": pct,
    "label_de": label_de,
    "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "closes": False,
})
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(extra, open(path, "w"), indent=2)
print("STATUS", phase, msg, str(pct) + "%", label_de)
PY
}

cmd_status() {
  if [ -f "$STATUS" ]; then
    cat "$STATUS"
  else
    printf '{"ok":false,"phase":"none","msg":"no ISO built yet","closes":false}\n'
  fi
}

cmd_cubic() {
  cat <<EOF
Cubic (optional GUI remaster) — same result as build-iso.sh

  sudo apt-get install -y cubic squashfs-tools xorriso
  cubic

1. Project dir:  $OUTDIR/cubic
2. Original ISO: $CACHE/$(cfg iso_name)
   SHA256:       $(cfg sha256)
3. In the chroot terminal:
     mkdir -p /var/lib/rimbaud/apt /etc/rimbaud
     # copy the .deb from the host (Cubic file copy) then:
     dpkg -i /var/lib/rimbaud/apt/rimbaud_*.deb || apt-get -f install -y
     rsync -a /tmp/rimbaud-overlay/ /
     touch /etc/rimbaud/in-iso /etc/rimbaud/firstboot-pending
     glib-compile-schemas /usr/share/glib-2.0/schemas
     systemctl enable ssh rimbaud-firstboot.service
4. Preseed: copy $HERE/preseed/rimbaud.seed → /preseed/rimbaud.seed on the ISO
5. Generate ISO (Cubic produces hybrid BIOS+UEFI).
6. Do not set the volume label or GRUB title to "Linux Mint".
   Use volume $(cfg volume_label). This is an internal appliance.

penguins-eggs alternative (already-installed box → new ISO):
  sudo apt-get install -y penguins-eggs
  sudo eggs produce --unsecure --basename rimbaud --fullname rimBaud
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then usage; exit 0; fi
if [ "${1:-}" = "--status" ]; then cmd_status; exit 0; fi
if [ "${1:-}" = "--cubic" ]; then cmd_cubic; exit 0; fi

[ "$(id -u)" -eq 0 ] || die "need root: sudo $0   (or Meta DEPLOY → Build ISO)"
trap 'write_status fail "build failed at line $LINENO"' ERR
die() {
  write_status fail "$*" 2>/dev/null || true
  printf 'FAIL %s\n' "$*" >&2
  printf '\033[0;31mxx\033[0m %s\n' "$*" >&2
  exit 1
}
# Meta overlay: CMD cyan / OK green (log() in _common.sh stays for humans).
say_cmd() { printf 'CMD  %s\n' "$*"; }
say_ok()  { printf 'OK   %s\n' "$*"; }

ISO_NAME="$(cfg iso_name)"
ISO_URL="$(cfg iso_url)"
SHA256="$(cfg sha256)"
SHA_URL="$(cfg sha256_url)"
VOL="$(cfg volume_label)"
VOL="${VOL:-RIMBAUD}"
PKG="$(cfg package)"
PKG="${PKG:-rimbaud}"
APT_REPO="$(cfg apt_repo)"

need_host() {
  local miss=()
  for c in unsquashfs mksquashfs xorriso rsync wget sha256sum dpkg-deb python3 gzip; do
    command -v "$c" >/dev/null 2>&1 || miss+=("$c")
  done
  if [ "${#miss[@]}" -gt 0 ]; then
    log "installing host tools: ${miss[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y squashfs-tools xorriso rsync wget ca-certificates dpkg-dev gzip
  fi
  need_cmd unsquashfs
  need_cmd mksquashfs
  need_cmd xorriso
  need_cmd rsync
}

mkdir -p "$CACHE" "$OUTDIR"
need_host
write_status running "host tools ok" 4 "Host-Werkzeuge"

AVAIL_KB="$(df -Pk "$OUTDIR" | awk 'NR==2{print $4}')"
# ~12G work + 3G cache + 4G out
NEED_KB=$((20 * 1024 * 1024))
if [ "${AVAIL_KB:-0}" -lt "$NEED_KB" ]; then
  write_status fail "need ~20G free on $(df -P "$OUTDIR" | awk 'NR==2{print $6}')"
  die "need ~20G free under $OUTDIR (have $((AVAIL_KB/1024/1024))G)"
fi

ORIG="$CACHE/$ISO_NAME"
if [ ! -f "$ORIG" ]; then
  log "download $ISO_URL"
  write_status running "downloading official Mint ISO" 6 "ISO wird geladen"
  wget -c -O "$ORIG.part" "$ISO_URL"
  mv "$ORIG.part" "$ORIG"
fi
if [ -z "$SHA256" ] && [ -n "$SHA_URL" ]; then
  log "fetch checksum list"
  wget -q -O "$CACHE/sha256sum.txt" "$SHA_URL"
  SHA256="$(awk -v n="$ISO_NAME" '$2==n || $2=="*"n {print $1; exit}' "$CACHE/sha256sum.txt")"
fi
[ -n "$SHA256" ] || die "no SHA256 in config.json / sha256sum.txt"
log "verify SHA256 $SHA256"
echo "$SHA256  $ORIG" | sha256sum -c -
write_status running "ISO checksum ok" 12 "ISO geprueft"

SQUASH="$WORK/squash"
ISO_TREE="$WORK/iso"
FRESH=0
for a in "$@"; do
  case "$a" in --fresh) FRESH=1 ;; esac
done

cleanup() {
  umount -l "$SQUASH/dev/pts" 2>/dev/null || true
  umount -l "$SQUASH/dev" 2>/dev/null || true
  umount -l "$SQUASH/proc" 2>/dev/null || true
  umount -l "$SQUASH/sys" 2>/dev/null || true
  umount -l "$SQUASH/run" 2>/dev/null || true
}
trap cleanup EXIT

SFS=""
if [ "$FRESH" = 1 ] || [ ! -d "$SQUASH/usr" ] || [ ! -d "$ISO_TREE/casper" ]; then
  rm -rf "$WORK"
  mkdir -p "$ISO_TREE" "$SQUASH"
  log "extract ISO (read-only tree)"
  write_status running "extracting ISO" 16 "ISO wird entpackt"
  xorriso -osirrox on -indev "$ORIG" -extract / "$ISO_TREE" >/dev/null
  for cand in "$ISO_TREE/casper/filesystem.squashfs" "$ISO_TREE/live/filesystem.squashfs"; do
    [ -f "$cand" ] && SFS="$cand" && break
  done
  [ -n "$SFS" ] || die "no casper/filesystem.squashfs in ISO"
  log "unsquashfs (several minutes)"
  write_status running "unsquashfs" 22 "System wird entpackt"
  unsquashfs -f -d "$SQUASH" "$SFS"
else
  say_cmd "resume: reuse unsquashed tree (pass --fresh to start over)"
  log "resume: reuse unsquashed tree (pass --fresh to start over)"
  write_status running "resume unsquashed tree" 20 "System liegt bereit"
  for cand in "$ISO_TREE/casper/filesystem.squashfs" "$ISO_TREE/live/filesystem.squashfs"; do
    [ -f "$cand" ] && SFS="$cand" && break
  done
  [ -n "$SFS" ] || die "no casper/filesystem.squashfs in ISO"
fi

say_cmd "build rimbaud .deb"
log "build rimbaud .deb"
write_status running "building .deb" 42 "rimBaud-Paket wird gebaut"
# Do not capture stdout: pack.sh / log() print banners. The .deb path is the argument.
DEB="$OUTDIR/${PKG}_amd64.deb"
"$HERE/build-deb.sh" "$DEB"
[ -f "$DEB" ] || die "build-deb.sh produced nothing"
say_ok "deb $DEB"

log "overlay look + local apt repo"
python3 "$HERE/make-look.py"
mkdir -p "$SQUASH/var/lib/rimbaud/apt" "$SQUASH/etc/rimbaud" "$SQUASH/preseed"
rsync -a "$HERE/overlay/" "$SQUASH/"
# Source is the overlay launcher — never install a file onto itself (GNU install fails).
install -m 0755 "$HERE/overlay/usr/local/bin/rimbaud-meta-desk" \
  "$SQUASH/usr/local/bin/rimbaud-meta-desk"
cp -a "$DEB" "$SQUASH/var/lib/rimbaud/apt/"
cp -a "$HERE/preseed/rimbaud.seed" "$SQUASH/preseed/rimbaud.seed"
touch "$SQUASH/etc/rimbaud/in-iso"
touch "$SQUASH/etc/rimbaud/firstboot-pending"
if [ -n "$APT_REPO" ]; then
  cat >"$SQUASH/etc/apt/sources.list.d/rimbaud.list" <<EOF
# rimBaud internal packages. Official Mint/Ubuntu lines stay in the stock sources.
deb [trusted=yes] file:/var/lib/rimbaud/apt ./
deb [trusted=yes] $APT_REPO ./
EOF
fi
(
  cd "$SQUASH/var/lib/rimbaud/apt"
  if command -v dpkg-scanpackages >/dev/null 2>&1; then
    dpkg-scanpackages . /dev/null > Packages
  else
    python3 - <<'PY'
import os, subprocess, sys
out = []
for f in sorted(os.listdir('.')):
    if not f.endswith('.deb'):
        continue
    p = subprocess.run(['dpkg-deb', '-f', f], capture_output=True, text=True)
    if p.returncode != 0:
        continue
    fields = p.stdout.strip() + '\n'
    if 'Filename:' not in fields:
        fields += f'Filename: ./{f}\n'
    out.append(fields)
open('Packages', 'w').write('\n'.join(out) + ('\n' if out else ''))
print('Packages', len(out), 'stanzas', file=sys.stderr)
PY
  fi
  gzip -9c Packages > Packages.gz
  [ -s Packages ] || warn "Packages index empty — apt install rimbaud on first boot may need dpkg -i"
)

log "chroot: install packages (Mint/Ubuntu repos stay)"
write_status running "chroot apt + rimbaud .deb" 50 "Pakete werden eingespielt"
mount --bind /dev "$SQUASH/dev"
mount --bind /dev/pts "$SQUASH/dev/pts"
mount -t proc proc "$SQUASH/proc"
mount -t sysfs sys "$SQUASH/sys"
mount -t tmpfs tmpfs "$SQUASH/run"
[ -f /etc/resolv.conf ] && cp -L /etc/resolv.conf "$SQUASH/etc/resolv.conf" || true

chroot "$SQUASH" /bin/bash -s <<'CHROOT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C
apt-get update -y || true
apt-get install -y --no-install-recommends \
  openssh-server wireguard wireguard-tools curl rsync make g++ \
  libx11-6 libx11-dev python3 ca-certificates dpkg-dev || true
dpkg -i /var/lib/rimbaud/apt/rimbaud_*.deb || apt-get -f install -y || true
if [ -x /usr/local/sbin/rimbaud-look.sh ]; then
  /usr/local/sbin/rimbaud-look.sh theme || true
fi
if command -v glib-compile-schemas >/dev/null 2>&1; then
  glib-compile-schemas /usr/share/glib-2.0/schemas || true
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable ssh.service || true
  systemctl enable rimbaud-firstboot.service || true
fi
# Keep official Mint/Ubuntu sources. Only add our file:// (already in overlay).
apt-get clean || true
rm -rf /tmp/* /var/tmp/* /var/cache/apt/archives/*.deb
truncate -s 0 /etc/machine-id || true
rm -f /var/lib/dbus/machine-id
ln -sf /etc/machine-id /var/lib/dbus/machine-id || true
CHROOT
if [ -d "$SQUASH" ]; then
  du -sx --block-size=1 "$SQUASH" | awk '{print $1}' >"${SFS%/*}/filesystem.size" || true
  chroot "$SQUASH" dpkg-query -W --showformat='${Package} ${Version}\n' \
    >"${SFS%/*}/filesystem.manifest" 2>/dev/null || true
fi
cleanup
trap - EXIT

log "rebuild squashfs"
write_status running "mksquashfs" 55 "Abbild wird gepackt"
rm -f "$SFS"
mksquashfs "$SQUASH" "$SFS" -comp xz -b 1M -noappend &
MKPID=$!
while kill -0 "$MKPID" 2>/dev/null; do
  sz="$(stat -c%s "$SFS" 2>/dev/null || echo 0)"
  p=55
  if [ "${sz:-0}" -gt 1000000 ]; then
    p=$((55 + sz * 32 / 3000000000))
    [ "$p" -gt 87 ] && p=87
    [ "$p" -lt 55 ] && p=55
  fi
  write_status running "mksquashfs" "$p" "Abbild wird gepackt" || true
  sleep 2
done
wait "$MKPID"
write_status running "mksquashfs done" 88 "Abbild gepackt"
rm -rf "$SQUASH"

log "preseed + boot menus (live + unattended install)"
mkdir -p "$ISO_TREE/preseed"
cp -a "$HERE/preseed/rimbaud.seed" "$ISO_TREE/preseed/rimbaud.seed"
# Also on the ISO root for some ubiquity paths
cp -a "$HERE/preseed/rimbaud.seed" "$ISO_TREE/rimbaud.seed"

python3 - "$ISO_TREE" <<'PY'
import os, re, sys
root = sys.argv[1]
uuid = ""
for rel in (".disk/casper-uuid-generic", ".disk/live-uuid"):
    p = os.path.join(root, rel)
    if os.path.isfile(p):
        uuid = open(p, encoding="utf-8", errors="replace").read().strip()
        if uuid:
            break
uuid_arg = (" uuid=" + uuid) if uuid else ""
initrd = "initrd.lz"
for name in ("initrd.lz", "initrd", "initrd.gz"):
    if os.path.isfile(os.path.join(root, "casper", name)):
        initrd = name
        break
vmlinuz = "vmlinuz"
for name in ("vmlinuz", "vmlinuz.efi"):
    if os.path.isfile(os.path.join(root, "casper", name)):
        vmlinuz = name
        break
# uuid= is how casper finds the stick. iso-scan alone is empty on a dd'd USB.
common = (
    "file=/cdrom/preseed/rimbaud.seed boot=casper%s "
    "username=mint hostname=mint iso-scan/filename=${iso_path} quiet splash --"
) % uuid_arg
live = (
    "  linux\t/casper/%s  %s\n"
    "  initrd\t/casper/%s\n"
) % (vmlinuz, common, initrd)
install = live.replace("boot=casper", "boot=casper automatic-ubiquity", 1)
# Restore boots the same live session and asks before it dd's a data disk.
# No automatic-ubiquity and no persistent: it must not install or write the stick.
restore = live.replace("boot=casper", "boot=casper rimbaud-restore=1", 1)
restore = restore.replace("hostname=mint", "hostname=rimbaud", 1)
entry_live = 'menuentry "Start rimBaud live" --class rimbaud {\n' + live + "}\n"
entry_inst = 'menuentry "Install rimBaud (unattended)" --class rimbaud {\n' + install + "}\n"
entry_restore = 'menuentry "Restore data disk (latest image)" --class rimbaud {\n' + restore + "}\n"
note = (
    "# Internal rimBaud appliance on official Linux Mint Cinnamon.\n"
    "# Not a Linux Mint fork. Stock live entry kept as fallback.\n"
)

def strip_rimbaud(text):
    text = re.sub(
        r"(?ms)^# Internal rimBaud appliance.*?Stock live entry kept as fallback\.\n+",
        "",
        text,
    )
    text = re.sub(r'(?ms)^menuentry "Start rimBaud live".*?\n\}\n+', "", text)
    text = re.sub(r'(?ms)^menuentry "Install rimBaud \(unattended\)".*?\n\}\n+', "", text)
    text = re.sub(r'(?ms)^menuentry "Restore data disk \(latest image\)".*?\n\}\n+', "", text)
    text = re.sub(r"(?m)^label rimbaud-live\n(?:^[ \t].*\n)*", "", text)
    text = re.sub(r"(?m)^label rimbaud-auto\n(?:^[ \t].*\n)*", "", text)
    text = re.sub(r"(?m)^label rimbaud-restore\n(?:^[ \t].*\n)*", "", text)
    return text

def patch_grub(path):
    if not os.path.isfile(path):
        return
    text = open(path, encoding="utf-8", errors="replace").read()
    own = re.search(r'(?ms)^menuentry "Start rimBaud live".*?\n\}', text)
    has_restore = 'menuentry "Restore data disk (latest image)"' in text
    if own and uuid and ("uuid=" + uuid) in own.group(0) and has_restore:
        print("grub ok", path)
        return
    text = strip_rimbaud(text)
    open(path, "w", encoding="utf-8").write(
        note + entry_live + entry_inst + entry_restore + "\n" + text
    )
    print("patched", path)

for rel in (
    "boot/grub/grub.cfg",
    "boot/grub/loopback.cfg",
    "EFI/BOOT/grub.cfg",
):
    patch_grub(os.path.join(root, rel))

iso_cfg = os.path.join(root, "isolinux/isolinux.cfg")
if os.path.isfile(iso_cfg):
    restore_cmd = common.replace("boot=casper", "boot=casper rimbaud-restore=1", 1)
    restore_cmd = restore_cmd.replace("hostname=mint", "hostname=rimbaud", 1)
    extra = (
        "label rimbaud-live\n"
        "  menu label Start rimBaud live\n"
        "  kernel /casper/%s\n"
        "  append initrd=/casper/%s %s\n"
        "label rimbaud-auto\n"
        "  menu label Install rimBaud (unattended)\n"
        "  kernel /casper/%s\n"
        "  append initrd=/casper/%s %s\n"
        "label rimbaud-restore\n"
        "  menu label Restore data disk (latest image)\n"
        "  kernel /casper/%s\n"
        "  append initrd=/casper/%s %s\n"
        % (vmlinuz, initrd, common.replace("${iso_path}", ""),
           vmlinuz, initrd, common.replace("boot=casper", "boot=casper automatic-ubiquity", 1).replace("${iso_path}", ""),
           vmlinuz, initrd, restore_cmd.replace("${iso_path}", ""))
    )
    text = open(iso_cfg, encoding="utf-8", errors="replace").read()
    own = re.search(r"(?ms)^label rimbaud-live\n(?:^[ \t].*\n)*", text)
    if not (own and uuid and ("uuid=" + uuid) in own.group(0) and "label rimbaud-restore" in text):
        text = strip_rimbaud(text)
        open(iso_cfg, "w", encoding="utf-8").write(extra + "\n" + text)
        print("patched", iso_cfg)
PY

# Live GRUB does not read GRUB_BACKGROUND. That file is the installed system.
# Copy the painted boot screen onto the ISO and point the live menu at it.
GRUB_BG="$HERE/overlay/usr/share/grub/rimbaud.png"
if [ -f "$GRUB_BG" ]; then
  mkdir -p "$ISO_TREE/boot/grub"
  cp -a "$GRUB_BG" "$ISO_TREE/boot/grub/rimbaud.png"
  python3 - "$ISO_TREE" <<'PY'
import os, sys
root = sys.argv[1]
needle = "background_image /boot/grub/rimbaud.png"
block = "insmod png\nbackground_image /boot/grub/rimbaud.png\n"
for rel in ("boot/grub/grub.cfg", "boot/grub/loopback.cfg", "EFI/BOOT/grub.cfg"):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        continue
    text = open(path, encoding="utf-8", errors="replace").read()
    if needle in text:
        print("boot screen already", rel)
        continue
    mark = "terminal_output gfxterm"
    at = text.find(mark)
    if at >= 0:
        at = text.find("\n", at)
        text = text[: at + 1] + block + text[at + 1 :]
    else:
        text = "insmod png\ninsmod gfxterm\nterminal_output gfxterm\n" + block + text
    open(path, "w", encoding="utf-8").write(text)
    print("boot screen", rel)
PY
fi

# Refresh md5sum.txt so casper doesn't warn
if [ -f "$ISO_TREE/md5sum.txt" ]; then
  log "refresh md5sum.txt"
  (
    cd "$ISO_TREE"
    find . -type f -print0 | grep -z -v './isolinux/boot.cat' | \
      xargs -0 md5sum > md5sum.txt || true
  )
fi

STAMP="$(date -u +%Y%m%d)"
OUT="$OUTDIR/rimbaud-mint-$(cfg mint_version)-cinnamon-amd64-${STAMP}.iso"
log "hybrid ISO (BIOS + UEFI) → $OUT"
write_status running "xorriso hybrid ISO" 90 "Hybrid-ISO wird geschrieben"

MBR="$WORK/isohdpfx.bin"
dd if="$ORIG" bs=1 count=432 of="$MBR" status=none

EFI_IMG=""
for cand in boot/grub/efi.img efi.img boot/grub/efi.img EFI/BOOT/efi.img; do
  [ -f "$ISO_TREE/$cand" ] && EFI_IMG="$cand" && break
done

XORRISO_ARGS=(
  -as mkisofs
  -r -J -joliet-long
  -V "$VOL"
  -o "$OUT"
)

if [ -f "$ISO_TREE/isolinux/isolinux.bin" ]; then
  # Official Mint uses partition_offset 16, so partition 1 starts at sector 64.
  # Offset 0 covers the MBR. A normal PC then reports no bootable device.
  XORRISO_ARGS+=(
    -isohybrid-mbr "$MBR"
    -partition_offset 16
    -c isolinux/boot.cat
    -b isolinux/isolinux.bin
    -no-emul-boot -boot-load-size 4 -boot-info-table
  )
  if [ -n "$EFI_IMG" ]; then
    XORRISO_ARGS+=(
      -eltorito-alt-boot
      -e "$EFI_IMG"
      -no-emul-boot
      -isohybrid-gpt-basdat
    )
  fi
  xorriso "${XORRISO_ARGS[@]}" "$ISO_TREE"
elif [ -f "$ISO_TREE/boot/grub/i386-pc/eltorito.img" ]; then
  log "GRUB BIOS eltorito"
  XORRISO_ARGS+=(
    --grub2-mbr "$MBR"
    --protective-msdos-label
    -partition_offset 16
    -b boot/grub/i386-pc/eltorito.img
    -c boot.catalog
    -no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info
  )
  if [ -n "$EFI_IMG" ]; then
    XORRISO_ARGS+=(
      -eltorito-alt-boot
      -e "$EFI_IMG"
      -no-emul-boot
      -isohybrid-gpt-basdat
    )
  fi
  xorriso "${XORRISO_ARGS[@]}" "$ISO_TREE"
else
  log "boot layout unknown — replaying original El Torito + overlay tree"
  xorriso -indev "$ORIG" -outdev "$OUT" \
    -boot_image any replay \
    -volid "$VOL" \
    -update_r "$ISO_TREE" /
fi

log "SHA256"
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" | tee "$OUT.sha256" )
BYTES="$(wc -c < "$OUT" | tr -d ' ')"
SUM="$(awk '{print $1}' "$OUT.sha256")"

python3 - "$STATUS" "$OUT" "$SUM" "$BYTES" "$ORIG" "$SHA256" <<'PY'
import json, os, sys, time
path, iso, sha, nbytes, orig, orig_sha = sys.argv[1:7]
j = {
    "ok": True,
    "phase": "done",
    "msg": "hybrid ISO ready",
    "iso": iso,
    "sha256": sha,
    "bytes": int(nbytes),
    "size_h": "%.1fG" % (int(nbytes) / 1e9),
    "orig_iso": orig,
    "orig_sha256": orig_sha,
    "pct": 100,
    "label_de": "Startmedium fertig",
    "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "closes": False,
    "not_a_mint_fork": True,
}
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(j, open(path, "w"), indent=2)
print(json.dumps(j, indent=2))
PY

say_ok "hybrid ISO $OUT"
log "done $OUT"
log "write USB:  sudo $HERE/write-usb.sh --dd /dev/sdX"
log "or Ventoy:  sudo $HERE/write-usb.sh --copy /mnt/Ventoy"
log "or Cubic:   $0 --cubic"
echo "$OUT"
