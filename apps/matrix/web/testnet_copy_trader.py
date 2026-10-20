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
testnet_copy_trader.py
======================
Standalone copy trader for Hyperliquid TESTNET.
- Polls mainnet trader positions every 5s
- Mirrors opens/closes on testnet via direct HL API
- No Hummingbot dependency — clean rate limiting
"""

import os
import time
import json
import requests
import logging
from datetime import datetime, timezone
from decimal import Decimal

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
from eth_account import Account

# ── Config from env ───────────────────────────────────────────────────────────

PRIVATE_KEY        = os.environ.get("HL_PRIVATE_KEY", "")
TRADER_ADDRESS     = os.environ.get("TRADER_ADDRESS", "")
ALLOCATION_PCT     = float(os.environ.get("ALLOCATION_PCT", "1.0"))
MAX_LEVERAGE       = int(os.environ.get("MAX_LEVERAGE", "20"))
POLL_INTERVAL      = int(os.environ.get("POLL_INTERVAL", "5"))
COPY_SHORTS        = os.environ.get("COPY_SHORTS", "true").lower() == "true"
LOG_PATH           = os.environ.get("LOG_PATH", "/logs/copy_trades_testnet.log")

MAINNET_API = "https://api.hyperliquid.xyz/info"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/logs/logs_testnet_copy_trader.log"),
    ]
)
log = logging.getLogger("testnet_copy_trader")

RED   = "\033[91m"
CYAN  = "\033[96m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def trade_log(line):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── Hyperliquid testnet setup ─────────────────────────────────────────────────

def setup_exchange():
    if not PRIVATE_KEY:
        raise ValueError("HL_PRIVATE_KEY env var not set")
    acct = Account.from_key(PRIVATE_KEY)
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    exchange = Exchange(acct, constants.TESTNET_API_URL)
    return acct, info, exchange

# ── Mainnet position polling ──────────────────────────────────────────────────

def fetch_trader_positions(address):
    r = requests.post(MAINNET_API,
        json={"type": "clearinghouseState", "user": address}, timeout=6)
    r.raise_for_status()
    data = r.json()
    result = {}
    for entry in data.get("assetPositions", []):
        pos = entry.get("position", {})
        coin = pos.get("coin")
        szi = float(pos.get("szi", 0))
        entry_px = float(pos.get("entryPx") or 0)
        lev = pos.get("leverage", {})
        leverage = int(lev.get("value", 1)) if isinstance(lev, dict) else 1
        if coin and abs(szi) > 1e-9:
            result[coin] = {
                "side": "LONG" if szi > 0 else "SHORT",
                "size": abs(szi),
                "entry_px": entry_px,
                "leverage": leverage,
            }
    return result

def get_testnet_balance(info, address):
    try:
        state = info.user_state(address)
        return float(state.get("marginSummary", {}).get("accountValue", 0))
    except Exception:
        return 0.0

def get_testnet_mid_price(info, coin):
    try:
        mids = info.all_mids()
        return float(mids.get(coin, 0))
    except Exception:
        return 0.0

# ── Order placement ───────────────────────────────────────────────────────────

def open_mirror(exchange, info, our_address, coin, pos, our_mirrors):
    leverage = min(pos["leverage"], MAX_LEVERAGE)
    balance = get_testnet_balance(info, our_address)
    allocation = balance * ALLOCATION_PCT
    if allocation <= 0:
        log.warning(f"No balance — skipping {coin}")
        return

    mid = get_testnet_mid_price(info, coin)
    if mid <= 0:
        log.warning(f"No price for {coin} — skipping")
        return

    notional = allocation * leverage
    amount = round(notional / mid, 6)
    if amount <= 0:
        log.warning(f"Amount too small for {coin}")
        return

    is_buy = pos["side"] == "LONG"
    log.info(f"Opening {pos['side']} mirror: {coin} amount={amount} @ ~${mid:.4f} "
             f"margin=${allocation:.2f} {leverage}x notional=${notional:.2f}")

    try:
        # Set leverage first
        exchange.update_leverage(leverage, coin, is_cross=True)
        time.sleep(0.3)

        # Place market order
        result = exchange.market_open(coin, is_buy, amount)
        log.info(f"Order result: {result}")

        our_mirrors[coin] = {
            "side": pos["side"],
            "amount": amount,
            "entry_price": mid,
            "entry_time": now(),
        }

        trade_log(
            f"{RED}{BOLD}"
            f"{now():<22} {'WE COPY':<14} {'OPENED':<8} {pos['side']:<6} {coin:<7} "
            f"{amount:>12.6f}  ${mid:>11,.4f}  margin=${allocation:.2f}  {leverage}x"
            f"{RESET}"
        )
    except Exception as e:
        log.error(f"Failed to open {coin}: {e}")
        # Still register mirror so close detection works
        our_mirrors[coin] = {
            "side": pos["side"],
            "amount": amount,
            "entry_price": mid,
            "entry_time": now(),
        }

def close_mirror(exchange, info, coin, our_mirrors):
    if coin not in our_mirrors:
        return
    mirror = our_mirrors.pop(coin)
    amount = mirror["amount"]
    side = mirror["side"]
    entry_px = mirror.get("entry_price", 0)
    entry_time = mirror.get("entry_time", "?")

    mid = get_testnet_mid_price(info, coin)
    pnl_str = ""
    if entry_px > 0 and mid > 0:
        pnl = (mid - entry_px) / entry_px * 100 if side == "LONG" else (entry_px - mid) / entry_px * 100
        pnl_str = f"  PnL~{pnl:+.2f}%"

    log.info(f"Closing mirror {side} {amount} {coin} @ ~${mid:.4f}{pnl_str}")

    try:
        is_buy = side == "SHORT"  # closing SHORT = buy, closing LONG = sell
        result = exchange.market_close(coin)
        log.info(f"Close result: {result}")
    except Exception as e:
        log.error(f"Failed to close {coin}: {e}")

    trade_log(
        f"{RED}{BOLD}"
        f"{now():<22} {'WE CLOSE':<14} {'CLOSED':<8} {side:<6} {coin:<7} "
        f"{amount:>12.6f}  ${mid:>11,.4f}  entry=${entry_px:,.4f}  opened={entry_time}{pnl_str}"
        f"{RESET}"
    )
    trade_log("─" * 80)

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("HL COPY TRADER — TESTNET")
    log.info(f"  Trader   : {TRADER_ADDRESS}")
    log.info(f"  Alloc    : {ALLOCATION_PCT*100:.0f}% of balance")
    log.info(f"  Max Lev  : {MAX_LEVERAGE}x")
    log.info(f"  Shorts   : {'yes' if COPY_SHORTS else 'no'}")
    log.info(f"  Poll     : every {POLL_INTERVAL}s")
    log.info("=" * 60)

    acct, info, exchange = setup_exchange()
    our_address = acct.address
    log.info(f"  Our addr : {our_address}")

    balance = get_testnet_balance(info, our_address)
    log.info(f"  Balance  : ${balance:.2f} USDC")

    trade_log(f"\n{'═'*80}")
    trade_log(f"{BOLD}{CYAN}  HL COPY TRADER TESTNET — started {now()}{RESET}")
    trade_log(f"  Trader: {TRADER_ADDRESS}  |  Alloc: {ALLOCATION_PCT*100:.0f}%  |  MaxLev: {MAX_LEVERAGE}x")
    trade_log(f"  Our testnet address: {our_address}  |  Balance: ${balance:.2f}")
    trade_log("═" * 80)
    trade_log(f"{'TIME (UTC)':<22} {'WHO':<14} {'ACTION':<8} {'SIDE':<6} {'COIN':<7} {'SIZE':>12}  {'PRICE':>13}  NOTE")
    trade_log("─" * 80)

    prev_positions = {}
    our_mirrors = {}
    last_heartbeat = 0

    while True:
        try:
            now_ts = time.time()

            # Heartbeat every 60s
            if now_ts - last_heartbeat >= 60:
                last_heartbeat = now_ts
                bal = get_testnet_balance(info, our_address)
                log.info(f"[heartbeat] mirrors open: {len(our_mirrors)} | balance: ${bal:.2f}")

            new_positions = fetch_trader_positions(TRADER_ADDRESS)

            # Detect closed positions
            for coin, old in list(prev_positions.items()):
                if coin not in new_positions:
                    trade_log(f"{now():<22} {'TRADER':<14} {'CLOSED':<8} {old['side']:<6} {coin:<7}")
                    if coin in our_mirrors:
                        log.info(f"Trader closed {coin} → closing mirror")
                        close_mirror(exchange, info, coin, our_mirrors)
                elif new_positions[coin]["side"] != old["side"]:
                    # Side flip
                    trade_log(f"{now():<22} {'TRADER':<14} {'FLIP':<8} {old['side']}→{new_positions[coin]['side']:<3} {coin:<7}")
                    if coin in our_mirrors:
                        close_mirror(exchange, info, coin, our_mirrors)
                        time.sleep(0.5)
                        open_mirror(exchange, info, our_address, coin, new_positions[coin], our_mirrors)

            # Detect new positions
            for coin, pos in new_positions.items():
                if coin not in prev_positions:
                    trade_log(f"{now():<22} {'TRADER':<14} {'OPENED':<8} {pos['side']:<6} {coin:<7} "
                              f"{pos['size']:>12.4f}  ${pos['entry_px']:>11,.4f}")
                    if coin in our_mirrors:
                        log.info(f"{coin} already mirrored — skipping")
                        continue
                    if pos["side"] == "SHORT" and not COPY_SHORTS:
                        log.info(f"Skipping SHORT {coin}")
                        continue
                    log.info(f"Trader opened {pos['side']} {coin} — copying")
                    open_mirror(exchange, info, our_address, coin, pos, our_mirrors)
                    time.sleep(0.5)

            prev_positions = new_positions

        except requests.Timeout:
            log.warning("Timeout polling trader")
        except Exception as e:
            log.error(f"Loop error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
