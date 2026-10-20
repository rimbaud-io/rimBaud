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

"""
hl_trader_audit.py
==================
Read-only daily health audit for the Hyperliquid leaders we copy.

Schedule (Europe/Berlin):
  Trader #1  →  01:00
  Trader #2  →  06:00
  Trader #3  →  11:00

Metrics (per trader):
  - free margin / equity / margin used  → can they open more?
  - last open fill time                 → how long have they been sleeping?
  - open positions snapshot
  - optional day/week PnL from leaderboard

Writes:
  /logs/trader_audits/latest.json
  /logs/trader_audits/history/YYYY-MM-DD_T{n}.json
  /logs/trader_audits/audit.log

Force re-audit: write {"trader": 1|2|3|"all"} to /logs/trader_audits/force.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import yaml

try:
    import matrix_store
except ImportError:
    matrix_store = None

# ── Paths (Docker mounts) ────────────────────────────────────────────────────
CONF_DIR = Path(os.environ.get("CONF_DIR", "/conf"))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", "/logs"))
OUT_DIR = LOGS_DIR / "trader_audits"
HISTORY_DIR = OUT_DIR / "history"
LATEST_PATH = OUT_DIR / "latest.json"
STATE_PATH = OUT_DIR / "state.json"
FORCE_PATH = OUT_DIR / "force.json"  # under logs so conf can stay :ro
YAML_PATH = CONF_DIR / "scripts" / "hl_copy_trader.yml"
LOG_PATH = OUT_DIR / "audit.log"

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

TZ = ZoneInfo(os.environ.get("AUDIT_TZ", "Europe/Berlin"))

# Local hour (0-23) when each trader is audited — once per calendar day
SCHEDULE_HOURS = {
    1: int(os.environ.get("AUDIT_HOUR_T1", "1")),   # 01:00
    2: int(os.environ.get("AUDIT_HOUR_T2", "6")),   # 06:00
    3: int(os.environ.get("AUDIT_HOUR_T3", "11")),  # 11:00
}

# Free-margin capacity thresholds (fraction of equity)
LOW_FREE_FRAC = float(os.environ.get("AUDIT_LOW_FREE_FRAC", "0.05"))   # <5% free → LOW
EMPTY_FREE_USD = float(os.environ.get("AUDIT_EMPTY_FREE_USD", "50"))  # < $50 free → EMPTY

# Sleep badges (hours since last open-like fill)
SLEEP_ACTIVE_H = 24
SLEEP_QUIET_H = 72
SLEEP_SLEEPING_H = 168  # 7d

POLL_SECONDS = int(os.environ.get("AUDIT_POLL_SECONDS", "30"))
HTTP_TIMEOUT = 25


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def hl_post(payload: dict) -> Any:
    r = requests.post(HL_INFO_URL, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def load_addresses() -> List[str]:
    if not YAML_PATH.exists():
        raise FileNotFoundError(f"Config not found: {YAML_PATH}")
    with open(YAML_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    addrs = cfg.get("trader_addresses") or []
    if not addrs:
        raise ValueError("No trader_addresses in hl_copy_trader.yml")
    return [str(a).strip() for a in addrs]


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        log(f"WARN read {path}: {e}")
    return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def fmt_ts(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def sleep_flag(hours: Optional[float]) -> str:
    if hours is None:
        return "UNKNOWN"
    if hours < SLEEP_ACTIVE_H:
        return "ACTIVE"
    if hours < SLEEP_QUIET_H:
        return "QUIET"
    if hours < SLEEP_SLEEPING_H:
        return "SLEEPING"
    return "DEAD"


def capacity_flag(free: float, equity: float) -> Tuple[str, bool]:
    """Return (flag, can_open)."""
    if equity <= 0 and free <= 0:
        return "EMPTY", False
    if free < EMPTY_FREE_USD:
        return "EMPTY", False
    if equity > 0 and free / equity < LOW_FREE_FRAC:
        return "LOW", True
    return "OK", True


def fetch_account(address: str) -> dict:
    data = hl_post({"type": "clearinghouseState", "user": address})
    ms = data.get("marginSummary") or data.get("crossMarginSummary") or {}
    equity = float(ms.get("accountValue") or 0)
    margin_used = float(ms.get("totalMarginUsed") or 0)
    notional = float(ms.get("totalNtlPos") or 0)
    withdrawable = float(data.get("withdrawable") or 0)
    free = max(0.0, equity - margin_used)

    positions = []
    for entry in data.get("assetPositions") or []:
        pos = entry.get("position") or {}
        coin = pos.get("coin")
        szi = float(pos.get("szi") or 0)
        if not coin or abs(szi) < 1e-12:
            continue
        lev = pos.get("leverage") or {}
        leverage = int(lev.get("value", 1)) if isinstance(lev, dict) else 1
        positions.append({
            "coin": coin,
            "side": "LONG" if szi > 0 else "SHORT",
            "size": abs(szi),
            "entry_px": float(pos.get("entryPx") or 0),
            "leverage": leverage,
            "u_pnl": float(pos.get("unrealizedPnl") or 0),
            "margin_used": float(pos.get("marginUsed") or 0),
        })
    try:
        subs = hl_post({"type": "subAccounts", "user": address}) or []
    except Exception:
        subs = []
    if isinstance(subs, list):
        by = {p["coin"]: p for p in positions}
        for row in subs:
            if not isinstance(row, dict):
                continue
            ch = row.get("clearinghouseState") or {}
            for entry in ch.get("assetPositions") or []:
                pos = entry.get("position") or {}
                coin = pos.get("coin")
                szi = float(pos.get("szi") or 0)
                if not coin or abs(szi) < 1e-12:
                    continue
                lev = pos.get("leverage") or {}
                leverage = int(lev.get("value", 1)) if isinstance(lev, dict) else 1
                rec = {
                    "coin": coin,
                    "side": "LONG" if szi > 0 else "SHORT",
                    "size": abs(szi),
                    "entry_px": float(pos.get("entryPx") or 0),
                    "leverage": leverage,
                    "u_pnl": float(pos.get("unrealizedPnl") or 0),
                    "margin_used": float(pos.get("marginUsed") or 0),
                }
                if coin not in by:
                    by[coin] = rec
                    continue
                a = by[coin]
                sa = a["size"] if a["side"] == "LONG" else -a["size"]
                sb = rec["size"] if rec["side"] == "LONG" else -rec["size"]
                net = sa + sb
                if abs(net) < 1e-12:
                    by.pop(coin, None)
                    continue
                tot = abs(sa) + abs(sb)
                if tot:
                    a["entry_px"] = (a["entry_px"] * abs(sa) + rec["entry_px"] * abs(sb)) / tot
                a["side"] = "LONG" if net > 0 else "SHORT"
                a["size"] = abs(net)
                a["u_pnl"] = float(a.get("u_pnl") or 0) + rec["u_pnl"]
                a["margin_used"] = float(a.get("margin_used") or 0) + rec["margin_used"]
                by[coin] = a
        positions = list(by.values())
    positions.sort(key=lambda p: p["coin"])
    u_pnl_total = round(sum(float(p.get("u_pnl") or 0) for p in positions), 2)
    return {
        "equity": round(equity, 2),
        "margin_used": round(margin_used, 2),
        "free_margin": round(free, 2),
        "withdrawable": round(withdrawable, 2),
        "notional": round(notional, 2),
        "n_positions": len(positions),
        "u_pnl_total": u_pnl_total,
        "positions": positions,
    }


def fetch_last_open(address: str) -> dict:
    """
    Last time the leader opened/increased a position.
    Prefer fills whose dir starts with 'Open'; fall back to any non-Close fill.
    """
    fills = hl_post({"type": "userFills", "user": address})
    if not isinstance(fills, list):
        fills = []

    open_like = []
    for f in fills:
        d = str(f.get("dir") or "")
        if d.startswith("Open") or d.startswith("Buy") or d.startswith("Sell"):
            # Buy/Sell can be opens on some historical payloads; prefer Open*
            open_like.append(f)

    pure_open = [f for f in open_like if str(f.get("dir") or "").startswith("Open")]
    pool = pure_open or open_like
    if not pool:
        return {
            "last_open_at": None,
            "last_open_ms": None,
            "last_open_coin": None,
            "last_open_dir": None,
            "sleep_hours": None,
            "sleep_flag": "UNKNOWN",
        }

    best = max(pool, key=lambda f: int(f.get("time") or 0))
    ms = int(best.get("time") or 0)
    age_h = (time.time() * 1000 - ms) / 1000.0 / 3600.0 if ms else None
    return {
        "last_open_at": fmt_ts(ms),
        "last_open_ms": ms,
        "last_open_coin": best.get("coin"),
        "last_open_dir": best.get("dir"),
        "sleep_hours": round(age_h, 2) if age_h is not None else None,
        "sleep_flag": sleep_flag(age_h),
    }


def fetch_leaderboard_row(address: str) -> dict:
    try:
        resp = requests.get(HL_LEADERBOARD_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("leaderboardRows") or []
        key = address.lower()
        for i, row in enumerate(rows):
            if str(row.get("ethAddress") or "").lower() == key:
                perfs = {p[0]: p[1] for p in (row.get("windowPerformances") or [])}
                def _pnl(w):
                    try:
                        return round(float((perfs.get(w) or {}).get("pnl") or 0), 2)
                    except (TypeError, ValueError):
                        return None
                def _roi(w):
                    try:
                        return round(float((perfs.get(w) or {}).get("roi") or 0) * 100, 2)
                    except (TypeError, ValueError):
                        return None
                return {
                    "rank": i + 1,
                    "leaderboard_n": len(rows),
                    "day_pnl": _pnl("day"),
                    "week_pnl": _pnl("week"),
                    "month_pnl": _pnl("month"),
                    "day_roi_pct": _roi("day"),
                    "week_roi_pct": _roi("week"),
                    "month_roi_pct": _roi("month"),
                    "display_name": row.get("displayName") or "",
                }
        return {"rank": None, "leaderboard_n": len(rows), "note": "not on leaderboard"}
    except Exception as e:
        return {"rank": None, "error": str(e)[:120]}


def audit_trader(trader_num: int, address: str) -> dict:
    now = datetime.now(timezone.utc)
    log(f"AUDIT T#{trader_num} start {address[:10]}…{address[-6:]}")
    acct = fetch_account(address)
    sleep = fetch_last_open(address)
    lb = fetch_leaderboard_row(address)
    cap, can_open = capacity_flag(acct["free_margin"], acct["equity"])

    result = {
        "trader": trader_num,
        "address": address,
        "short": f"{address[:10]}…{address[-6:]}",
        "audited_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "audited_at_local": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        **acct,
        **sleep,
        "capacity_flag": cap,
        "can_open": can_open,
        "leaderboard": lb,
        "ok": True,
        "error": None,
    }
    log(
        f"AUDIT T#{trader_num} done  equity=${acct['equity']:,.2f} free=${acct['free_margin']:,.2f} "
        f"uPnL=${acct.get('u_pnl_total', 0):+,.2f} "
        f"cap={cap} sleep={sleep['sleep_flag']} ({sleep['sleep_hours']}h) "
        f"pos={acct['n_positions']}"
    )
    return result


def audit_trader_safe(trader_num: int, address: str) -> dict:
    try:
        return audit_trader(trader_num, address)
    except Exception as e:
        log(f"AUDIT T#{trader_num} FAILED: {e}")
        traceback.print_exc()
        return {
            "trader": trader_num,
            "address": address,
            "short": f"{address[:10]}…{address[-6:]}",
            "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "ok": False,
            "error": str(e)[:200],
            "can_open": None,
            "capacity_flag": "ERROR",
            "sleep_flag": "UNKNOWN",
        }


def merge_latest(trader_num: int, result: dict) -> dict:
    latest = load_json(LATEST_PATH, {
        "timezone": str(TZ),
        "schedule": {str(k): f"{v:02d}:00" for k, v in SCHEDULE_HOURS.items()},
        "traders": {},
        "updated_at": None,
    })
    traders = latest.get("traders") or {}
    traders[str(trader_num)] = result
    latest["traders"] = traders
    latest["timezone"] = str(TZ)
    latest["schedule"] = {str(k): f"{v:02d}:00" for k, v in SCHEDULE_HOURS.items()}
    latest["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save_json(LATEST_PATH, latest)

    day = datetime.now(TZ).strftime("%Y-%m-%d")
    save_json(HISTORY_DIR / f"{day}_T{trader_num}.json", result)
    if matrix_store:
        try:
            matrix_store.ensure()
            matrix_store.leader_audit_put(result)
        except Exception as e:
            log(f"WARN sqlite leader_audit_put: {e}")
    return latest


def load_state() -> dict:
    if matrix_store:
        try:
            matrix_store.ensure()
            st = matrix_store.leader_audit_state_get()
            if isinstance(st, dict) and st.get("last_run_date"):
                return st
        except Exception:
            pass
    return load_json(STATE_PATH, {"last_run_date": {}})


def save_state(state: dict) -> None:
    save_json(STATE_PATH, state)
    if matrix_store:
        try:
            matrix_store.ensure()
            matrix_store.leader_audit_state_set(state)
        except Exception as e:
            log(f"WARN sqlite state: {e}")


def due_traders(addresses: List[str], state: dict) -> List[int]:
    """Traders whose local schedule hour has been reached and not yet run today."""
    now_local = datetime.now(TZ)
    today = now_local.strftime("%Y-%m-%d")
    last = state.get("last_run_date") or {}
    due = []
    for i, _addr in enumerate(addresses, 1):
        hour = SCHEDULE_HOURS.get(i)
        if hour is None:
            continue
        if now_local.hour < hour:
            continue
        if last.get(str(i)) == today:
            continue
        due.append(i)
    return due


def consume_force() -> Optional[List[int]]:
    """
    If force_audit.json exists, return list of trader nums (empty list = all)
    and delete the file. Returns None when no force request.
    """
    raw_sql = None
    if matrix_store:
        try:
            matrix_store.ensure()
            raw_sql = matrix_store.leader_audit_force_take()
        except Exception as e:
            log(f"WARN sqlite force take: {e}")
    if raw_sql is not None:
        raw = raw_sql
        if FORCE_PATH.exists():
            try:
                FORCE_PATH.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            if raw in ("all", "*", 0, "0", None, ""):
                return []
            return [int(raw)]
        except Exception:
            return []
    if not FORCE_PATH.exists():
        return None
    try:
        data = load_json(FORCE_PATH, {})
        raw = data.get("trader", "all")
        if raw in ("all", "*", 0, "0", None, ""):
            nums: List[int] = []  # empty → all
        else:
            nums = [int(raw)]
    except Exception as e:
        log(f"force_audit parse error: {e}")
        nums = []
    try:
        FORCE_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    return nums


def run_one(trader_num: int, addresses: List[str], state: dict) -> None:
    if trader_num < 1 or trader_num > len(addresses):
        log(f"skip invalid trader #{trader_num}")
        return
    address = addresses[trader_num - 1]
    result = audit_trader_safe(trader_num, address)
    merge_latest(trader_num, result)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    state.setdefault("last_run_date", {})[str(trader_num)] = today
    save_state(state)


def stale_trader_nums(addresses: List[str]) -> List[int]:
    """T# slots whose saved audit address no longer matches hl_copy_trader.yml."""
    latest = load_json(LATEST_PATH, {})
    traders = (latest.get("traders") or {}) if isinstance(latest, dict) else {}
    out = []
    for i, addr in enumerate(addresses, 1):
        row = traders.get(str(i)) if isinstance(traders.get(str(i)), dict) else {}
        old = str(row.get("address") or "").strip().lower()
        new = str(addr or "").strip().lower()
        if not new:
            continue
        if old != new:
            out.append(i)
    return out


