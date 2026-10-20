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

# Snapshot rules

`doku/copy/` is an rsync of the production repo **for documentation**. It is not a second live tree.

## Included

All of our application folders and the Hummingbot overlays we actually bind-mount:

- C++: `freiquell_c++/`, `freiquell_server_c++/`, `rimbaud_meta_c++/`, `rimbaud-archy/`, legacy `brain_c++/`, `c++/`
- Hunters: `scripts/`, `controllers/`, `docker-compose.yml`, `docker-compose.v2-sleep.yml`
- Per-hunter conf dirs: `conf-dman/`, `conf-dman-maker/`, `conf-helliot/`, `conf-onlyup/`, `conf-shortn/`, `conf-surfn/`, `conf-swing/`, `conf-v2-sleep/`
- Shared `conf/` **without** runtime DBs, lock files, wallets, `*_status.json` / `*_trades.json` / `*_bias.json` where they are live-only (schema JSON such as `wire_registry.json`, `archy.json`, `meta.json` stay)
- Dashboard, version, audit, condor, iphone, website, install + install_new, rimbaud_vpn, HL_wallet_creator, version, setup, prompts, grok_bot (no `secrets.env`)
- Hummingbot overlays only (five files), not the full upstream package

Compiled binaries (`rimbaud-desk`, `spring`, …) were **stripped** so the copy is source + Makefiles.

## Excluded on purpose

| Path | Why |
|------|-----|
| `.git/` `node_modules/` | noise |
| `certs/` `logs/` `data/` | runtime / secrets |
| `matrix.db*` | live sqlite |
| `.compose.env` | secrets |
| `conf/connectors/*.yml` live keys | wallets |
| `conf/vpn/` private keys | WireGuard |
| `gateway-files/` | gateway certs + logs |
| `grok_bot/secrets.env` | Telegram tokens |
| `dist/` | packed tarballs |
| `doku/` itself | avoid recursion |
| live `*_status.json` `*_trades.json` `wire_runtime.json` `bot_sl.json` `bot_accounts.json` | runtime |

## Refresh the copy (does not touch live code)

From the repo root, same excludes as `install_new/pack.sh`, then re-run the C++ comment pass:

```bash
# do not rsync into a running desk
rsync -a --delete \
  --exclude doku --exclude .git --exclude certs --exclude logs --exclude data \
  --exclude dist --exclude '*.db' --exclude '*.db-*' --exclude .compose.env \
  --exclude node_modules --exclude __pycache__ \
  ./ doku/copy/
# then restore comments with doku/tools/annotate_cpp.py
```

Prefer leaving the snapshot as-is unless you are documenting a new release.

## Marker

`copy/DOKU-COPY.txt` states that this tree is a documented copy, not production.
