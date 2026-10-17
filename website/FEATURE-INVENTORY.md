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

# Feature inventory (what exists today)

Use this so the website does not promise vapor.

## Built

- Spring C++ brain on :5004 (files + sqlite via dashboard `matrix_store`)
- Flask :5001 HTML (Home, Wire, Copy, hunter pages, Funds, Audit, Backup, Version)
- Native desk + chart (X11), branded **rimBaud**
- Eight hunters in Docker, unique subaccounts
- Per-hunter sqlite `controller_spec` packs and `controller_preset` saves
- Ableton knobs on every HUNTERS page
- Chart hunter dropdown + live overlays
- PMM SCALE x, coupling, Open on BIAS, after-close 100% BIAS, NEXT IN wait
- Copy follow mute, TRADE LOG with T#, AUDIT overlay from daily cron `latest.json`
- PMM chart-linked knobs, drag lines, live `/api/live/knobs`
- Group boxes persist on Wire Save (cosmetics); Compile is real wires
- Surfn = SuperTrend V1, BIAS-gated, no reverse flatten
- 100% leftover **adds** same-side if already open
- IP budget file + copy `subAccounts` cache
- Only Up moon bags; Clip ROE to 2000%
- Flatten guards on SL / TP / clip ROE
- Leader audit worker `hummingbot-audit` (read-only)

## Explicitly not built / not claimed

- Hosted multi-user SaaS
- Mobile app
- rimBaud token
- Automatic flatten-on-BIAS
- “Always rebuy after close” for every hunter
- Flask fully replaced (it still serves HTML)
- Public API for third-party desks

## Development note for the site timeline (optional, no dates required)

The desk grew from a Flask overlay into Spring-as-brain, then hunter knobs, then chart sync, then presets, then the rimBaud rename. The public story is: **one operator, one IP, eight isolated hunters, a matrix that refuses to flatten by accident.**
