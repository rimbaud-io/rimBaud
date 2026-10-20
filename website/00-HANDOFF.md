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

# Handoff — design the rimBaud website

You are a design/build agent. The operator will paste this folder into grok.com. Your job is a **public marketing + product website** for **rimBaud**.

## Product in one sentence

**rimBaud** is a native operator desk for a fleet of Hyperliquid perpetual hunters (Hummingbot containers). Spring (C++ daemon) is the shared brain. The matrix desk and a Flask LAN site are the two faces. Hunters trade; the desk does not invent fills.

## You must

- Brand as **rimBaud** (never Freiquell on the public site).
- Keep the tone: operator, precise, dark, no hype APY, no “AI will 100x you”.
- Cover: fleet of hunters, unique subaccounts, BIAS without flattening, Ableton knobs, chart overlays, presets per hunter, IP-token awareness, copy-trading mute vs close.
- Make Safety a first-class page: never CLOSE/OPEN unless the operator uses the explicit Close / 100% path.
- Use hunter names from `HUNTERS.md` exactly.

## You must not

- Invent backtests, returns, AUM, or token tickers for a rimBaud coin (there is no rimBaud token).
- Show real wallet addresses, API keys, or Tailscale hostnames.
- Claim the desk is a broker or a hosted exchange.
- Say Flask is gone. Flask `:5001` still serves HTML; Spring `:5004` is the shared API brain.
- Promise that every hunter “always rebids after close”. They do not. See `HUNTERS.md`.

## Suggested sitemap

1. Home — what it is, who it is for (one operator, many bots, one IP).
2. Product — desk + chart + knobs + presets.
3. Hunters — eight bots, one card each.
4. How it runs — Docker hunters, Spring, Flask, X11 desk.
5. Safety — BIAS, mute, flatten guards, IP budget.
6. Contact / private — placeholder only (no live endpoints).

## Visual cues already in the desk

- Background: near-black navy (`#0a1a12` / `#06120c` class of greens).
- Accent gold, cyan, rose for loss, green for win.
- Wordmark style: `rimBaud  //  MATRIX` (small caps slash, not a bubble logo).
- Knobs: Ableton-like rotaries, min/max in tiny type, gold fill when pressed.

Read the other markdown files in this folder before drawing a single layout.
