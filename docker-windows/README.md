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

# rimBaud on Windows

Docker Desktop runs the Hummingbot engine, the eight hunters, the browser desk, and Spring. The native matrix, chart, meta, and Archy stay on the Linux install.

This folder is meant to be used from a clone of the published rimBaud tree. The paths point at `apps/`, `hunters/`, and `third_party/` next to it.

## What you need

- 64-bit Windows with Docker Desktop, using the Linux container engine.
- A password you choose for Hummingbot. It is not included here.
- Your own exchange keys. Add them in the Hummingbot prompt on first start. This folder has no wallet.

## Start

From the repository root:

```bat
cd docker-windows
copy env.example .env
```

Edit `.env` and set `CONFIG_PASSWORD` to a password you will remember.

The start asks you to read the risk notice. The box starts empty. Hunters stay down until you tick it. The acceptance is saved on this computer and is not sent anywhere. Then:

```bat
up.bat
```

On Linux, from this same folder:

```bash
./up.sh
```

Open the desk at [http://localhost:5001](http://localhost:5001). Spring answers on port 5004.

The first time a hunter starts, Hummingbot asks you to confirm that password and to connect the exchange. Attach to a hunter if the prompt is not in the compose log:

```bat
docker compose run --rm hummingbot-hl
```

After the connector exists, `up.bat` or `./up.sh` brings the stack back. A saved acceptance of the same notice is reused. Your password, keys, and conf stay in the Docker volume named `conf`. They are not written into this folder. The acceptance file stays in your user config, outside this folder.

## What starts

| Container | Role |
| --- | --- |
| `prepare` | Checks the saved risk notice, then creates an empty conf and the starter script files, once. |
| `spring` | Brain on port 5004. Built from `apps/spring`. |
| `desk` | Browser desk on port 5001. Built from `apps/matrix/web`. |
| `hummingbot-hl` | Copy hunter. Follows nobody until you add addresses in the conf volume, file `scripts/hl_copy_trader.yml`. |
| `hummingbot-bn-swing` | Swing hunter. |
| `hummingbot-helliot` | Helliot hunter. |
| `hummingbot-surfn` | Surfn hunter. |
| `hummingbot-shortn` | Shortn hunter. |
| `hummingbot-dman` | Dman hunter. |
| `hummingbot-onlyup` | Onlyup hunter. |
| `hummingbot-dman-maker` | Dman Maker hunter. |

Hunters use the public image `hummingbot/hummingbot:latest`. The rimBaud scripts are mounted from `hunters/`.

Stop the stack with `docker compose down`. That keeps the `conf` volume. `docker compose down -v` deletes the volume, including the keys stored there.
