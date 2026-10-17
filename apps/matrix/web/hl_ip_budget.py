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

"""Shared Hyperliquid IP REST token-bucket (rolling 60s).

Official: 1200 aggregated weight / minute / IP.
Weight 2: l2Book, allMids, clearinghouseState, orderStatus,
          spotClearinghouseState, exchangeStatus.
Weight 20: other /info (meta, candleSnapshot, userFills, …).
Weight 1: /exchange actions.

All hummingbot containers + the dashboard append here:
  /home/hummingbot/conf/hl_ip_budget.json  (bots)
  /conf/hl_ip_budget.json                  (dashboard)
Same host file: ./conf/hl_ip_budget.json
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

CAP = 1200
WINDOW_S = 60.0

_WEIGHT2 = {
    "l2book", "allmids", "clearinghousestate", "orderstatus",
    "spotclearinghousestate", "exchangestatus", "openorders",
    "frontendopenorders",
}

_PATHS = (
    os.environ.get("HL_IP_BUDGET_FILE") or "",
    "/home/hummingbot/conf/hl_ip_budget.json",
    "/conf/hl_ip_budget.json",
)


def budget_path() -> str:
    for p in _PATHS:
        if not p:
            continue
        d = os.path.dirname(p)
        if d and os.path.isdir(d):
            return p
    return _PATHS[1]


def weight_for(payload: Optional[dict], path_url: str = "") -> int:
    path = (path_url or "").lower()
    if "exchange" in path and "info" not in path:
        return 1
    kind = str((payload or {}).get("type") or "").replace("_", "").lower()
    if not kind:
        return 2
    if kind in _WEIGHT2:
        return 2
    return 20


def _empty() -> dict:
    return {"cap": CAP, "window_s": WINDOW_S, "events": [], "last_429": None}


def _load(raw: str) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    ev = data.get("events")
    if not isinstance(ev, list):
        data["events"] = []
    return data


def _prune(data: dict, now: float) -> dict:
    cut = now - WINDOW_S
    ev = []
    for item in data.get("events") or []:
        try:
            t, w = float(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if t >= cut and w > 0:
            src = item[2] if len(item) > 2 else ""
            ev.append([round(t, 3), w, str(src)[:16]])
    data["events"] = ev[-2500:]
    data["cap"] = CAP
    data["window_s"] = WINDOW_S
    return data


def record(payload: Optional[dict] = None, src: str = "?", is_429: bool = False,
           path_url: str = "", weight: Optional[int] = None) -> None:
    """Append one REST attempt. Never raises."""
    try:
        now = time.time()
        w = int(weight) if weight is not None else (0 if is_429 else weight_for(payload, path_url))
        path = budget_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            import fcntl  # Linux
        except Exception:
            fcntl = None
        lockp = path + ".lock"
        with open(lockp, "a+", encoding="utf-8") as lk:
            if fcntl:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = _prune(_load(f.read()), now)
                except Exception:
                    data = _empty()
                if w > 0:
                    data["events"].append([round(now, 3), w, str(src)[:16]])
                if is_429:
                    data["last_429"] = round(now, 3)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as out:
                    json.dump(data, out, separators=(",", ":"))
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, path)
            finally:
                if fcntl:
                    fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def snapshot() -> Dict[str, Any]:
    now = time.time()
    try:
        with open(budget_path(), "r", encoding="utf-8") as f:
            data = _prune(_load(f.read()), now)
    except Exception:
        data = _empty()
    used = 0
    oldest = None
    oldest_w = 0
    by_src: Dict[str, int] = {}
    for item in data.get("events") or []:
        try:
            t, w = float(item[0]), int(item[1])
            src = str(item[2]) if len(item) > 2 else "?"
        except (TypeError, ValueError, IndexError):
            continue
        used += w
        by_src[src] = by_src.get(src, 0) + w
        if oldest is None or t < oldest:
            oldest = t
            oldest_w = w
    last_429 = data.get("last_429")
    last_429_s = None
    try:
        if last_429:
            last_429_s = max(0, int(now - float(last_429)))
    except (TypeError, ValueError):
        last_429_s = None
    refill_in_s = 0
    if oldest is not None:
        refill_in_s = max(0, int(round(oldest + WINDOW_S - now)))
    used = min(used, CAP * 2)
    left = max(0, CAP - used)
    pct = min(100.0, (used / CAP) * 100.0) if CAP else 0.0
    return {
        "cap": CAP,
        "window_s": int(WINDOW_S),
        "used": used,
        "left": left,
        "pct": round(pct, 1),
        "calls": len(data.get("events") or []),
        "refill_in_s": refill_in_s,
        "refill_weight": oldest_w,
        "last_429_s": last_429_s,
        "n429": int(data.get("n429") or 0),
        "by_src": by_src,
        "ws_cap": 10,
        "ws_new_per_min": 30,
        "note": "shared public IP · rolling 60s token window · 1200 weight/min",
    }
