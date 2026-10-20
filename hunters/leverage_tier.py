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

"""Desk leverage tiers shared by every hunter except HL Copy.

MAX keeps the old exchange-max path. MID is half the cap, LOW is a quarter,
at least 1. The live ctrl key is leverage_tier on that hunter's own bot id.
"""
from __future__ import annotations

_TIERS = ("MAX", "MID", "LOW")


def tier_name(bot_id: str) -> str:
    try:
        from ctrl_override import live
        raw = str(live(bot_id, "leverage_tier", "MAX") or "MAX").strip().upper()
    except Exception:
        return "MAX"
    return raw if raw in _TIERS else "MAX"


def sized(cfg: int, hl: int, use_max: bool, tier: str) -> int:
    """Integer leverage for one coin. MAX keeps the old exchange-max path."""
    try:
        cfg_i = max(1, int(cfg))
    except (TypeError, ValueError):
        cfg_i = 1
    try:
        hl_i = int(hl or 0)
    except (TypeError, ValueError):
        hl_i = 0
    if hl_i < 0:
        hl_i = 0
    cap = hl_i if hl_i > 0 else cfg_i
    name = str(tier or "MAX").strip().upper()
    if name == "LOW":
        return max(1, cap // 4)
    if name == "MID":
        return max(1, cap // 2)
    if use_max and hl_i > 0:
        return hl_i
    if hl_i > 0:
        return max(1, min(cfg_i, hl_i))
    return cfg_i
