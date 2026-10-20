<!--
Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io

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

# Product

rimBaud is open-source overview software for automatic trading, under the Apache License 2.0.

It is the all-in-one screen: which bots are running, which side they are on, and the knobs that tune them. It is not the strategy engine. Hummingbot hunters are what this desk watches today. Another engine can sit beside them. Each hunter still has its own script, wallet, BIAS file, controller JSON, and status JSON. The overview does not flatten a live clip when a knob moves.

## Two faces, one brain

- **Spring** (C++ daemon, port **5004**) — shared brain for the website leftover APIs and the C++ desk. Reads/writes hunter files via a tight sqlite/json path. Does not CLOSE/OPEN unless the operator uses the explicit Close / 100% commands.
- **Flask** (port **5001**) — LAN HTML for Home, Wire, Copy, hunter pages, Funds, Audit, Backup.
- **rimBaud desk** (`rimbaud-desk`) — native X11 matrix: Home, Wire, Hunters, Funds, JaneStreet, System.
- **rimBaud chart** (`rimbaud-chart`) — per-coin candles with IN / SL / WIN / ARM / TRAIL / NEXT IN overlays tied to a hunter dropdown.

## What the operator actually does

- Set **BIAS** per coin: LONG / SHORT / FLAT. Changing BIAS does **not** close an open clip.
- Tune **Ableton-style knobs** (TP, trail, SL ROE, margin, SuperTrend, copy caps).
- **SAVE** named presets **per hunter** (sqlite). `milaas` on PMM is not `milaas` on Only Up.
- **STD** restores that hunter’s standard pack. STD is locked.
- Drag overlay lines on the chart; Spring live knobs keep desk and chart on the same numbers.
- **Mute** copy leaders (no new copies; mirrors stay).
- **100%** / **Close** are the only one-click size/exit paths. Everything else is gated so a knob cannot flatten a bag.

## What it is not

- Not a hosted product with logins for third parties (unless you later add that — it is not built).
- Not a signal marketplace.
- Not a replacement for Hyperliquid’s own UI.
