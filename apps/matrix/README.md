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

# matrix

The operator screen. It does not send an exchange order by itself. Spring does the write. The hunter does the trade.

## desk

Native X11 window. Build with `make` in `desk/`.

| File | What it is |
| --- | --- |
| `src/main.cpp` | Starts the window. |
| `src/ui.cpp` | Draws Home, Wire, Hunters, Funds, Strategy, and System. |
| `src/api.cpp` | Calls Spring: bias, close, leverage, pause. |
| `src/http.cpp` | Plain HTTP client. Meta uses this file too. |
| `src/https.cpp` | TLS client for chart prices. |
| `src/chart.cpp` | Candle window, built as `rimbaud-chart`. |

`make` writes `rimbaud-desk` and `rimbaud-chart`.

## web

The same desk in the browser. Flask is `web/app.py`. Pages are `web/templates/`. The wire board is the React app in `web/wire/src/`. Build that board with npm inside `web/wire/`. `node_modules` is not in this tree.
