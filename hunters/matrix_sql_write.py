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

"""Spring writes sqlite (live). JSON stays a fallback dump. Never close/open HL."""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import matrix_store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--job", required=True)
    args = ap.parse_args()
    db = os.path.abspath(args.db)
    if os.path.basename(db) != "matrix.db":
        print("db must be matrix.db", file=sys.stderr)
        return 2
    matrix_store.init(db)
    with open(args.job, "r") as f:
        job = json.load(f) or {}
    op = str(job.get("op") or "")
    bot = str(job.get("bot_id") or "")
    if op == "bias":
        matrix_store.bias_set_map(bot, job.get("mapping") or {})
        return 0
    if op == "kv":
        values = job.get("values") if isinstance(job.get("values"), dict) else {}
        for k, v in values.items():
            if str(k) in ("updated", "source"):
                continue
            matrix_store.kv_set(bot, str(k), v, "spring")
        return 0
    print("unknown op", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
