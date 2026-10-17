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

# Desk and chart

## Matrix desk (HUNTERS)

Every hunter has Ableton-like knobs:

- Hover: green fill. Press/drag: strong gold fill, white needle.
- Min / max in small type on the dial.
- Double-click the value to type. Enter applies. Esc cancels.
- Arrows: fine step (0.01 on price-% knobs, 1 on ROE / Trail% / Margin).
- **SAVE** + dropdown: named snapshots in sqlite **for that Docker hunter only**. Header: `OnlyUp saves only`. Button shows `OnlyUp · milaas` vs `PMM · milaas`.
- If a named save is selected, SAVE overwrites it. **STD stays locked.**
- PMM knobs are two rows: SCALE / TP / NEXT IN / Margin, then ARM / Trail% / Refresh / SL.

## Chart

- Opened per coin from a hunter (`--bot <container>`).
- **HUNTER** dropdown is painted **on top of the candles** (was hidden under the plot).
- Overlays: IN, **SL {roe}%**, **WIN {roe}%** or price TP, ARM, TRAIL, NEXT IN.
- Drag a dashed line → Spring live knobs → desk knobs. Reverse: desk knobs → live → lines.
- NEXT IN is a **frozen price** after drag (not a % trailing last candle).
- Only Up WIN follows **Clip ROE**, not a leftover 6% price TP.
- Footer shows `SL {n}% ROE` so a 15%→40% stop is readable on 40× BTC (the line barely moves in price).

## Wire

Visual patch bay for which hunter owns which wallet / script. **Save** is cosmetics (layout, group boxes). **Compile** is the real wire change (Flask confirm + integrate). Group boxes persist in the layout JSON.
