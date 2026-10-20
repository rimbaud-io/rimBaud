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

# doku — documented snapshot of the rimBaud matrix

This folder is a **read-only documentation copy** of the live project.

**Open files under `doku/copy/`, not the live tree.** The live sources at the repo root (`freiquell_c++/src/ui.cpp`, …) have **no** extra comments on purpose. Every function in the copy has a `// name — …` (C++) or `# name — …` (Python) line directly above it, so it is visible as soon as you open the file.

- **Live code** stays at the repo root (`.`). Do not compile or run traders from here.
- **`doku/copy/`** is a snapshot of production folders (source + configs + scripts), **without secrets, wallets, logs, clips, or compiled binaries**.
- Comments exist **only** inside `doku/copy/`. Live `.cpp` / hunters were not changed.

| Doc | What |
|-----|------|
| [APPLICATIONS.md](APPLICATIONS.md) | Every application: desk, Spring, hunters, Meta, Archy, deploy |
| [FILES.md](FILES.md) | Folder-by-folder file map |
| [COMPILE-CPP.md](COMPILE-CPP.md) | How to compile the C++ programs (from the **live** tree) |
| [SNAPSHOT.md](SNAPSHOT.md) | What was copied, what was excluded, how to refresh |
| `copy/` | The snapshot itself |

---

## 1. Run the matrix in front (this machine)

The operator front is **native C++**, not the browser. Hunters stay in Docker. Spring is the brain.

```
 Operator
    |-- rimbaud-desk     (X11 matrix)  ----\
    |-- rimbaud-chart    (candles)         |---- Spring :5004  ---- conf/ + matrix.db
    |-- springctl        (start/stop)  ----/         |
    |-- rimbaud-meta-desk (mothership)               |  Docker
    |-- rimbaud-archy     (version)                  +-- hummingbot-hl          Copy
                                                     +-- hummingbot-bn-swing    PMM Simple
 Flask HTML :5001 leftover (website / compile)       +-- hummingbot-helliot     Helliot
                                                     +-- hummingbot-surfn       Surfn
                                                     +-- hummingbot-shortn      Shorty
                                                     +-- hummingbot-dman        Dman
                                                     +-- hummingbot-onlyup      Only Up
                                                     +-- hummingbot-dman-maker  DMan Maker
                                                     +-- dashboard / version / audit / condor
```

### 1.1 Compile (live tree, not doku)

```bash
cd .
# deps: g++ libx11-dev libssl-dev
make -C freiquell_c++            # rimbaud-desk  rimbaud-chart
make -C freiquell_server_c++     # spring        springctl
make -C rimbaud_meta_c++         # rimbaud-meta  rimbaud-meta-desk
make -C rimbaud-archy            # rimbaud-archy
```

Details, flags, and which `.cpp` files belong to which binary: [COMPILE-CPP.md](COMPILE-CPP.md).

### 1.2 Start Spring (brain)

```bash
cd freiquell_server_c++
./spring --debug --conf ../conf --port 5004
# or the panel:
./springctl
```

- Binds **127.0.0.1:5004**. Refuses Flask port **5001**.
- `--debug` is required to start.
- WRITE MODE (file `/tmp/freiquell-spring-writes`) lets Spring queue hunter commands into sqlite (`conf/matrix.db`) and hunter JSON. CLOSE/OPEN still only happen when the operator hits Close / 100% on the desk.
- Never start the **legacy** brains `brain_c++` or `c++/server` on the live desk — those older trees can write hunter JSON without the current guards.

### 1.3 Start the desk (front)

```bash
cd freiquell_c++
./rimbaud-desk                  # always Spring :5004
./rimbaud-chart                 # candles; talks to Spring + Binance/HL
```

`rimbaud-desk` **refuses port 5001**. Pages: Home, Wire, Condor, Hunters, Funds, Strategy, System. Keys `1`–`7`, `q` / Esc quit.

### 1.4 Start hunters (Docker)

