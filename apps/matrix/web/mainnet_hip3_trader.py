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

"""
mainnet_hip3_trader.py
======================
Sidecar copy trader for Hyperliquid HIP-3 (commodity / DEX-abstraction) markets.
- Watches the target trader's fills for xyz: markets every 5s
- Opens / closes matching positions on mainnet using the hyperliquid-python-sdk
- Fixed $10 allocation per position, no leverage (1x)
"""

import os
import time
import math
import requests
import logging
from datetime import datetime, timezone
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# ── Config ────────────────────────────────────────────────────────────────────

PRIVATE_KEY    = os.environ.get("HL_PRIVATE_KEY", "")
TRADER_ADDRESS = os.environ.get("TRADER_ADDRESS", "0x9f3e77cb89df964003053aa5b438e5697c77f4f9")
ALLOCATION_USD = float(os.environ.get("ALLOCATION_USD", "10"))   # fixed $ per position
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL", "5"))
LOG_PATH       = os.environ.get("LOG_PATH", "/logs/copy_trades_hip3.log")

MAINNET_API = "https://api.hyperliquid.xyz/info"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/logs/logs_hip3_trader.log"),
    ]
)
log = logging.getLogger("hip3_trader")

PURPLE = "\033[95m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def trade_log(line):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── Exchange setup ────────────────────────────────────────────────────────────

