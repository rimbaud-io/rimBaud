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

# Safety (must appear on the public site)

These are product facts, not slogans.

1. **Never CLOSE/OPEN** live Hyperliquid positions unless the operator uses the explicit **Close** or **100%** path.
2. **Spring never CLOSE/OPEN** on its own. Same for changing BIAS, mute, SL, knobs that would immediately close, or restarting the UI.
3. **Flatten guard:** if a new SL / TP / Clip ROE would hit **right now**, the desk refuses and says so. Clips stay.
4. **Mute ≠ close.** Copy mute = `unfollowed_traders`. No new copies; mirrors remain.
5. **Unique subaccount** per hunter. The script refuses to trade until Confiig has a wallet that is not shared.
6. **IP tokens:** Hyperliquid cap is **1200 weight / 60 seconds / IP**, not 1200 requests. The desk shows leftover. Copy used to burn `subAccounts` (weight 20) every 5s per leader; that list is cached.
7. **WRITE MODE** in springctl unlocks hunter-file writes only — still not a close switch.
8. Audit worker is **read-only** leader health.

If a design mockup shows a big red “Flatten all” button on the marketing home page, that mockup is wrong.
