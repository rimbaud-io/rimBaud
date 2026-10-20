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

# Applications — what we built to run the matrix

Everything below is **ours** (rimBaud / Freiquell / hunters) on top of the Hummingbot Docker image. Upstream Hummingbot itself is not rewritten; we bind-mount a few connector files.

Ports on the hub:

| Port | Application |
|------|-------------|
| **5004** | Spring C++ brain (live matrix) |
| **5001** | Flask `dashboard/app.py` leftover HTML / some Wire compile |
| **5002** | Optional local Grok brief |
| **22** | SSH (live-push). VPN mesh `10.66.80.0/24` is control plane only |

---

## Front (operator, this machine)

### `rimbaud-desk` — C++ matrix window

- **Where:** `freiquell_c++/` → `src/main.cpp` + `http.cpp` + `api.cpp` + `ui.cpp`
- **Binary:** `freiquell_c++/rimbaud-desk` (symlink `freiquell-desk`)
- **Role:** Native X11 desk. GET snapshots from Spring `:5004`, draw Home / Wire / Condor / Hunters / Funds / Strategy / System.
- **Does:** BIAS, knobs, Close, 100%, Wire — via Spring POST when WRITE MODE is on. Refuses Flask `:5001`.
- **Does not:** Talk to Hyperliquid REST itself. Hunters execute.

### `rimbaud-chart` — candle window

- **Where:** `freiquell_c++/src/chart.cpp` + `http.cpp` + `https.cpp`
- **Role:** Per-coin candles (Binance + Hyperliquid), overlay TP / ARM / trail / WIN from Spring live knobs.
- **Libs:** X11 + OpenSSL (`-lssl -lcrypto`).

### `spring` — C++ brain

- **Where:** `freiquell_server_c++/` → `src/main.cpp` + `server.cpp` + `brain.cpp`
- **Binary:** `spring` (symlinks `freiquell-server`, `rimbaud-server`)
- **Role:** HTTP API on `127.0.0.1:5004`. Reads `conf/` JSON + sqlite. In WRITE MODE queues SQL (`conf/.spring_sql_apply.py` / `scripts/matrix_sql_write.py`) and hunter files. CLOSE/OPEN only for operator Close / 100%.
- **Daemon:** `freiquell_server_c++/daemon/install.sh` → systemd unit from `spring.service.in`.

### `springctl` — Spring panel

- **Where:** `freiquell_server_c++/src/ctl.cpp`
- **Role:** QjackCtl-style window: start/stop/restart Spring, DAEMON vs COMMAND, IP-load bar (HL 1200 weight), VPN checkbox (does **not** reroute hunter traffic).

### `rimbaud-meta` / `rimbaud-meta-desk` — mothership

- **Where:** `rimbaud_meta_c++/src/main.cpp` (console) and `src/desk.cpp` (X11). Shares `freiquell_c++/src/http.cpp`.
- **Role:** HOME lists connected matrices (hub + remote…). DEPLOY: live-push, USB kit, Mint ISO, with CMD/OK/FAIL log overlay. iPhone gateway on/off. GET only toward clips. Never CLOSE/OPEN.

### `rimbaud-archy` — version desk

- **Where:** `rimbaud-archy/src/main.cpp` + `desk.cpp` + `archy_db.py`
- **Role:** Operator **LEVEL UP** 0.1 → 0.2 in the window. Grok **`--bump-g`** adds `g1`, `g2`… Ledger: `conf/archy.db` + `conf/archy.json`. `--record-deploy` after live-push.

---

## Hunters (Docker, Hyperliquid)

Image: `hummingbot/hummingbot:latest`. `network_mode: host`. One **unique subaccount** each. Compose file: `docker-compose.yml`. Production start list: `install_new/services.list`.

| Container | Script | What it does |
|-----------|--------|----------------|
| `hummingbot-hl` | `scripts/hl_copy_trader.py` | Copy: mirror up to 3 leader wallets. Mute = no new copies; open mirrors stay. |
| `hummingbot-bn-swing` | `scripts/pmm_simple_trader.py` | PMM Simple on BTC/ETH/LTC/TAO. Dip bids unless Open-on-BIAS. Trail main exit. |
| `hummingbot-helliot` | `scripts/helliot_trader.py` | Elliott-wave directional. |
| `hummingbot-surfn` | `scripts/surfn_trader.py` | Official SuperTrend V1 (3m / 20 / 4). ST reverse must not flatten. |
| `hummingbot-shortn` | `scripts/shortn_trader.py` | Short-the-bounce (BBP). |
| `hummingbot-dman` | `scripts/dman_trader.py` | DMan V3 DCA / trail. |
| `hummingbot-onlyup` | `scripts/onlyup_trader.py` | Long-only SuperTrend + optional moon bag. |
| `hummingbot-dman-maker` | `scripts/dman_maker_v2_trader.py` | Market leftover on LONG/SHORT when flat. 100% same-side **scales** (does not skip). |

