#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne www.rimbaud.io
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
hl_copy_trader_testnet.py
=========================
TESTNET version — trades on https://api.hyperliquid-testnet.xyz
Mirrors open perpetual positions from up to 3 Hyperliquid wallet addresses.

Logic:
  - Every poll_interval_seconds: fetch each tracked wallet's open positions via HL public API
  - New position detected  → open same-side market order on our connector
  - Position closed by them → close our mirror with a market order
  - Side-flip (LONG→SHORT)  → close old, open new
  - Two traders same coin   → first one wins; second skipped (logged)

Config (hl_copy_trader.yml):
  connector_name: hyperliquid_perpetual
  leverage: 5
  allocation_pct_per_trader: 0.30   # fraction of free balance per trader slot
  poll_interval_seconds: 5
  copy_shorts: false                 # set true to also mirror short positions
  trader_addresses:
    - ""
    - ""
    - ""
"""

import os
import time
import requests
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, PositionAction, TradeType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

# ── Trade log ─────────────────────────────────────────────────────────────────

TRADE_LOG_PATH = "/home/hummingbot/logs/copy_trades_testnet.log"

# ANSI colours (stripped automatically by viewers that don't support them)
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def trade_log(line: str):
    """Append a line to the dedicated trade log file."""
    try:
        with open(TRADE_LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── Hyperliquid public API ────────────────────────────────────────────────────

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Map HL coin symbol → hyperliquid_perpetual trading pair
COIN_TO_PAIR: Dict[str, str] = {
    "BTC":   "BTC-USD",
    "ETH":   "ETH-USD",
    "SOL":   "SOL-USD",
    "ARB":   "ARB-USD",
    "OP":    "OP-USD",
    "AVAX":  "AVAX-USD",
    "MATIC": "MATIC-USD",
    "DOGE":  "DOGE-USD",
    "XRP":   "XRP-USD",
    "BNB":   "BNB-USD",
    "LINK":  "LINK-USD",
    "SUI":   "SUI-USD",
    "APT":   "APT-USD",
    "INJ":   "INJ-USD",
    "WIF":   "WIF-USD",
    "PEPE":  "PEPE-USD",
    "TIA":   "TIA-USD",
    "SEI":   "SEI-USD",
    "JUP":   "JUP-USD",
    "kPEPE": "kPEPE-USD",
    "ENA":   "ENA-USD",
    "HYPE":  "HYPE-USD",
}


# ── Config ────────────────────────────────────────────────────────────────────

class HLCopyTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual_testnet")
    max_leverage: int = Field(20)            # cap on copied leverage
    allocation_pct: float = Field(0.50)      # 50% of free balance per trade
    poll_interval_seconds: int = Field(5)
    copy_shorts: bool = Field(True)
    trader_addresses: List[str] = Field(default_factory=list)


# ── Strategy ──────────────────────────────────────────────────────────────────

class HLCopyTraderTestnet(StrategyV2Base):
    """
    Copy-trades up to 3 Hyperliquid wallet addresses by polling their
    open perpetual positions and mirroring opens/closes on our own account.
    """

    connectors: Dict
    config: HLCopyTraderConfig

    def __init__(self, connectors: Dict, config: HLCopyTraderConfig):
        super().__init__(connectors, config)

        self._last_poll: float = 0.0

        # trader_address → {coin: {"side": "LONG"|"SHORT", "size": float, "entry_px": float}}
        self._trader_positions: Dict[str, Dict[str, dict]] = {
            addr: {} for addr in config.trader_addresses
        }

        # pair → {"trader": addr, "coin": str, "side": "LONG"|"SHORT",
        #          "amount": Decimal, "order_id": str|None}
        self._our_mirrors: Dict[str, dict] = {}

        self._pending_order_ids: set = set()

        self.logger().info("=" * 60)
        self.logger().info("HL Copy Trader started")
        self.logger().info(f"  Traders  : {len(config.trader_addresses)}")
        self.logger().info(f"  Max Lev  : {config.max_leverage}x (copies trader leverage up to this)")
        self.logger().info(f"  Alloc    : {config.allocation_pct*100:.0f}% of free balance per trade")
        self.logger().info(f"  Poll     : every {config.poll_interval_seconds}s")
        self.logger().info(f"  Shorts   : {'yes' if config.copy_shorts else 'no'}")
        for i, addr in enumerate(config.trader_addresses, 1):
            self.logger().info(f"  Trader {i} : {addr}")
        self.logger().info("=" * 60)

        # ── Trade log banner ──────────────────────────────────────────────
        sep = "═" * 80
        trade_log(f"\n{sep}")
        trade_log(f"{BOLD}{CYAN}  HL COPY TRADER  —  started {_now()}{RESET}")
        trade_log(f"  Max Leverage : {config.max_leverage}x  |  Alloc : {config.allocation_pct*100:.0f}% balance  |  Shorts : {'YES' if config.copy_shorts else 'NO'}")
        for i, addr in enumerate(config.trader_addresses, 1):
            trade_log(f"  Trader #{i} : {addr}")
        trade_log(sep)
        trade_log(f"{'TIME (UTC)':<22} {'WHO':<14} {'ACTION':<8} {'SIDE':<6} {'COIN':<7} {'SIZE':>10}  {'PRICE':>12}  NOTE")
        trade_log("─" * 80)

    @classmethod
    def init_markets(cls, config: HLCopyTraderConfig):
        # Pre-register common pairs so the connector order book initializes.
        # Additional pairs are handled at runtime via market orders.
        # Minimal pairs for testnet — avoids rate limiting during init
        cls.markets = {config.connector_name: {"BTC-USD", "ETH-USD"}}

    # ── Restore existing positions on startup ────────────────────────────────

    def _restore_our_positions(self):
        """On startup, check our own account for open positions and register them in _our_mirrors."""
        try:
            connector = self.connectors[self.config.connector_name]
            account_positions = connector.account_positions
            if not account_positions:
                return
            for pos_key, pos in account_positions.items():
                pair = pos.trading_pair if hasattr(pos, "trading_pair") else str(pos_key)
                coin = pair.replace("-USD", "")
                amount = abs(Decimal(str(pos.amount))) if hasattr(pos, "amount") else Decimal("0")
                side = "LONG" if (hasattr(pos, "amount") and pos.amount > 0) else "SHORT"
                entry_px = Decimal(str(pos.entry_price)) if hasattr(pos, "entry_price") else Decimal("0")
                if amount > 0 and pair not in self._our_mirrors:
                    trader = self.config.trader_addresses[0] if self.config.trader_addresses else ""
                    self._our_mirrors[pair] = {
                        "trader": trader,
                        "coin": coin,
                        "side": side,
                        "amount": amount,
                        "entry_price": entry_px,
                        "entry_time": "restored",
                        "order_id": None,
                    }
                    self.logger().info(f"[restore] Found existing {side} {amount} {pair} — registered in mirrors")
                    trade_log(f"{YELLOW}[restore] Existing {side} {amount} {coin} @ ${float(entry_px):,.4f} — now tracked{RESET}")
        except Exception as e:
            self.logger().warning(f"Position restore failed: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def on_tick(self):
        now = time.time()
        if now - self._last_poll < self.config.poll_interval_seconds:
            return
        self._last_poll = now

        # Restore positions once on first tick after connector is ready
        if not hasattr(self, "_positions_restored"):
            self._positions_restored = True
            self._restore_our_positions()

        # Heartbeat every 60 seconds so we know the bot is alive
        if not hasattr(self, "_last_heartbeat"):
            self._last_heartbeat = 0.0
        if now - self._last_heartbeat >= 60:
            self._last_heartbeat = now
            mirrors = len(self._our_mirrors)
            self.logger().info(f"[heartbeat] polling 3 traders | mirrors open: {mirrors} | balance: ${self._get_free_balance():.2f}")

        for address in self.config.trader_addresses:
            try:
                self._check_trader(address)
            except requests.Timeout:
                self.logger().warning(f"Timeout fetching {address[:10]}...")
            except Exception as e:
                self.logger().warning(f"Error checking {address[:10]}...: {e}")

    # ── Trader monitoring ─────────────────────────────────────────────────────

    def _fetch_trader_positions(self, address: str) -> Dict[str, dict]:
        """Return {coin: {side, size, entry_px}} for all open positions."""
        resp = requests.post(
            HL_INFO_URL,
            json={"type": "clearinghouseState", "user": address},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()

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
                    "pair": COIN_TO_PAIR.get(coin),
                }
        return result

    def _check_trader(self, address: str):
        new_pos = self._fetch_trader_positions(address)
        old_pos = self._trader_positions[address]

        trader_num = self.config.trader_addresses.index(address) + 1
        trader_tag = f"TRADER #{trader_num}"

        # Detect closed / side-flipped positions
        for coin, old in list(old_pos.items()):
            new = new_pos.get(coin)
            if new is None:
                # Trader closed this coin
                pair = old.get("pair")
                trade_log(
                    f"{_now():<22} {trader_tag:<14} {'CLOSED':<8} {old['side']:<6} {coin:<7} "
                    f"{'':>10}  {'':>12}  {address[:10]}...{address[-6:]}"
                )
                if pair and pair in self._our_mirrors and self._our_mirrors[pair]["trader"] == address:
                    self.logger().info(f"[{address[:10]}] Closed {old['side']} {coin} → closing our mirror")
                    self._close_mirror(pair, reason="trader closed")
            elif new["side"] != old["side"]:
                # Side flipped LONG↔SHORT
                pair = old.get("pair")
                trade_log(
                    f"{_now():<22} {trader_tag:<14} {'FLIP':<8} {old['side']}→{new['side']:<4} {coin:<7} "
                    f"{new['size']:>10.4f}  ${new['entry_px']:>11,.2f}  side-flip"
                )
                if pair and pair in self._our_mirrors and self._our_mirrors[pair]["trader"] == address:
                    self.logger().info(f"[{address[:10]}] Side flip on {coin}: {old['side']}→{new['side']}")
                    self._close_mirror(pair, reason="side flip")
                    self._open_mirror(address, coin, new)

        # Detect new positions
        for coin, pos in new_pos.items():
            if coin not in old_pos:
                pair = pos.get("pair")
                trade_log(
                    f"{_now():<22} {trader_tag:<14} {'OPENED':<8} {pos['side']:<6} {coin:<7} "
                    f"{pos['size']:>10.4f}  ${pos['entry_px']:>11,.2f}  {address[:10]}...{address[-6:]}"
                )
                if pair is None:
                    self.logger().info(f"[{address[:10]}] Skipping unsupported coin: {coin}")
                    trade_log(f"{'':>22} {'':>14} {'SKIP':<8} {'':6} {coin:<7} unsupported coin")
                    continue
                if pair in self._our_mirrors:
                    owner = self._our_mirrors[pair]["trader"]
                    self.logger().info(
                        f"[{address[:10]}] {coin} already mirrored from {owner[:10]}... — skipping"
                    )
                    trade_log(f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} already mirrored by another trader")
                    continue
                if pos["side"] == "SHORT" and not self.config.copy_shorts:
                    self.logger().info(f"[{address[:10]}] Skipping SHORT on {coin} (copy_shorts=false)")
                    trade_log(f"{'':>22} {'':>14} {'SKIP':<8} SHORT  {coin:<7} copy_shorts=false")
                    continue

                self.logger().info(
                    f"[{address[:10]}] NEW {pos['side']} {pos['size']:.4f} {coin} "
                    f"@ ${pos['entry_px']:,.2f} — opening mirror"
                )
                self._open_mirror(address, coin, pos)

        self._trader_positions[address] = new_pos

    # ── Order helpers ─────────────────────────────────────────────────────────

    def _get_free_balance(self) -> Decimal:
        try:
            connector = self.connectors[self.config.connector_name]
            bal = connector.get_available_balance("USD")
            return Decimal(str(bal)) if bal else Decimal("0")
        except Exception:
            return Decimal("0")

    def _get_mid_price(self, pair: str) -> Optional[Decimal]:
        try:
            connector = self.connectors[self.config.connector_name]
            price = connector.get_mid_price(pair)
            return Decimal(str(price)) if price else None
        except Exception:
            return None

    def _open_mirror(self, trader_addr: str, coin: str, pos: dict):
        pair = pos["pair"]

        # Use exact trader's leverage capped at max_leverage
        leverage = min(pos.get("leverage", 1), self.config.max_leverage)
        self.logger().info(f"Trader leverage: {pos.get('leverage', 1)}x → using {leverage}x")

        # Set leverage first
        try:
            self.connectors[self.config.connector_name].set_leverage(pair, leverage)
        except Exception as e:
            self.logger().warning(f"set_leverage failed for {pair}: {e}")

        balance = self._get_free_balance()
        allocation = balance * Decimal(str(self.config.allocation_pct))
        if allocation <= 0:
            self.logger().warning(f"No balance available — skipping")
            return

        # Use live mid-price if available, else fall back to trader's entry price
        mid = self._get_mid_price(pair) or Decimal(str(pos["entry_px"]))
        if mid <= 0:
            self.logger().warning(f"Invalid price for {pair}: {mid}")
            return

        notional = allocation * leverage
        # Quantize to 4 decimal places (safe for BTC); connectors clamp further if needed
        amount = (notional / mid).quantize(Decimal("0.00001"))

        if amount <= 0:
            self.logger().warning(f"Amount too small for {pair}: {amount}")
            return

        self.logger().info(
            f"Opening {pos['side']} mirror: {pair}  amount={amount}  "
            f"price~${mid:,.2f}  margin=${allocation:.2f}  notional=${notional:.2f}"
        )

        # Register mirror FIRST so close detection always works even if order errors
        self._our_mirrors[pair] = {
            "trader": trader_addr,
            "coin": coin,
            "side": pos["side"],
            "amount": amount,
            "entry_price": mid,
            "entry_time": _now(),
            "order_id": None,
        }

        try:
            if pos["side"] == "LONG":
                order_id = self.buy(
                    self.config.connector_name,
                    pair,
                    amount,
                    OrderType.MARKET,
                    mid * Decimal("1.01"),
                    PositionAction.OPEN,
                )
            else:
                order_id = self.sell(
                    self.config.connector_name,
                    pair,
                    amount,
                    OrderType.MARKET,
                    mid * Decimal("0.99"),
                    PositionAction.OPEN,
                )
            self._our_mirrors[pair]["order_id"] = order_id
            if order_id:
                self._pending_order_ids.add(order_id)
        except Exception as e:
            self.logger().error(f"Order placement failed for {pair}: {e}")
            # Mirror still registered so close will trigger when trader closes

        # ── Trade log: our copy (red) ─────────────────────────────────────
        trade_log(
            f"{RED}{BOLD}"
            f"{_now():<22} {'WE COPY':<14} {'OPENED':<8} {pos['side']:<6} {coin:<7} "
            f"{float(amount):>10.5f}  ${float(mid):>11,.2f}  "
            f"margin=${float(allocation):.2f}  {leverage}x  notional=${float(notional):.2f}"
            f"{RESET}"
        )

    def _close_mirror(self, pair: str, reason: str = ""):
        if pair not in self._our_mirrors:
            return

        mirror = self._our_mirrors.pop(pair)
        amount = mirror["amount"]
        side = mirror["side"]
        coin = mirror["coin"]
        entry_price = mirror.get("entry_price", Decimal("0"))
        entry_time = mirror.get("entry_time", "?")

        mid = self._get_mid_price(pair) or Decimal("1")

        # Calculate PnL
        pnl_str = ""
        if entry_price and entry_price > 0 and mid > 0:
            if side == "LONG":
                pnl_pct = (float(mid) - float(entry_price)) / float(entry_price) * 100 * self.config.leverage
            else:
                pnl_pct = (float(entry_price) - float(mid)) / float(entry_price) * 100 * self.config.leverage
            pnl_str = f"  PnL~{pnl_pct:+.2f}%"

        self.logger().info(f"Closing mirror {side} {amount} {pair} @ ~${mid:,.2f}{pnl_str}")

        # ── Trade log: our close (red) ────────────────────────────────────
        trade_log(
            f"{RED}{BOLD}"
            f"{_now():<22} {'WE CLOSE':<14} {'CLOSED':<8} {side:<6} {coin:<7} "
            f"{float(amount):>10.5f}  ${float(mid):>11,.2f}  "
            f"entry=${float(entry_price):,.2f}  opened={entry_time}{pnl_str}"
            f"{RESET}"
        )
        trade_log("─" * 80)

        if side == "LONG":
            order_id = self.sell(
                self.config.connector_name,
                pair,
                amount,
                OrderType.MARKET,
                mid * Decimal("0.99"),
                PositionAction.CLOSE,
            )
        else:
            order_id = self.buy(
                self.config.connector_name,
                pair,
                amount,
                OrderType.MARKET,
                mid * Decimal("1.01"),
                PositionAction.CLOSE,
            )

        if order_id:
            self._pending_order_ids.add(order_id)

    # ── Status display ────────────────────────────────────────────────────────

    def format_status(self) -> str:
        lines = [
            "",
            "── HL Copy Trader ─────────────────────────────────────────────",
            f"Traders: {len(self.config.trader_addresses)}  |  "
            f"Leverage: {self.config.leverage}x  |  "
            f"Alloc: {self.config.allocation_pct*100:.0f}% balance  |  "
            f"Poll: {self.config.poll_interval_seconds}s",
            "",
        ]

        for i, addr in enumerate(self.config.trader_addresses, 1):
            positions = self._trader_positions.get(addr, {})
            short = addr[:10] + "..." + addr[-6:]
            if positions:
                lines.append(f"Trader #{i} ({short}):")
                for coin, p in positions.items():
                    mirror_tag = " [mirrored]" if p.get("pair") in self._our_mirrors else ""
                    lines.append(
                        f"  {p['side']:<5} {p['size']:>10.4f} {coin:<6} "
                        f"@ ${p['entry_px']:>10,.2f}{mirror_tag}"
                    )
            else:
                lines.append(f"Trader #{i} ({short}): no open positions")

        lines.append("")
        lines.append("Our mirrors:")
        if self._our_mirrors:
            bal = self._get_free_balance()
            for pair, m in self._our_mirrors.items():
                mid = self._get_mid_price(pair) or Decimal("0")
                pnl_str = ""
                if mid > 0 and m["side"] == "LONG":
                    try:
                        entry_px = self._trader_positions[m["trader"]][m["coin"]]["entry_px"]
                        pnl_pct = (float(mid) - entry_px) / entry_px * 100 * self.config.leverage
                        pnl_str = f"  PnL ~ {pnl_pct:+.2f}%"
                    except Exception:
                        pass
                lines.append(
                    f"  {m['side']:<5} {m['amount']:>10} {pair:<12} "
                    f"from trader {m['trader'][:10]}...{pnl_str}"
                )
        else:
            lines.append("  (none)")

        lines.append("─" * 63)
        return "\n".join(lines)