def bootstrap_if_empty(addresses: List[str], state: dict) -> None:
    """First start / roster swap: audit missing or replaced T# slots."""
    latest = load_json(LATEST_PATH, {})
    traders = (latest.get("traders") or {}) if isinstance(latest, dict) else {}
    missing = [i for i in range(1, len(addresses) + 1) if str(i) not in traders]
    stale = stale_trader_nums(addresses)
    need = []
    for n in missing + stale:
        if n not in need:
            need.append(n)
    if not need:
        return
    log(f"bootstrap audit for missing/swapped traders: {need}")
    for n in need:
        run_one(n, addresses, state)
        time.sleep(1.5)


def next_slot_summary(addresses: List[str], state: dict) -> str:
    now_local = datetime.now(TZ)
    today = now_local.strftime("%Y-%m-%d")
    last = state.get("last_run_date") or {}
    parts = []
    for i in range(1, len(addresses) + 1):
        h = SCHEDULE_HOURS.get(i, 0)
        done = last.get(str(i)) == today
        parts.append(f"T#{i}@{h:02d}:00{'✓' if done else ''}")
    return " ".join(parts)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    log("=" * 60)
    log("hl_trader_audit worker starting")
    log(f"  TZ={TZ}  schedule={SCHEDULE_HOURS}")
    log(f"  conf={YAML_PATH}  out={LATEST_PATH}")

    try:
        addresses = load_addresses()
    except Exception as e:
        log(f"FATAL config: {e}")
        return 1

    if matrix_store:
        try:
            matrix_store.init(str(Path(os.environ.get("CONF_DIR", "/conf")) / "matrix.db"))
            n = matrix_store.leader_audit_import_json(str(LOGS_DIR))
            log(f"sqlite leader_audits ready (imported {n} json rows)")
        except Exception as e:
            log(f"WARN sqlite init: {e}")

    for i, a in enumerate(addresses, 1):
        log(f"  T#{i} {a}")

    state = load_state()
    bootstrap_if_empty(addresses, state)

    while True:
        try:
            addresses = load_addresses()  # pick up address swaps without restart
            state = load_state()

            force = consume_force()
            if force is not None:
                targets = force if force else list(range(1, len(addresses) + 1))
                log(f"FORCE audit → {targets}")
                for n in targets:
                    run_one(n, addresses, state)
                    time.sleep(1.0)
            else:
                stale = stale_trader_nums(addresses)
                if stale:
                    log(f"ROSTER SWAP audit → {stale}")
                    for n in stale:
                        run_one(n, addresses, state)
                        time.sleep(1.0)
                due = due_traders(addresses, state)
                for n in due:
                    if n in stale:
                        continue
                    log(f"SCHEDULE due T#{n}")
                    run_one(n, addresses, state)
                    time.sleep(1.0)

            if int(time.time()) % 300 < POLL_SECONDS:
                log(f"heartbeat  local={datetime.now(TZ).strftime('%Y-%m-%d %H:%M %Z')}  {next_slot_summary(addresses, state)}")

        except Exception as e:
            log(f"loop error: {e}")
            traceback.print_exc()

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    # One-shot mode for testing: python hl_trader_audit.py --once [N|all]
    if len(sys.argv) >= 2 and sys.argv[1] in ("--once", "once"):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        addresses = load_addresses()
        state = load_state()
        who = sys.argv[2] if len(sys.argv) >= 3 else "all"
        if who == "all":
            nums = list(range(1, len(addresses) + 1))
        else:
            nums = [int(who)]
        for n in nums:
            run_one(n, addresses, state)
            time.sleep(0.5)
        print(json.dumps(load_json(LATEST_PATH, {}), indent=2))
        sys.exit(0)
    sys.exit(main() or 0)
