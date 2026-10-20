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

# install_new — rimBaud matrix on a second Linux Mint box

Current installer. The older `install/` kit is Flask-only and its Docker
apt line uses `ID=linuxmint` (that repo does not exist).

This kit installs hunters, Spring, C++ desk/meta, **springctl VPN checkbox**,
and **rimbaud-vpn**. It does **not** copy live wallets, keys, logs, or clips.

VPN is a **control plane** (`10.66.80.0/24`). Hunters keep the **new machine’s
public IP** so you get a **new Hyperliquid 1200 weight / 60s** bucket.

## Live push (remote already joined)

From **rimbaud-meta-desk → DEPLOY → PUSH latest to remote**, or:

```bash
./install_new/live-push.sh usb-stick
```

Copies Spring + matrix **code** over VPN/LAN SSH (`forklifter@10.66.80.2`, LAN fallback). Does **not** copy wallets, `conf/`, `.compose.env`, VPN keys, or logs. Restarts Spring and hunter containers; clips stay on Hyperliquid.

If SSH is not set up yet, the script stages `~/share/live-push/` and `APPLY-ON-REMOTE.sh`. Install `~/share/rimbaud-deploy.pub` into remote’s `~/.ssh/authorized_keys` once, then PUSH works from meta.

## On this machine (pack)

```bash
./install_new/pack.sh
# optional offline image (~4 GB):
./install_new/pack.sh --with-image
```

Writes `dist/rimbaud-install-YYYYMMDD.tar.gz`. VPN private keys are excluded.

## On the Mint box

```bash
scp dist/rimbaud-install-*.tar.gz user@MINT:/tmp/
ssh user@MINT
sudo mkdir -p /opt
sudo tar -C /opt -xzf /tmp/rimbaud-install-*.tar.gz
cd /opt/rimbaud
sudo ./install_new/install.sh
```

Then `./install_new/setup.sh` with **new** unique subaccounts.

**Meta desk (this hub):** `rimbaud-meta-desk` — HOME lists connected matrices.
DEPLOY has two new-machine modes:

- **USB kit** — tarball + JOIN.conf for a box that already runs Mint (`make-usb.sh`).
- **Mint ISO** — remaster official Linux Mint Cinnamon 22.3 into a hybrid BIOS+UEFI
  appliance ISO (`mint-image/build-iso.sh`). Live USB + unattended install. Not a
  distro fork. See `install_new/mint-image/README.md` and `CHECKLIST.md`.

Join this box to the meta hub:

```bash
# on the hub
sudo ./install_new/vpn.sh add mint-2
scp conf/vpn/peers/mint-2.conf user@MINT:/tmp/

# on Mint
sudo ./install_new/vpn.sh join /tmp/mint-2.conf
springctl    # tick VPN — gold bar is the NEW HL tokens IP
```

## Commands

| Script | What |
|--------|------|
| `install.sh` | deps + dirs + hunters + Spring + desk + autostart + TUI |
| `pack.sh` | tarball (no secrets, no VPN keys) |
| `deps.sh` | Docker + g++ / X11 / OpenSSL + wireguard-tools |
| `dirs.sh` | folders, empty wallets, `.env` (never overwrite) |
| `up.sh` | pull image, start 8 hunters + dashboard/condor/audit/version |
| `spring.sh` | Spring + springctl, systemd `:5004` |
| `desk.sh` | desk / chart / meta / vpn / springctl links |
| `vpn.sh` | `init` `add` `join` `status` `down` — GET-only proxy |
| `make-usb.sh` | write deploy USB (kit + JOIN.conf, no wallets) |
| `mint-image/build-iso.sh` | remaster official Mint 22.3 Cinnamon → hybrid rimBaud ISO |
| `mint-image/write-usb.sh` | `dd` that ISO to USB, or copy onto Ventoy |
| `down.sh` | stop hunters (conf stays, clips stay) |
| `status.sh` | compose + Spring + VPN iface |
| `setup.sh` | TUI wallets |
| `autostart.sh` | systemd for compose |
| `verify.sh` | required files |

## Safety

- Unique Hyperliquid subaccount per hunter.
- Spring binds **127.0.0.1:5004**. VPN proxy is **GET-only**.
- Tick VPN on springctl does not change hunter routing.
- `AllowedIPs = 0.0.0.0/0` join files are refused.
- V2 sleepers created **stopped**. Max 2 running via WIRE.
- SOL / testnet / HIP-3 / Gateway are not started.
