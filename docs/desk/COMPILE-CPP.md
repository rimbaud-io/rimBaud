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

# Compile the C++ programs

Compile from the **live** tree, not from `doku/copy/`. The copy has the same sources **with extra comments**; changing or building the copy does not update production binaries.

Needs: `g++` (C++17), `libx11-dev`, `libssl-dev` (chart only), `libpthread`. No CMake, no Qt.

```bash
sudo apt-get install -y g++ libx11-dev libssl-dev
cd .
```

---

## Production binaries (run the matrix)

### 1. Desk + chart — `freiquell_c++/`

```bash
make -C freiquell_c++
# → rimbaud-desk    (symlinks freiquell-desk)
# → rimbaud-chart   (symlinks freiquell-chart)
```

| Binary | Sources | Link |
|--------|---------|------|
| `rimbaud-desk` | `src/main.cpp` `src/http.cpp` `src/api.cpp` `src/ui.cpp` | `-lX11 -lpthread` |
| `rimbaud-chart` | `src/chart.cpp` `src/http.cpp` `src/https.cpp` | `-lX11 -lpthread -lssl -lcrypto` |

Headers: `api.hpp` `ui.hpp` `http.hpp` `https.hpp`. JSON: `third_party/nlohmann/json.hpp` (do not edit).

Run:

```bash
./freiquell_c++/rimbaud-desk              # Spring :5004
./freiquell_c++/rimbaud-desk --headless   # GET /api/home and exit
./freiquell_c++/rimbaud-chart
```

### 2. Spring + springctl — `freiquell_server_c++/`

```bash
make -C freiquell_server_c++
# → spring          (symlinks freiquell-server, rimbaud-server)
# → springctl
```

| Binary | Sources | Link |
|--------|---------|------|
| `spring` | `src/main.cpp` `src/server.cpp` `src/brain.cpp` | `-lpthread` |
| `springctl` | `src/ctl.cpp` | `-lX11 -lpthread` |

Headers: `brain.hpp` `jsonio.hpp`.

Run:

```bash
./freiquell_server_c++/spring --debug --conf ./conf --port 5004
./freiquell_server_c++/springctl
# always-on:
./freiquell_server_c++/daemon/install.sh
```

`--debug` is mandatory. Port **5001** is refused (Flask).

### 3. Meta — `rimbaud_meta_c++/`

```bash
make -C rimbaud_meta_c++
# → rimbaud-meta         (console)
# → rimbaud-meta-desk    (X11)
```

| Binary | Sources | Link |
|--------|---------|------|
| `rimbaud-meta` | `src/main.cpp` `../freiquell_c++/src/http.cpp` | `-lpthread` |
| `rimbaud-meta-desk` | `src/desk.cpp` `../freiquell_c++/src/http.cpp` | `-lX11 -lpthread` |

Include path uses `freiquell_c++/third_party` and `freiquell_c++/src`.

### 4. Archy — `rimbaud-archy/`

```bash
make -C rimbaud-archy
# → rimbaud-archy
```

| Binary | Sources | Link |
|--------|---------|------|
| `rimbaud-archy` | `src/main.cpp` `src/desk.cpp` | `-lX11 -lpthread` |

CLI flags (`--bump-g`, `--status`, `--record-deploy`) exec `archy_db.py`. The X11 window is operator LEVEL UP only.

---

## Legacy (snapshot only — do not ARM on the live desk)

```bash
make -C brain_c++          # freiquell-brain   port 5003
make -C c++                # server + matrix
make -C c++/server         # freiquell-server
make -C c++/matrix         # freiquell-matrix
```

These early trees can write hunter JSON without current Close/100% guards.

---

## Commented copies (this folder)

Every compile source under `doku/copy/` has a file banner and function comments:

```
doku/copy/freiquell_c++/src/{main,http,https,api,ui,chart}.{cpp,hpp}
doku/copy/freiquell_server_c++/src/{main,server,brain,ctl,jsonio}.{cpp,hpp}
doku/copy/rimbaud_meta_c++/src/{main,desk}.cpp
doku/copy/rimbaud-archy/src/{main,desk}.cpp
doku/copy/brain_c++/src/…
doku/copy/c++/matrix/src/…
doku/copy/c++/server/src/…
```

`third_party/nlohmann/json.hpp` is upstream — left untouched.

If you rebuild from the copy for study:

```bash
cd doku/copy/freiquell_c++ && make
# binaries stay inside doku/copy — they are not the live desk
```

---

## After a live rebuild

Restart the process you replaced (desk / Spring / meta). Hunters do not need a rebuild for C++ UI changes. Archy `--bump-g` records the edit; Meta PUSH ships code to remote.
