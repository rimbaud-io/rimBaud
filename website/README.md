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

# rimBaud — website content pack

This folder is **source material for a public website**, not the live trading desk.

Copy the whole `website/` directory to [grok.com](https://grok.com) (or any design agent) and ask them to **design and build the marketing site**. Do not copy `conf/`, wallets, keys, or `brief_token`.

## What to tell the design agent

Paste this:

> Read `00-HANDOFF.md` first, then `SITE-BRIEF.md` and `BRAND.md`. Design a public website for **rimBaud**. Use only facts in this folder. Do not invent exchange listings, token tickers, or performance numbers. Do not show live wallets or API keys. Produce a modern marketing site (Home, Product, Hunters, Desk, Safety, Architecture). Dark, precise, operator-grade — not a meme coin landing page.

## Files

| File | Use |
|---|---|
| `00-HANDOFF.md` | Start here. Mission for the design agent. |
| `SITE-BRIEF.md` | Pages, IA, tone, must / must-not. |
| `BRAND.md` | Name, voice, colors already in the desk. |
| `PRODUCT.md` | What rimBaud is. |
| `ARCHITECTURE.md` | Spring, Flask, desk, Docker hunters. |
| `HUNTERS.md` | The eight hummingbots. |
| `DESK-AND-CHART.md` | Matrix desk, Ableton knobs, chart. |
| `SAFETY.md` | Never flatten, unique subaccounts, IP budget. |
| `FEATURE-INVENTORY.md` | Development inventory (what exists today). |
| `index.html` | Local table of contents. Open in a browser to read. |

Live trading stays in the parent repo. This pack is documentation only.
