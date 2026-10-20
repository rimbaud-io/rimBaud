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

# Hunters

Eight Docker hummingbots. Each has its own Hyperliquid subaccount, BIAS (except Copy), knobs, and presets.

## HL Copy (`hummingbot-hl`)

Follows leader wallets. **Mute** = no new copies; open mirrors stay. Copy TRADE LOG tags which leader (T#). Leader **AUDIT** is read-only (daily cron health). Never flatten to “unfollow”.

## PMM Simple (`hummingbot-bn-swing`)

Official-style PMM on BTC / ETH / LTC / TAO.

- **Open on BIAS off (default):** LONG **buys the dip** — rest bids 0.2% and 0.4% below mid. SHORT idle. FLAT idle (does not close).
- **Open on BIAS on:** market-open leftover × Margin % on LONG or SHORT. Skips NEXT IN.
- **after close 100% BIAS:** after TP / TRAIL / SL / TIME (~4s settle), market 100% leftover on current BIAS. Does **not** fire after operator **Close**.
- **NEXT IN:** after WIN/TRAIL, wait for a frozen price (or %), then dip-bid again if Open on BIAS is off.
- Trailing is the main exit. Hard TP is a cap. SL ROE is the emergency cap.
- SCALE x writes TP / ARM / Trail% / NEXT IN / Refresh / SL / Margin with coupling. STD is locked (TP 2.4, ARM 1.4, Trail 22, NEXT 0.18, Refresh 20, SL 40, Margin 60).
- Trail % is **percent of ARM**, not raw price %.

Does **not** always rebuy. FLAT = no rebuy. SHORT with both boxes off = no rebuy. Leftover $0 = cannot open.

## Helliot (`hummingbot-helliot`)

Elliot-wave directional. Knobs: TP ROE, SL ROE, wave2 min/max, wave3 ext, margin.

## Surfn (`hummingbot-surfn`)

Official `directional_trading.supertrend_v1` (3m / length 20 / mult 4). BIAS gates **entries**. SuperTrend reverse must **not** flatten an open clip. Clip ROE / clip size / SL / ST knobs.

## Shorty (`hummingbot-shortn`)

Short-the-bounce. TP/SL ROE, rebuy bounce ROE, short BBP, margin.

## Dman (`hummingbot-dman`)

DCA / trail. Trail ARM / Δ, rebuy BBP, short BBP, hold hours (12/24/68), SL, margin.

## Only Up (`hummingbot-onlyup`)

Long-only trend + optional **moon bag**. Moon on: ignore Clip ROE / SL / SuperTrend; sell the **full clip** when coin +moon% from the arm mark (example: LTC moon +50% from ~$58.11 → **~$87.16**). Clip ROE is a scale-out when moon is off (knob max **2000%** ROE). WIN on the chart is Clip ROE, not a leftover price TP.

## DMan Maker (`hummingbot-dman-maker`)

**Market leftover** when BIAS is LONG or SHORT and flat. Recycles after TP/trail/SL. Not a dip bid. FLAT = idle.

Does **not** always rebuy after close:

- BIAS must be LONG (or 100% for SHORT).
- `afterLossNoRebuy` (yml default on; live ctrl may be off): after SL, no new long until LONG is set again.
- `dontGoCrazy` (yml default on; live ctrl may be off): two +20% ROE wins → wait 30 min or −30% ROE dip from last exit.
- Cooldown 15s + close-settle ~45s.
- Needs leftover USD.

## Shared operator rules

- BIAS change does not flatten.
- Mute / SL / knobs that would **immediately** close are refused (`flatten_guard`).
- 100% leftover follows BIAS; if already in the same side it **adds**, it does not reverse-flatten.
- Restarting the UI does not close clips.
