# Copyright 2026 Laurence Sterne
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

"""Shared dashboard stop-loss override (ROE %). SQLite first, conf/bot_sl.json fallback."""
from __future__ import annotations

import json
import os
import sys

SL_FILE = "/home/hummingbot/conf/bot_sl.json"
_cache = {}
_mtime = None
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

try:
    import matrix_store
except ImportError:
    matrix_store = None


def _load() -> dict:
    global _cache, _mtime
    try:
        mt = os.path.getmtime(SL_FILE)
        if mt != _mtime:
            with open(SL_FILE, "r") as f:
                data = json.load(f) or {}
            _cache = data if isinstance(data, dict) else {}
            _mtime = mt
    except Exception:
        pass
    return _cache if isinstance(_cache, dict) else {}


def override_sl_roe(bot_id: str):
    """Dashboard override if set, else None (bot keeps its own default)."""
    if matrix_store:
        try:
            matrix_store.ensure()
            if matrix_store.has_kv(bot_id, "sl_roe_pct"):
                v = float(matrix_store.kv_get(bot_id, "sl_roe_pct"))
                if 1.0 <= v <= 90.0:
                    return v
        except Exception:
            pass
    row = _load().get(bot_id)
    if not isinstance(row, dict):
        return None
    try:
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except (TypeError, ValueError):
        pass
    return None


def effective_sl_roe(bot_id: str, default: float = 20.0) -> float:
    v = override_sl_roe(bot_id)
    if v is not None:
        return v
    try:
        return float(default)
    except (TypeError, ValueError):
        return 20.0


_HOLD_S = {12: 12 * 3600, 24: 24 * 3600, 68: 68 * 3600}


def effective_time_limit_s(bot_id: str, default: int = 43200) -> int:
    """DMan (and similar) clip hold. Dashboard 12h / 24h / 68h."""
    if matrix_store:
        try:
            matrix_store.ensure()
            kv = matrix_store.kv_map(bot_id)
            if kv.get("time_limit_s") is not None:
                v = int(kv.get("time_limit_s"))
                if v in _HOLD_S.values():
                    return v
            if kv.get("time_limit_h") is not None:
                h = int(kv.get("time_limit_h"))
                if h in _HOLD_S:
                    return _HOLD_S[h]
            if kv.get("hold_h") is not None:
                h = int(kv.get("hold_h"))
                if h in _HOLD_S:
                    return _HOLD_S[h]
        except Exception:
            pass
    row = _load().get(bot_id)
    if isinstance(row, dict):
        try:
            if row.get("time_limit_s") is not None:
                v = int(row.get("time_limit_s"))
                if v in _HOLD_S.values():
                    return v
            if row.get("time_limit_h") is not None:
                h = int(row.get("time_limit_h"))
                if h in _HOLD_S:
                    return _HOLD_S[h]
        except (TypeError, ValueError):
            pass
    try:
        d = int(default)
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    return 43200