```bash
cd .
# production list (install_new/services.list):
docker compose up -d \
  hummingbot-hl hummingbot-bn-swing hummingbot-helliot hummingbot-surfn \
  hummingbot-shortn hummingbot-dman hummingbot-onlyup hummingbot-dman-maker \
  dashboard hummingbot-version hummingbot-audit condor
```

Not started in production: SOL (`hummingbot`, `hummingbot-sol`), testnet copy, HIP-3 copy, Gateway. V2 sleepers are created **stopped**.

Each hunter needs its **own Hyperliquid subaccount**. One public IP → **1200 weight / 60s** HL budget (`scripts/hl_ip_budget.py`).

### 1.5 Meta + Archy (optional on the hub)

```bash
./rimbaud_meta_c++/rimbaud-meta-desk    # HOME / DEPLOY / HL MASTER
./rimbaud-archy/rimbaud-archy           # LEVEL UP (you) / --bump-g (Grok)
```

Meta is GET-only toward live clips. DEPLOY runs USB kit / Mint ISO / live-push. It never CLOSE/OPEN Hyperliquid.

### 1.6 Safety (live)

- Unique subaccount per hunter / per machine.
- BIAS / mute / reboot do **not** flatten open clips.
- CLOSE/OPEN only from operator Close or 100% (or an explicit “I do not care”).
- Do not mix wallets. Do not brand the Mint appliance as “Linux Mint”.

---

## 2. Roll out to another machine

Two paths from **rimbaud-meta-desk → DEPLOY** (or the scripts under `install_new/`).

### A. USB kit — box already runs Linux Mint

For a second Mint Cinnamon box (example: remote). Writes a FAT32 stick `RIMBAUD` (≥2G) with the install tarball + `JOIN.conf`. **No wallets.**

```bash
./install_new/make-usb.sh          # or Meta → Create USB install media
# on the new box:
sudo tar -C /opt -xzf /media/usb/rimbaud-install-*.tar.gz
cd /opt/rimbaud
sudo ./install_new/install.sh
./install_new/setup.sh             # NEW unique subaccounts
```

Join VPN (control plane `10.66.80.0/24` only — hunters keep the **new machine’s public IP** so they get a new HL 1200 bucket):

```bash
# hub
sudo ./install_new/vpn.sh add mint-2
# new box
sudo ./install_new/vpn.sh join /tmp/mint-2.conf
springctl    # tick VPN
```

Later updates: Meta **PUSH latest** → `install_new/live-push.sh`. Copies code over SSH, restarts Spring + hunter containers, **does not** copy wallets / `conf/` / VPN keys. Clips stay on Hyperliquid.

SSH helper for remote: `install_new/REMOTE-SSH.sh` (LAN `forklifter@192.168.1.20`, key `~/.ssh/id_ed25519_rimbaud_deploy`).

### B. Mint ISO — bare metal / new disk

Remaster **official** Linux Mint Cinnamon 22.3 into a hybrid BIOS+UEFI live+install ISO with rimBaud look + `/opt/rimbaud`. Not a distro fork (`os-release` stays Mint).

```bash
# Meta → DEPLOY → Mint ISO → Build   (do not download the ~3G ISO until Build)
./install_new/mint-image/build-iso.sh
./install_new/mint-image/write-usb.sh   # USB ≥8G
```

Default install user `forklifter`, first-boot password set at install (expires). German locale. See `copy/install_new/mint-image/README.md` and `CHECKLIST.md`.

### First weeks on a new HL account

Bootstrap list (`install_new/services.bootstrap.list`): **Copy + dashboard only** until ~$100k lifetime volume, then unique subaccounts for the other hunters.

---

## 3. This snapshot

`doku/copy/` mirrors production **source** as of the snapshot date. Compiled binaries were stripped so the copy is source + Makefiles. Secrets, `matrix.db`, connector YAML with keys, logs, and certs were **not** copied.

To study C++: open `doku/copy/freiquell_c++/src/` etc. — those files have the extra comments.

To actually compile and run: use the **live** tree at the repo root, as in section 1.
