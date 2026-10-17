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

# pettibon

Saves the trade book for the hunter it watches. It does not place an order.

| File | What it is |
| --- | --- |
| `src/main.cpp` | Starts the collector. |
| `src/collect.cpp` | Reads the hunter events. |
| `src/store.cpp` | Writes the SQLite book. |
| `src/dman_setup.cpp` | The DMan Maker setup it records. |
| `daemon/` | Install script. |
| `grafana/` | The boards that read the book. |

The database itself is not in this tree.
