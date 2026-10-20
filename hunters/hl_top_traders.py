#!/usr/bin/env python3
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

"""
Hyperliquid Top 10 Consistent Profitable Traders
Fetches leaderboard and filters for traders profitable across all-time, 30d, and 7d.
Usage: python hl_top_traders.py
"""

import requests
import json

URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

def fetch_leaderboard():
    print("Fetching Hyperliquid leaderboard...")
    r = requests.get(URL, timeout=30)
    r.raise_for_status()
    return r.json()

def parse_roi(val):
    """Convert ROI value to float percentage."""
    try:
        return float(val) * 100
    except (TypeError, ValueError):
        return None

def parse_pnl(val):
    """Convert PnL value to float USD."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def format_pnl(val):
    if val is None:
        return "N/A"
    if abs(val) >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if abs(val) >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.2f}"

def main():
    data = fetch_leaderboard()

    # The leaderboard may be a list or wrapped in a key
    if isinstance(data, dict):
        traders = data.get("leaderboardRows") or data.get("data") or list(data.values())[0]
    else:
        traders = data

    print(f"Total traders: {len(traders)}")

    consistent = []

    for t in traders:
        # Each entry has windowPerformances: list of [window, {pnl, roi, vlm}]
        # Windows: "allTime", "month", "week", "day"
        perfs = {}
        for item in t.get("windowPerformances", []):
            window = item[0]
            stats = item[1]
            perfs[window] = stats

        alltime = perfs.get("allTime", {})
        month   = perfs.get("month", {})
        week    = perfs.get("week", {})

        roi_all   = parse_roi(alltime.get("roi"))
        pnl_all   = parse_pnl(alltime.get("pnl"))
        roi_month = parse_roi(month.get("roi"))
        roi_week  = parse_roi(week.get("roi"))

        # Must be profitable across all three windows
        if (roi_all is not None and roi_all > 0 and
            roi_month is not None and roi_month > 0 and
            roi_week is not None and roi_week > 0):

            consistent.append({
                "address": t.get("ethAddress", "?"),
                "display_name": t.get("displayName") or "",
                "roi_all": roi_all,
                "pnl_all": pnl_all,
                "roi_month": roi_month,
                "roi_week": roi_week,
            })

    print(f"Consistently profitable (all-time + month + week): {len(consistent)}")

    # Sort by all-time ROI descending
    consistent.sort(key=lambda x: x["roi_all"], reverse=True)

    top10 = consistent[:10]

    print("\n" + "=" * 90)
    print(f"{'#':<4} {'Address':<46} {'All-time ROI':>13} {'All-time PnL':>13} {'Month ROI':>10} {'Week ROI':>9}")
    print("=" * 90)

    for i, t in enumerate(top10, 1):
        label = t["display_name"] or t["address"]
        if len(label) > 44:
            label = label[:41] + "..."
        print(
            f"{i:<4} {label:<46} "
            f"{t['roi_all']:>12.2f}% "
            f"{format_pnl(t['pnl_all']):>13} "
            f"{t['roi_month']:>9.2f}% "
            f"{t['roi_week']:>8.2f}%"
        )

    print("=" * 90)
    print("\nFull addresses:")
    for i, t in enumerate(top10, 1):
        name = f" ({t['display_name']})" if t["display_name"] else ""
        print(f"  {i}. {t['address']}{name}")

    # Also save to JSON
    out_file = "hl_top_traders.json"
    with open(out_file, "w") as f:
        json.dump(top10, f, indent=2)
    print(f"\nFull results saved to {out_file}")


if __name__ == "__main__":
    main()
