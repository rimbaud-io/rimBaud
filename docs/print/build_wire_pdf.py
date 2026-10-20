#!/usr/bin/env python3
# Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os

from build_pdfs import HERE, build_one, styles

def main():
    chat = json.load(open(os.path.join(HERE, "wire_board.json")))
    path = os.path.join(HERE, "04-wire-board-litegraph.pdf")
    build_one(
        path,
        "4 · Wiring board (LiteGraph vs React Flow)",
        chat["q"],
        chat["a"],
        styles(),
    )
    print("wrote", path)

if __name__ == "__main__":
    main()
