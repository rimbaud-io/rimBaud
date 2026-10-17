<!--
Copyright 2026 rimbaud.io

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

# Checklist — rimBaud Mint appliance

## Before Build

- [ ] `config.json` sha256 matches kernel.org `sha256sum.txt` for current Mint Cinnamon LTS
- [ ] `"apt_repo"` empty (local file://) **or** a real HTTPS URL
- [ ] ~20 G free under `dist/mint-image/`
- [ ] Host can sudo (Meta uses pkexec)
- [ ] This tree has no wallets in the kit (`pack.sh` excludes them)

## Build

- [ ] `sudo ./install_new/mint-image/build-iso.sh`  (or Meta → Mint ISO → Build ISO)
- [ ] Official ISO SHA256: OK
- [ ] `.deb` built, `Packages` in `/var/lib/rimbaud/apt`
- [ ] Overlay copied (wallpaper, gtk, greeter, GRUB_DISTRIBUTOR=rimBaud)
- [ ] Hybrid ISO written, `*.iso.sha256` present
- [ ] Volume label is `RIMBAUD`, not `Linux Mint`

## USB / Ventoy / mintstick

- [ ] Stick ≥ 8 G
- [ ] `write-usb.sh --dd /dev/sdX` **or** copy ISO onto Ventoy **or** mintstick
- [ ] Not the running system disk

## Live test (USB or VM)

- [ ] BIOS boot: isolinux / GRUB shows **Start rimBaud live**
- [ ] UEFI boot: same
- [ ] Live desktop: dark green desk look, top panel, `rimBaud` wallpaper/wordmark
- [ ] Greeter: greeter.png + rimBaud logo, not the Mint leaf
- [ ] GRUB: `rimBaud` distributor + dark splash
- [ ] Official `apt-cache policy linux-image-generic` still points at Ubuntu/Mint
- [ ] `apt-cache policy rimbaud` → `file:/var/lib/rimbaud/apt`

## Unattended install

- [ ] Menu **Install rimBaud (unattended)** (or `automatic-ubiquity file=/cdrom/preseed/rimbaud.seed`)
- [ ] Locale de_DE, user `forklifter`, LVM atomic
- [ ] First boot: password expired, `install.sh` ran, ssh on
- [ ] `/opt/rimbaud` present, Meta autostart
- [ ] `os-release` still Linux Mint (not a fork name)
- [ ] GRUB distributor `rimBaud`

## After install (operator)

- [ ] Unique wallets via `/opt/rimbaud/install_new/setup.sh` — never clone hub keys
- [ ] Unique VPN peer (`vpn.sh add`) — never copy JOIN.conf to two boxes
- [ ] Do not CLOSE/OPEN live HL from this image
