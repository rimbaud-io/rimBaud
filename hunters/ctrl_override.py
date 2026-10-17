#!/usr/bin/env python3
# Copyright 2026 rimbaud.io
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

"""Live controller knobs: SQLite first, conf/{bot_id}_ctrl.json fallback."""
from __future__ import annotations

import json
import os
import sys

_CACHE = {}
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

try:
    import matrix_store
except ImportError:
    matrix_store = None


def _path(bot_id: str) -> str:
    return f"/home/hummingbot/conf/{bot_id}_ctrl.json"


def _from_sql(bot_id: str, key: str, default):
    if not matrix_store:
        return None
    try:
        matrix_store.ensure()
        if not matrix_store.has_kv(bot_id, key):
            return None
        return matrix_store.kv_get_typed(bot_id, key, default)
    except Exception:
        return None


def live(bot_id: str, key: str, default):
    sql = _from_sql(bot_id, key, default)
    if sql is not None:
        return sql
    path = _path(bot_id)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default
    pack = _CACHE.get(bot_id)
    if not pack or pack[0] != mtime:
        try:
            with open(path, "r") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        _CACHE[bot_id] = (mtime, data)
        pack = _CACHE[bot_id]
    row = pack[1]
    if key not in row or row[key] is None:
        return default
    try:
        if isinstance(default, bool):
            return bool(row[key])
        if isinstance(default, int) and not isinstance(default, bool):
            return int(float(row[key]))
        if isinstance(default, float):
            return float(row[key])
    except (TypeError, ValueError):
        return default
    return row[key]
