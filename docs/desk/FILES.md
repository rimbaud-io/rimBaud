<!--
Copyright 2026 Laurence Sterne

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

# File map — documented copy (`doku/copy/`)

Paths below are relative to `doku/copy/` (same layout as production). C++ compile units are listed again in [COMPILE-CPP.md](COMPILE-CPP.md).

---

## C++ we compile (production)

### `freiquell_c++/` — desk + chart

| File | Role |
|------|------|
| `Makefile` | Builds `rimbaud-desk` and `rimbaud-chart` |
| `src/main.cpp` | Desk entry: flags, refuse :5001, call `run_desk` |
| `src/http.cpp` / `http.hpp` | Tiny HTTP/1.1 client (GET/POST) used by desk, chart, meta |
| `src/https.cpp` / `https.hpp` | TLS client for Binance / HL candles |
| `src/api.cpp` / `api.hpp` | Parse Spring JSON → `DeskSnap`; POST helpers (BIAS, Close, 100%, knobs) |
| `src/ui.cpp` / `ui.hpp` | X11 matrix: pages, Wire canvas, hits, draw loop |
| `src/chart.cpp` | X11 candle window + overlays |
| `third_party/nlohmann/json.hpp` | Upstream JSON (not ours — do not comment/edit) |
| `README.md` | Desk usage |

### `freiquell_server_c++/` — Spring

| File | Role |
|------|------|
| `Makefile` | Builds `spring` + `springctl` |
| `src/main.cpp` | Requires `--debug`, refuse :5001, find `conf/` |
| `src/server.cpp` | Threaded HTTP listener, dispatch to `Brain::handle` |
| `src/brain.cpp` / `brain.hpp` | All `/api/*` routes, WRITE MODE, SQL queue, Close/100% |
| `src/jsonio.hpp` | Read/write JSON files + flock |
| `src/ctl.cpp` | springctl X11 panel |
| `daemon/spring.service.in` | systemd unit |
| `daemon/install.sh` `uninstall.sh` | Install user or system daemon |

### `rimbaud_meta_c++/` — mothership

| File | Role |
|------|------|
| `Makefile` | `rimbaud-meta` + `rimbaud-meta-desk` |
| `src/main.cpp` | Console viewer (GET `/api/home` of each site) |
| `src/desk.cpp` | X11 HOME / DEPLOY / HL MASTER, USB + Mint ISO jobs, PUSH log |

### `rimbaud-archy/` — versions

| File | Role |
|------|------|
| `Makefile` | `rimbaud-archy` |
| `src/main.cpp` | CLI → `archy_db.py`; no-args → desk |
| `src/desk.cpp` | LEVEL UP window |
| `archy_db.py` | sqlite ledger `conf/archy.db` |
| `GROK.md` | Grok bump rules |

---

## Legacy C++ (do not ARM live)

### `brain_c++/`

Early read/write brain (`freiquell-brain`, port 5003). `src/main.cpp` `server.cpp` `brain.cpp` `brain.hpp` `jsonio.hpp`.

### `c++/server/` and `c++/matrix/`

First server + first X11 desk (`freiquell-server`, `freiquell-matrix`). Same shape as Spring/desk, older guards. `c++/Makefile` builds both. `c++/third_party/nlohmann/json.hpp` duplicate of nlohmann.

---

## Hunters and helpers — `scripts/`

### Live hunters

| File | Container |
|------|-----------|
| `hl_copy_trader.py` | hummingbot-hl |
| `pmm_simple_trader.py` | hummingbot-bn-swing |
| `helliot_trader.py` | hummingbot-helliot |
| `surfn_trader.py` | hummingbot-surfn |
| `shortn_trader.py` | hummingbot-shortn |
| `dman_trader.py` | hummingbot-dman |
| `onlyup_trader.py` | hummingbot-onlyup |
| `dman_maker_v2_trader.py` | hummingbot-dman-maker |

### Shared

| File | Role |
|------|------|
| `hl_ip_budget.py` | HL 1200 weight / 60s |
| `risk_park.py` | Park / leftover USD |
| `bot_sl_override.py` | Stop-loss file |
| `ctrl_override.py` | Live knob file |
| `matrix_store.py` | sqlite helpers |
| `matrix_sql_write.py` | Spring SQL apply |
| `copy_digest.py` | Copy trade digest |
| `hl_trader_audit.py` | Leader audit (read-only) |
| `hl_top_traders.py` / `.json` | Leader list helper |
| `ensure_autostart.sh` | systemd compose |
| `watch_copy_trades.sh` | Tail copy log |

### Off / examples (not production front)

`agenc_sol_trader.py`, `sol_micro_trader.py`, `solana_bb_trader.py`, `hl_copy_trader_testnet.py`, `hl_btc_trader.py`, `bn_swing_trader.py` (old swing; PMM replaced it), Hummingbot examples (`simple_pmm.py`, `v2_with_controllers.py`, …).

---

## Docker / compose