def setup_exchange():
    if not PRIVATE_KEY:
        raise ValueError("HL_PRIVATE_KEY env var not set")
    acct = Account.from_key(PRIVATE_KEY)
    for attempt in range(10):
        try:
            info = Info(constants.MAINNET_API_URL, skip_ws=True, perp_dexs=["", "xyz"])
            exchange = Exchange(acct, constants.MAINNET_API_URL, perp_dexs=["", "xyz"])
            return acct, info, exchange
        except Exception as e:
            wait = 30 * (attempt + 1)
            log.warning(f"Exchange init failed (attempt {attempt+1}/10): {e} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("Could not initialize exchange after 10 attempts")

# ── Price fetch ───────────────────────────────────────────────────────────────

def get_hip3_price(coin: str) -> float:
    """Get latest price for a HIP-3 coin via candle API."""
    try:
        start = int(time.time() * 1000) - 120_000  # last 2 minutes
        r = requests.post(MAINNET_API,
            json={"type": "candleSnapshot", "req": {"coin": coin, "interval": "1m", "startTime": start}},
            timeout=6)
        r.raise_for_status()
        candles = r.json()
        if candles:
            return float(candles[-1]["c"])  # latest close
    except Exception as e:
        log.warning(f"Price fetch failed for {coin}: {e}")
    return 0.0

def get_our_balance(info, address: str) -> float:
    try:
        # Perp margin (standard accounts)
        state = info.user_state(address)
        perp_val = float(state.get("marginSummary", {}).get("accountValue", 0))
        # Spot USDC (unified accounts keep funds here)
        r = requests.post(MAINNET_API, json={"type": "spotClearinghouseState", "user": address}, timeout=6)
        r.raise_for_status()
        spot_usdc = sum(float(b["total"]) for b in r.json().get("balances", []) if b["coin"] == "USDC")
        return max(perp_val, spot_usdc)
    except Exception:
        return 0.0

# ── Fill polling ──────────────────────────────────────────────────────────────

def fetch_hip3_fills(address: str, since_ts: int) -> list:
    """Return new HIP-3 fills since since_ts (ms)."""
    r = requests.post(MAINNET_API,
        json={"type": "userFills", "user": address, "startTime": since_ts + 1},
        timeout=6)
    r.raise_for_status()
    fills = r.json()
    return [f for f in fills if ":" in f.get("coin", "")]

# ── Order helpers ─────────────────────────────────────────────────────────────

def open_position(exchange, info, our_addr: str, coin: str, is_buy: bool, ref_px: float):
    """Open a HIP-3 position with fixed $ALLOCATION_USD at 1x leverage."""
    px = get_hip3_price(coin) or ref_px
    if px <= 0:
        log.warning(f"No price for {coin}, skipping")
        return None

    try:
        asset = info.name_to_asset(coin)
        sz_decimals = info.asset_to_sz_decimals.get(asset, 4)
    except Exception:
        sz_decimals = 4
    step = 10 ** (-sz_decimals)
    size = math.ceil(ALLOCATION_USD / px / step) * step
    size = round(size, sz_decimals)  # fix any float imprecision
    if size <= 0:
        log.warning(f"Size too small for {coin} @ ${px}")
        return None

    side_str = "LONG" if is_buy else "SHORT"
    log.info(f"Opening {side_str} {coin}  size={size}  px~${px:.4f}  alloc=${ALLOCATION_USD}")

    try:
        exchange.update_leverage(1, coin, is_cross=True)
        time.sleep(0.3)
        result = exchange.market_open(coin, is_buy, size)
        log.info(f"Open result: {result}")
        trade_log(
            f"{PURPLE}{BOLD}"
            f"{now_str():<22} {'WE COPY':<14} {'OPENED':<8} {side_str:<6} {coin.replace('xyz:',''):<12} "
            f"{size:>10.6f}  ${px:>12,.4f}  alloc=${ALLOCATION_USD:.2f}  1x"
            f"{RESET}"
        )
        return result
    except Exception as e:
        log.error(f"Failed to open {coin}: {e}")
        trade_log(
            f"{PURPLE}{BOLD}"
            f"{now_str():<22} {'WE COPY':<14} {'FAILED':<8} {side_str:<6} {coin.replace('xyz:',''):<12} "
            f"error: {e}"
            f"{RESET}"
        )
        return None

def close_position(exchange, info, coin: str, side: str, entry_px: float):
    """Close a HIP-3 mirror position."""
    mid = get_hip3_price(coin) or entry_px
    pnl_str = ""
    if entry_px > 0 and mid > 0:
        pnl = (mid - entry_px) / entry_px * 100 if side == "LONG" else (entry_px - mid) / entry_px * 100
        pnl_str = f"  PnL~{pnl:+.2f}%"

    log.info(f"Closing {side} {coin} @ ~${mid:.4f}{pnl_str}")
    try:
        result = exchange.market_close(coin)
        log.info(f"Close result: {result}")
        trade_log(
            f"{PURPLE}{BOLD}"
            f"{now_str():<22} {'WE CLOSE':<14} {'CLOSED':<8} {side:<6} {coin.replace('xyz:',''):<12} "
            f"${mid:>12,.4f}  entry=${entry_px:,.4f}{pnl_str}"
            f"{RESET}"
        )
    except Exception as e:
        log.error(f"Failed to close {coin}: {e}")

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("HIP-3 COMMODITY COPY TRADER — MAINNET")
    log.info(f"  Trader   : {TRADER_ADDRESS}")
    log.info(f"  Alloc    : ${ALLOCATION_USD:.2f} per position (no leverage)")
    log.info(f"  Poll     : every {POLL_INTERVAL}s")
    log.info("=" * 60)

    acct, info, exchange = setup_exchange()
    our_addr = acct.address
    log.info(f"  Our addr : {our_addr}")

    bal = get_our_balance(info, our_addr)
    log.info(f"  Balance  : ${bal:.2f} USDC")

    trade_log(f"\n{'═'*80}")
    trade_log(f"{PURPLE}{BOLD}  HIP-3 COMMODITY COPY TRADER — started {now_str()}{RESET}")
    trade_log(f"  Trader: {TRADER_ADDRESS}  |  Alloc: ${ALLOCATION_USD:.2f}/trade  |  1x (no leverage)")
    trade_log(f"  Our address: {our_addr}  |  Balance: ${bal:.2f}")
    trade_log("═" * 80)
    trade_log(f"{'TIME (UTC)':<22} {'WHO':<14} {'ACTION':<8} {'SIDE':<6} {'COIN':<12} {'SIZE':>10}  {'PRICE':>13}  NOTE")
    trade_log("─" * 80)

    # Watermark: only process fills from now on
    since_ts = int(time.time() * 1000)
    seen_fills: set = set()  # dedup by (coin, dir, time_ms)

    # Track our open HIP-3 mirrors: coin → {side, entry_px}
    our_mirrors: dict = {}

    last_heartbeat = 0.0

    while True:
        try:
            now_ts = time.time()

            if now_ts - last_heartbeat >= 60:
                last_heartbeat = now_ts
                bal = get_our_balance(info, our_addr)
                log.info(f"[heartbeat] mirrors open: {len(our_mirrors)} | balance: ${bal:.2f}")

            hip3_fills = fetch_hip3_fills(TRADER_ADDRESS, since_ts)
            if hip3_fills:
                since_ts = max(f["time"] for f in hip3_fills)

            # Group fills by coin+direction (avoid per-partial-fill noise)
            groups: dict = {}
            for f in sorted(hip3_fills, key=lambda x: x["time"]):
                coin = f["coin"]
                direction = f.get("dir", "?")
                t_ms = f["time"]
                dedup = (coin, direction, t_ms)
                if dedup in seen_fills:
                    continue
                seen_fills.add(dedup)
                key = f"{coin}|{direction}|{t_ms // 10000}"
                if key not in groups:
                    groups[key] = {"coin": coin, "dir": direction, "px": float(f["px"]), "sz": 0.0}
                groups[key]["sz"] += float(f["sz"])

            for g in groups.values():
                coin     = g["coin"]
                direction = g["dir"]
                ref_px   = g["px"]
                is_open  = "Open" in direction
                is_long  = "Long" in direction
                side_str = "LONG" if is_long else "SHORT"
                label    = coin.replace("xyz:", "")

                # Log trader activity
                action = "OPENED" if is_open else "CLOSED"
                trade_log(
                    f"{now_str():<22} {'TRADER #1':<14} {action:<8} {side_str:<6} {label:<12} "
                    f"{g['sz']:>10.4f}  ${ref_px:>12,.4f}  [HIP-3]"
                )

                if is_open:
                    if coin in our_mirrors:
                        log.info(f"{coin} already mirrored — skipping")
                        continue
                    result = open_position(exchange, info, our_addr, coin, is_long, ref_px)
                    if result and result.get("status") == "ok":
                        our_mirrors[coin] = {"side": side_str, "entry_px": ref_px}
                else:
                    # Close
                    if coin in our_mirrors:
                        mirror = our_mirrors.pop(coin)
                        close_position(exchange, info, coin, mirror["side"], mirror["entry_px"])
                    else:
                        log.info(f"No mirror for {coin} — nothing to close")

        except requests.Timeout:
            log.warning("Timeout polling fills")
        except Exception as e:
            log.error(f"Loop error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
