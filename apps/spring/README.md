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

# spring

The brain. It listens on port 5004. The matrix, meta, and archy talk to it. It reads and writes hunter files. It does not sign a Hyperliquid order.

| File | What it is |
| --- | --- |
| `src/main.cpp` | Starts the process. |
| `src/server.cpp` | HTTP server. |
| `src/brain.cpp` | The routes: home, bias, close, leverage, pause, wire, sleepers. |
| `src/matrix_sql.cpp` | SQLite for the desk snapshot. |
| `src/hl_key.cpp` | The key lamp. It does not print the secret. |
| `src/ctl.cpp` | `springctl`, the small control window. |
| `daemon/` | The systemd unit. |

`make` writes `spring` and `springctl`.
