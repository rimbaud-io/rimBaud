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

# rimBaud iPhone — matrix-looking META on the Home Screen

PWA (Safari → **Share → Add to Home Screen**). Looks like the desk: navy, green, gold, orange.  
Talks **only** to your **static-IP gateway**. Never holds HL keys. **LONG / FLAT / SHORT** = desk BIAS (`closes: false`). No Close button.

## Files

| Path | What |
|------|------|
| `index.html` `css/` `js/` `icons/` `manifest.webmanifest` | The app |
| `gateway.py` `run.sh` | Gateway: serves PWA + `/api/fleet` + `/api/bias` |
| `config.example.json` | Copy to `config.json` on the phone box |
| `nginx.example.conf` | TLS in front of `:8787` |

## What you need (static IP server)

1. A VPS / home box with a **public IPv4** (or domain + Let’s Encrypt).
2. That box must **reach the hub Spring** (`127.0.0.1:5004` if the gateway runs **on the hub**, or WireGuard `10.66.80.1:5004` if the VPS is elsewhere).
3. To see **remote**, the gateway host must also GET `http://10.66.80.2:5004/api/home` (join the same `10.66.80.0/24` control plane; UFW 5004 on `wg-rimbaud`).
4. `GATEWAY_TOKEN` — long random string. Put the same token in the iPhone Settings field.
5. **HTTPS** if you want “Add to Home Screen” to behave as an app (iOS requires HTTPS except localhost).

Do **not** bind Spring itself to `0.0.0.0`. Only this gateway listens on the static IP.

## Run on the hub (LAN test)

```bash
cd phone
GATEWAY_TOKEN='pick-a-long-secret' ./run.sh
```

iPhone on the same Wi-Fi: `http://192.168.1.10:8787/`  
Settings → API base that URL, paste token, Save.  
Safari → Share → **Add to Home Screen**.

## Run on the static-IP box

```bash
# WireGuard to 10.66.80.1 (hub) and 10.66.80.2 (remote)
export HUB_SPRING=http://10.66.80.1:5004
export GATEWAY_TOKEN='…'
python3 gateway.py   # :8787
# Caddy/nginx 443 → 8787
```

iPhone: `https://YOUR.DOMAIN/` → Add to Home Screen.

## Panic buttons

LONG / FLAT / SHORT → `POST /api/bias` → Spring `POST /api/home/desk_bias`.  
Hunters **keep open clips**. After they go flat they follow the new BIAS.  
Telegram `/long` `/short` `/flat` still works the same.

## HL tokens

The app **shows leftover IP per seat**. It does not create a new 1200 bucket. Same house WAN = same IP.