| File | Role |
|------|------|
| `docker-compose.yml` | All hunters + dashboard + version + audit + condor + gateway |
| `docker-compose.v2-sleep.yml` | V2 sleeper hunters (created stopped) |
| `Dockerfile` | Upstream-style HB image (we usually pull `hummingbot/hummingbot:latest`) |
| `install_new/services.list` | Production `up -d` list |
| `install_new/services.bootstrap.list` | Copy + dashboard only |

---

## Dashboard leftover — `dashboard/`

| File | Role |
|------|------|
| `app.py` | Flask :5001 |
| `matrix_store.py` | sqlite (duplicate of scripts/) |
| `hl_ip_budget.py` | same budget helper |
| `templates/` | HTML desk |
| `wire/` | Wire compile helpers |
| `Dockerfile` `Dockerfile.testnet` `Dockerfile.hip3` | extra copy bots (off) |
| `testnet_copy_trader.py` `mainnet_hip3_trader.py` | off |

---

## Other Docker apps

| Path | Role |
|------|------|
| `condor/gauge.py` + `Dockerfile` | Condor gauge JSON |
| `audit/Dockerfile` | wraps `hl_trader_audit.py` |
| `version/app.py` `timeshift_lib.py` `index.html` | version / timeshift page |

---

## Config layout — `conf/` and `conf-*`

`conf/` is the shared brain directory Spring reads.

Important **schema / control** files (copied): `wire_registry.json`, `wire_graph.json`, `wire_pack.json`, `meta.json`, `archy.json`, `deploy_hosts.json`, `controller_specs.json`, `controller_presets.json`, `hl_follow_notes.json`, `hl_reserved_coins.json`, `hl_risk_overrides.json`, `hummingbot-*_ctrl.json`, `dman_tune.json`, `onlyup_moon.json`, `v2_sleepers.json`, `iphone_gateway.json`, `live_push_job.json`, `cpp_usb_job.json`.

**Not copied:** `matrix.db*`, connector private keys, VPN keys, live `*_status.json` / `*_trades.json` / `bot_sl.json`.

Per-hunter connector mounts (wallets live only on the machine):

- `conf-swing/` PMM Simple
- `conf-helliot/` Helliot
- `conf-surfn/` Surfn
- `conf-shortn/` Shorty
- `conf-dman/` Dman
- `conf-onlyup/` Only Up
- `conf-dman-maker/` DMan Maker
- `conf-v2-sleep/` sleepers
- Copy uses `conf/connectors`

---

## Hummingbot overlays — `hummingbot/`

Only our bind-mounts, not the full package:

```
hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_derivative.py
hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_api_order_book_data_source.py
hummingbot/data_feed/candles_feed/candles_base.py
hummingbot/core/data_type/order_book_tracker.py
hummingbot/strategy/strategy_v2_base.py
```

---

## Controllers — `controllers/`

V2 controllers we ship: `directional_trading/{dman_v3,supertrend_v1,bollinger_v1,bollinger_v2,bollingrid,macd_bb_v1,ai_livestream}.py`, `market_making/pmm_dynamic.py`.

---

## Install / roll-out — `install_new/`

| File | Role |
|------|------|
| `install.sh` | Full Mint install |
| `pack.sh` | Tarball without secrets |
| `deps.sh` `dirs.sh` `up.sh` `down.sh` `status.sh` `verify.sh` | pieces |
| `spring.sh` `desk.sh` `autostart.sh` | Spring + desk + systemd |
| `vpn.sh` | WireGuard control plane |
| `live-push.sh` | SSH update to a joined matrix |
| `make-usb.sh` `make-usb-gui.sh` | USB kit + CMD/OK/FAIL log |
| `REMOTE-SSH.sh` | remote LAN key |
| `setup.sh` `setup_tui.py` | wallet TUI (new unique accounts) |
| `bootstrap.sh` | Copy-only first weeks |
| `env.example` | env template (no live secrets) |
| `templates/hyperliquid_perpetual.yml` | connector template |
| `mint-image/build-iso.sh` `build-deb.sh` `write-usb.sh` | Mint appliance |
| `mint-image/preseed/rimbaud.seed` | unattended install |
| `mint-image/firstboot/` | first-boot service |
| `mint-image/make-look.py` | wallpaper / greeter / GRUB look |
| `mint-image/config.json` `CHECKLIST.md` `README.md` | kit docs |

Older `install/` is the Flask-era kit (kept for history).

---

## VPN / iPhone / wallets / website

| Path | Role |
|------|------|
| `rimbaud_vpn/spring-proxy.py` | GET-only Spring over VPN |
| `iphone/gateway.py` `index.html` `js/app.js` `css/matrix.css` | optional PWA |
| `HL_wallet_creator/hl_wallet_creator.py` | new HL wallet |
| `website/*.md` `index.html` | public-safe site |
| `grok_bot/` | optional Telegram bot (**no secrets.env**) |
| `setup/install-conda.sh` `environment.yml` | host conda |
| `prompts/opening_prompts.txt` | prompts |

---

## Root odds and ends

`Makefile` `start` `clean` `compile` `uninstall` `setup.py` `pyproject.toml` `Dockerfile` `LICENSE` `README.md` (upstream Hummingbot readme — our ops docs are `doku/` and `install_new/README.md`).
