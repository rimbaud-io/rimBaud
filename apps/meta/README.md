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

# meta

The mothership. It lists the matrices it can reach and can push a build. It does not close a clip and it does not move funds.

| File | What it is |
| --- | --- |
| `src/main.cpp` | Console viewer, `rimbaud-meta`. |
| `src/desk.cpp` | X11 window, `rimbaud-meta-desk`. |
| `src/backup.cpp` | Backup tool, `rimbaud-backup`. |

The HTTP client is `apps/matrix/desk/src/http.cpp`. `make` compiles it in from there.
