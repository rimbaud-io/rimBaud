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

# Site brief (for the design agent)

## Goal

A public site that makes a Hyperliquid operator say: “this is the desk I would actually run,” not “this is a landing page for a token.”

## Pages

1. **Home** — `rimBaud  //  MATRIX`. One sentence. Three proofs: unique subaccounts, BIAS without flatten, knobs + chart on Spring.
2. **Product** — desk screenshot slots (use gold/navy frames; real screenshots can be added later). Knobs, presets, chart dropdown.
3. **Hunters** — eight cards from `HUNTERS.md`. Do not collapse them into “AI strategies.”
4. **How it runs** — diagram from `ARCHITECTURE.md`.
5. **Safety** — the eight rules, as a checklist, not a legal wall of text.
6. **Private / contact** — empty form or “by invitation.” No live IP, no wallet.

## Copy constraints

- Name: **rimBaud**
- Slash motif: `//`
- No fake metrics
- No “now in beta on Product Hunt”
- English, short
- Optional German subtitle only if you add a language toggle; default EN

## Assets to generate (design agent)

- Wordmark `rimBaud` (gold on near-black)
- Simple `// MATRIX` lockup
- 8 hunter marks (Copy, PMM, Helliot, SuperTrend, Shorty, Dman, Only Up, DMan Maker) in the desk palette
- OG image 1200×630
- Favicon 32 / 180

Do not generate photos of real people as “the team.”

## Tech for the designed site (suggestion, not required)

Static HTML/CSS, no backend, no wallet connect, no live ticker that pretends to be the desk. The real desk is local.

## Deliverable

A folder of HTML/CSS/SVG the operator can host anywhere. Keep this `website/` markdown as the source of truth; do not contradict `HUNTERS.md` or `SAFETY.md`.