Shared helpers used by hunters:

- `scripts/hl_ip_budget.py` — 1200 weight/min per IP
- `scripts/risk_park.py` — park / leftover USD
- `scripts/bot_sl_override.py` — SL file
- `scripts/ctrl_override.py` — live knobs
- `scripts/matrix_store.py` / `matrix_sql_write.py` — sqlite

Per-hunter connector dirs (so wallets never mix): `conf-swing/`, `conf-helliot/`, `conf-surfn/`, `conf-shortn/`, `conf-dman/`, `conf-onlyup/`, `conf-dman-maker/`. Copy uses `conf/connectors`.

**Bind-mounted Hummingbot overlays** (our patches, not the whole tree):

- `hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_derivative.py`
- `hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_api_order_book_data_source.py`
- `hummingbot/data_feed/candles_feed/candles_base.py`
- `hummingbot/core/data_type/order_book_tracker.py`
- `hummingbot/strategy/strategy_v2_base.py`

### Not started in production

| Container | Script | Why off |
|-----------|--------|---------|
| `hummingbot` / `hummingbot-sol` | `agenc_sol_trader.py`, `sol_micro_trader.py` | SOL / Gateway path |
| `hl-testnet-copy` | `dashboard/testnet_copy_trader.py` | testnet |
| `hl-hip3-copy` | `dashboard/mainnet_hip3_trader.py` | HIP-3 |
| `gateway` | Hummingbot Gateway | DEX / SOL |
| V2 sleepers | `conf-v2-sleep/` | created stopped; max 2 via Wire |

---

## Desk helpers (Docker)

| Container | Code | Role |
|-----------|------|------|
| `dashboard` | `dashboard/app.py` | Flask leftover :5001 — HTML desk, some Wire compile |
| `hummingbot-version` | `version/app.py` | Timeshift / version page |
| `hummingbot-audit` | `scripts/hl_trader_audit.py` | Read-only Copy leader health |
| `condor` | `condor/gauge.py` | Condor tape / gauge JSON for the desk |

---

## Roll-out / mothership tools

| App / script | Role |
|--------------|------|
| `install_new/install.sh` | Full install on a Mint box: deps, dirs, hunters, Spring, desk, autostart |
| `install_new/pack.sh` | Tarball **without** secrets / VPN keys |
| `install_new/live-push.sh` | SSH code update to a joined matrix (remote). Restarts Spring + containers, not clips |
| `install_new/make-usb.sh` + `make-usb-gui.sh` | FAT32 USB kit + JOIN.conf, CMD/OK/FAIL log |
| `install_new/mint-image/*` | Remaster official Mint 22.3 ISO (look, preseed, first-boot, `.deb`) |
| `install_new/vpn.sh` | WireGuard control plane `init` / `add` / `join` |
| `install_new/REMOTE-SSH.sh` | One-shot LAN SSH for remote |
| `rimbaud_vpn/spring-proxy.py` | GET-only Spring proxy over VPN |
| `HL_wallet_creator/` | New HL wallet / subaccount helper (run on the **new** box) |
| `iphone/` | PWA + `gateway.py` (optional; Meta can stop it) |

Older kit `install/` is Flask-era; do not use it for new boxes (`install_new` replaced it).

---

## Legacy C++ (in the snapshot, **do not ARM on live**)

| Tree | Binary | Note |
|------|--------|------|
| `brain_c++/` | `freiquell-brain` | Early Flask stand-in, port 5003. Can write hunter JSON. |
| `c++/server/` | `freiquell-server` | Same family as brain_c++. |
| `c++/matrix/` | `freiquell-matrix` | First X11 desk. Replaced by `freiquell_c++`. |

Keep them in the copy for history. Production front is `freiquell_c++` + `freiquell_server_c++` only.

---

## Other small apps

| Path | Role |
|------|------|
| `website/` | Public-safe product pages (no hosts, no keys) |
| `grok_bot/` | Optional Telegram / bias bot (**secrets not in this copy**) |
| `setup/install-conda.sh` | Conda env for Hummingbot host tools |
| `prompts/` | Opening prompts |
| `controllers/` | Hummingbot V2 controllers we ship (DMan V3, SuperTrend, Bollinger, …) |
