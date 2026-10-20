<!--
Copyright 2026 Laurence Sterne

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

# rimbaud-vpn — connect matrices, keep Hyperliquid tokens

Each matrix’s hunters use **that machine’s public IP**. Hyperliquid allows
**1200 weight / 60s / IP**. If you route hunters through one VPN exit, you
collapse to **one** bucket.

This tool builds a WireGuard **control plane** (`10.66.80.0/24`) so meta can
GET Spring on each box. **AllowedIPs is never 0.0.0.0/0.** Spring stays on
`127.0.0.1:5004`. A GET-only proxy listens on the VPN address `:5004`.

## Hub (this meta box)

```bash
sudo ./rimbaud_vpn/rimbaud-vpn init
sudo ./rimbaud_vpn/rimbaud-vpn add mint-2
```

Copy `conf/vpn/peers/mint-2.conf` to the other machine.

## Matrix below (Linux Mint)

```bash
sudo /opt/rimbaud/rimbaud_vpn/rimbaud-vpn join /tmp/mint-2.conf
```

That box keeps its own public IP → its own 1200 HL tokens.

## Meta console

```bash
./rimbaud_meta_c++/rimbaud-meta
```

j/k selects a site. Each UP row shows **IP left/1200** for that matrix.

`sudo ./rimbaud_vpn/rimbaud-vpn status` — handshakes + HL public IP of this box.
