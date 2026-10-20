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

# hunters

These scripts run inside Hummingbot. Hummingbot's own source is not in this tree. The stock Hummingbot examples are not here either.

| Script | Hunter |
| --- | --- |
| `hl_copy_trader.py` | HL Copy |
| `pmm_simple_trader.py` | PMM Simple |
| `helliot_trader.py` | Helliot |
| `surfn_trader.py` | Surfn |
| `shortn_trader.py` | Shortn |
| `dman_trader.py` | DMan |
| `onlyup_trader.py` | Only Up |
| `dman_maker_v2_trader.py` | DMan Maker |

The other files are shared pieces: leverage, risk skim, the matrix sqlite writer, the copy digest, and the key tool. `docker-compose.v2-sleep.yml` is the stopped V2 menu. Wallets and connector files are not here.
