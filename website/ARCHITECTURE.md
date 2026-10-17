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

# Architecture (public-safe)

```
 Operator
    |-- rimBaud desk (X11)  ----\
    |-- rimBaud chart           |---- Spring :5004  ---- hunter files / sqlite
    |-- Flask HTML :5001  ------/         |
                                          |  Docker
                                          +-- hummingbot-hl          HL Copy
                                          +-- hummingbot-bn-swing    PMM Simple
                                          +-- hummingbot-helliot     Helliot
                                          +-- hummingbot-surfn       SuperTrend V1
                                          +-- hummingbot-shortn      Shorty
                                          +-- hummingbot-dman        Dman
                                          +-- hummingbot-onlyup      Only Up
                                          +-- hummingbot-dman-maker  DMan Maker
                                          +-- hummingbot-audit       leader audit (read-only)
```

## Rules the website should mention without leaking ops

- **One IP** to Hyperliquid. Copy + candles + eight hunters share a **1200 weight / 60s** IP budget. Expensive calls (`subAccounts` = 20, `meta` = 20, `clearinghouseState` = 2).
- **Unique subaccount per hunter.** Mixing wallets is refused.
- **Controller specs** live in sqlite (`controller_spec`) so every hunter has a fixed knob pack. Live values stay in `kv` + `*_ctrl.json`.
- **Presets** are `(bot_id, name)` in sqlite. Never global.
- **Spring live knobs** (`/api/live/knobs`) are RAM for desk ↔ chart. Persist is `/api/wire/controller` + `bot_sl.json` for stop-loss ROE.
- Compile/Wire integrate still hits Flask `:5001` by design.

## Ports (do not put real Tailscale hosts on the public site)

| Port | Role |
|---|---|
| 5004 | Spring brain |
| 5001 | Flask HTML |
| 5002 | Optional local brief for a read-only Grok summary bot |

## Source layout (internal; optional footnote)

- `freiquell_c++/` — desk + chart (binaries `rimbaud-desk`, `rimbaud-chart`)
- `freiquell_server_c++/` — Spring (`spring`, symlink `rimbaud-server`)
- `scripts/` — hunter Python
- `dashboard/` — Flask
- `conf/` — **private**. Never copy to grok.com.
