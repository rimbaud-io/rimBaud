<!--
Copyright 2026 Laurence Sterne www.rimbaud.io

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# rimBaud Mint Cinnamon appliance ISO

Internal appliance on **official Linux Mint Cinnamon LTS (22.x)**. Not a distro fork — do not brand it as Linux Mint. Official Mint/Ubuntu repos stay on. Own packages live in `file:/var/lib/rimbaud/apt` (and an extra HTTPS line when you set `apt_repo` in `config.json`).

Does **not** copy wallets. Does **not** CLOSE/OPEN Hyperliquid.

## Missing pieces (filled with defaults)

| Item | Value |
|------|--------|
| Software path | this tree → `/opt/rimbaud` |
| Package | `rimbaud` (built by `build-deb.sh` from `pack.sh`) |
| Public APT repo | **none yet** — ISO ships `deb [trusted=yes] file:/var/lib/rimbaud/apt ./`. Set `"apt_repo"` in `config.json` to a `https://…` URL when you publish one. |
| Install user | `forklifter` / password set at install (first boot expires it) |

## Directory tree

```
install_new/mint-image/
  config.json                 Mint 22.3 URL + sha256, package, empty apt_repo
  build-deb.sh                pack.sh → .deb (no secrets)
  build-iso.sh                download ISO, verify, chroot remaster, hybrid xorriso
  write-usb.sh                --dd /dev/sdX   or   --copy /mnt/Ventoy
  gui-build.sh / gui-write.sh Meta pkexec wrappers
  preseed/rimbaud.seed        ubiquity unattended (de_DE, LVM atomic)
  firstboot/                  systemd oneshot → install.sh, chage -d 0
  overlay/
    etc/apt/sources.list.d/rimbaud.list
    etc/skel/.config/gtk-3.0/          dark gtk
    etc/lightdm/slick-greeter.conf
    etc/default/grub.d/99-rimbaud.cfg  GRUB_DISTRIBUTOR=rimBaud
    usr/share/backgrounds/rimbaud/
    usr/share/glib-2.0/schemas/90_rimbaud.gschema.override
    usr/share/applications/rimbaud-meta.desktop
    etc/xdg/autostart/rimbaud-meta.desktop
```

Output: `dist/mint-image/rimbaud-mint-22.3-cinnamon-amd64-YYYYMMDD.iso` + `.sha256` + `STATUS.json`.

## Commands

```bash
# 1–4  (Meta DEPLOY → Mint ISO → Build ISO  does this)
sudo ./install_new/mint-image/build-iso.sh

# Cubic GUI instead of the script
./install_new/mint-image/build-iso.sh --cubic

# 5  USB
sudo ./install_new/mint-image/write-usb.sh --dd /dev/sdX          # destroys stick, >= 8G
sudo ./install_new/mint-image/write-usb.sh --copy /mnt/Ventoy     # keep Ventoy
# mintstick:  mintstick -m iso -s dist/mint-image/rimbaud-mint-*.iso -d /dev/sdX

# status for Meta
./install_new/mint-image/build-iso.sh --status
```

### VM test

```bash
qemu-system-x86_64 -m 4096 -enable-kvm \
  -cdrom dist/mint-image/rimbaud-mint-22.3-cinnamon-amd64-*.iso \
  -boot d
# UEFI:
qemu-system-x86_64 -m 4096 -enable-kvm \
  -bios /usr/share/ovmf/OVMF.fd \
  -cdrom dist/mint-image/rimbaud-mint-22.3-cinnamon-amd64-*.iso
```

Boot menu: **Start rimBaud live** (USB trial) or **Install rimBaud (unattended)**. Stock Mint live stays as fallback. After install: password change on login, then `/opt/rimbaud/install_new/setup.sh` for **unique** wallets.

## Look

Desk palette (`#020804` / `#00ff6a` / `#2a9fd6` / `#f0b90b`):

- Wallpaper + greeter + GRUB from `make-look.py` (wordmark `rimBaud  //  MATRIX`)
- Cinnamon theme **rimBaud** = Mint-Y-Dark clone + panel/menu overrides (top panel)
- GTK 3/4 system CSS, slick-greeter logo, no desktop icons
- First login: `/usr/local/sbin/rimbaud-look.sh user` (autostart)

Rebuild the ISO after look changes. Not a Mint fork — `os-release` stays stock so official repos still work.

## Own APT repo later

```bash
# on the package server
dpkg-scanpackages pool /dev/null | gzip -9c > dists/stable/main/binary-amd64/Packages.gz
```

Then set `"apt_repo"` in `config.json` to that URL and rebuild. `apt upgrade` will then pull `rimbaud` as well as Mint security updates.
