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
hl_copy_trader.py
=================
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

import json
import math
import os
import sys
import time
import requests
from collections import deque
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Dict, List, Optional

# Dashboard writes these files (shared ./conf volume) for manual actions.
COPY_BOT_ID = "hummingbot-hl"
FORCE_CLOSE_FILE = "/home/hummingbot/conf/force_close_hl_copy.json"
FORCE_FOLLOW_FILE = "/home/hummingbot/conf/force_follow_hl_copy.json"
FORCE_SCALE_FILE = "/home/hummingbot/conf/force_scale_hl_copy.json"
FORCE_FULL_BTC_FILE = "/home/hummingbot/conf/force_full_btc_hl_copy.json"
FORCE_FULL_ALLIN_FILE = "/home/hummingbot/conf/force_full_allin_hl_copy.json"
FORCE_ADD_ETH_FILE = "/home/hummingbot/conf/force_add_eth_hl_copy.json"
FORCE_RISK_TOGGLE_FILE = "/home/hummingbot/conf/force_risk_toggle_hl_copy.json"
FORCE_DROP_MARKETS_FILE = "/home/hummingbot/conf/force_drop_markets_hl_copy.json"
# Per-coin risk auto-stop overrides (dashboard "Auto-stop" button). Persists.
RISK_OVERRIDES_FILE = "/home/hummingbot/conf/hl_risk_overrides.json"
# Dashboard runtime flags (persists across restarts; overrides yml until changed again)
RUNTIME_FLAGS_FILE = "/home/hummingbot/conf/hl_copy_runtime.json"
STATUS_FILE = "/home/hummingbot/conf/hl_copy_status.json"
# Coins owned by the HL swing bot on this same account (BTC/ETH).
RESERVED_COINS_FILE = "/home/hummingbot/conf/hl_reserved_coins.json"

# SDK for HIP-3 / xyz-dex commodity trading — imported lazily inside _init_sdk()
_SDK_AVAILABLE = False
_SDK_WARN_LOGGED = False
HLExchange = None
HLInfo = None
HL_CONSTANTS = None

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, PositionAction, TradeType
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
if "/home/hummingbot/scripts" not in sys.path:
    sys.path.insert(0, "/home/hummingbot/scripts")

try:
    from bot_sl_override import effective_sl_roe, override_sl_roe
except ImportError:
    def effective_sl_roe(bot_id, default=20.0):
        return float(default)

    def override_sl_roe(bot_id):
        return None

SL_FILE = "/home/hummingbot/conf/bot_sl.json"
SL_BLOCK_FILE = "/home/hummingbot/conf/hl_copy_sl_block.json"


def _live_sl_roe(default=20.0):
    """Home SL % from bot_sl.json / SQL. Always a number (default 20)."""
    try:
        with open(SL_FILE, "r") as f:
            row = (json.load(f) or {}).get("hummingbot-hl") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        v = override_sl_roe("hummingbot-hl")
        if v is not None:
            v = float(v)
            if 1.0 <= v <= 90.0:
                return v
    except Exception:
        pass
    return float(default)


try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default


def _ctrl(key, default):
    return _ctrl_live("hummingbot-hl", key, default)

# ── Trade log ─────────────────────────────────────────────────────────────────

TRADE_LOG_PATH = "/home/hummingbot/logs/copy_trades.log"

# ANSI colours (stripped automatically by viewers that don't support them)
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[33m"
CYAN   = "\033[96m"
PURPLE = "\033[95m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

# Trader colours: light blue → blue → dark blue
TRADER_COLORS = [
    "\033[96m",   # Trader #1 — light blue (bright cyan)
    "\033[94m",   # Trader #2 — blue (bright blue)
    "\033[34m",   # Trader #3 — dark blue
]


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
    "TIA":   "TIA-USD",
    "SEI":   "SEI-USD",
    "JUP":   "JUP-USD",
    "kPEPE": "kPEPE-USD",
    "kBONK": "kBONK-USD",
    "ENA":   "ENA-USD",
    "HYPE":  "HYPE-USD",
    "WLFI":  "WLFI-USD",
    "IOTA":  "IOTA-USD",
    "LTC":   "LTC-USD",
    "BCH":   "BCH-USD",
    "STRK":  "STRK-USD",
    "NEAR":  "NEAR-USD",
    "DOT":   "DOT-USD",
    "ATOM":  "ATOM-USD",
    "AAVE":  "AAVE-USD",
    "UNI":   "UNI-USD",
    "MEGA":  "MEGA-USD",
    "TAO":   "TAO-USD",
    "KAITO": "KAITO-USD",
    "PUMP":     "PUMP-USD",
    "FARTCOIN": "FARTCOIN-USD",
    "ZEC":      "ZEC-USD",
    "MON":      "MON-USD",
    "PURR":     "PURR-USD",
    "LIT":      "LIT-USD",
    "CHIP":     "CHIP-USD",
    "XPL":      "XPL-USD",
    "ZEREBRO":  "ZEREBRO-USD",
    "PENGU":    "PENGU-USD",
    "POPCAT":   "POPCAT-USD",
    "ZRO":      "ZRO-USD",
    "DYDX":     "DYDX-USD",
    "ETHFI":    "ETHFI-USD",
    "ALGO":     "ALGO-USD",
    "ADA":      "ADA-USD",
    "HBAR":     "HBAR-USD",
    "XMR":      "XMR-USD",
    "STABLE":   "STABLE-USD",
    "VVV":      "VVV-USD",
}

# Minimal order-book seed at startup. Extra pairs are subscribed on demand when a
# leader opens them — avoids the 25–40 pair WS re-subscribe storms that caused 504s.
CORE_MARKETS = {
    "BTC-USD",
}
# Hard cap on live HL order books. Leaders dumping 10–20 alts used to subscribe
# every coin; connector.ready then waited on ALL books and 429-stalled for hours.
MAX_SUBSCRIBED_MARKETS = 8
# After a local close, keep the book this long in case the leader flips back.
# Then unsubscribe — idle books burn metaAndAssetCtxs (weight 20) on this IP.
IDLE_MARKET_GRACE_S = 45.0


# ── Config ────────────────────────────────────────────────────────────────────

class HLCopyTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    min_leverage: int = Field(4)             # floor: always use at least this leverage
    max_leverage: int = Field(10)            # ceiling: never exceed this leverage
    allocation_pct: float = Field(1.00)      # fraction of each position slot to use (1.0 = full 1/3 of trader pot)
    # Legacy name: used only if max_positions_per_trader not set in older configs — prefer max_positions_per_trader
    min_open_positions: int = Field(3)       # kept for yml compat; sizing uses max_positions_per_trader
    # Equal pots: equity ÷ N traders, then each trader may hold up to this many mirrors,
    # each sized at (trader_pot / max_positions_per_trader).
    max_positions_per_trader: int = Field(3)
    poll_interval_seconds: int = Field(5)
    copy_shorts: bool = Field(True)
    # Cap ZEC SHORT notional in USD. 0 = no extra cap (normal pot sizing).
    max_zec_short_usd: float = Field(0)
    trader_addresses: List[str] = Field(default_factory=list)
    # Optional per-address override of max_positions_per_trader
    trader_max_positions: Dict[str, int] = Field(default_factory=dict)
    commodity_allocation_pct: float = Field(0.20)   # 20% of total balance reserved for commodity (xyz) positions
    commodity_max_positions: int = Field(2)          # max simultaneous commodity positions
    commodity_blocked_coins: List[str] = Field(default_factory=list)  # xyz coins to never trade
    # Extra pairs to subscribe at startup (beyond CORE_MARKETS). Prefer empty —
    # pairs are added dynamically when leaders open them.
    core_markets: List[str] = Field(default_factory=list)
    # Max simultaneous HL order books (mirrors + seed). Extra leader coins are
    # ranked by size, not subscribed. Random picks churn; we keep existing first.
    max_subscribed_markets: int = Field(8)
    # Close our position when no tracked trader holds the same coin+side (roadmap P3)
    close_orphans: bool = Field(True)
    # Single HL account: only one open mirror per asset. If trader A already
    # owns our BTC long, trader B opening BTC must not open/close/flip it.
    one_position_per_asset: bool = Field(True)

    # ── Risk management (margin trading) ──────────────────────────────────
    # Close a mirror when its unrealized USD loss ≥ this fraction of total equity.
    # Default 1/3: one bad trade cannot burn more than a third of the account.
    max_position_loss_pct: float = Field(0.333)
    # Never put more than this fraction of equity as margin into a single open.
    max_margin_per_position_pct: float = Field(0.333)
    # Block new opens when free margin < this fraction of equity (cover first).
    min_free_margin_pct: float = Field(0.15)
    # Size pots/allocations on equity WITHOUT positive unrealized PnL so paper
    # profits cannot fund larger new positions until realized (closed).
    size_exclude_unrealized_profit: bool = Field(True)
    # Master switch — set false to disable all automated risk closes/gates.
    risk_management_enabled: bool = Field(True)
    # Coins managed by another bot on this account (HL swing: BTC/ETH).
    # Copy trader will not open, close, flip, or orphan-manage these.
    reserved_coins: List[str] = Field(default_factory=list)


# ── Strategy ──────────────────────────────────────────────────────────────────

class HLCopyTrader(StrategyV2Base):
    """
    Copy-trades up to 3 Hyperliquid wallet addresses by polling their
    open perpetual positions and mirroring opens/closes on our own account.
    """

    connectors: Dict
    config: HLCopyTraderConfig

    def __init__(self, connectors: Dict, config: HLCopyTraderConfig):
        super().__init__(connectors, config)

        self._last_poll: float = 0.0

        # coin → max leverage allowed by HL (fetched once at startup from /info meta)
        self._hl_max_leverage: Dict[str, int] = {}

        # trader_address → {coin: {"side": "LONG"|"SHORT", "size": float, "entry_px": float}}
        self._trader_positions: Dict[str, Dict[str, dict]] = {
            addr: {} for addr in config.trader_addresses
        }

        # pair → {"trader": addr, "coin": str, "side": "LONG"|"SHORT",
        #          "amount": Decimal, "order_id": str|None}
        self._our_mirrors: Dict[str, dict] = {}

        self._pending_order_ids: set = set()

        # Close-order retry tracking: order_id → {pair, mirror, attempts}
        # Used by did_fail_order to retry failed close orders with higher slippage.
        self._pending_closes: Dict[str, dict] = {}

        # 429 rate-limit backoff: skip all HL REST calls until this timestamp
        self._api_backoff_until: float = 0.0
        # subAccounts is weight 20. Cache the address list (rarely changes)
        # and poll each sub with clearinghouseState (weight 2).
        self._subacct_cache: Dict[str, tuple] = {}
        self._subacct_ttl_s: float = 300.0

        # HL API rate-limit tracking
        # HL info endpoint limit: ~1200 req/min (20 req/sec)
        # We track timestamps of our own REST calls in a rolling 60s window.
        self._hl_call_timestamps: deque = deque()   # timestamps of our REST calls
        self._HL_RATE_LIMIT = 1200                  # HL documented limit per minute
        self._HL_WARN_THRESHOLD = 0.70              # warn at 70% usage

        # Traders that have completed their first snapshot poll.
        # On the very first poll we only record positions — no new mirrors opened.
        self._initialized_traders: set = set()

        # Track positions that were skipped (no balance / unsupported) so we
        # never enter them late once the trader already has them open.
        self._skipped_positions: Dict[str, set] = {
            addr: set() for addr in config.trader_addresses
        }

        # Opens deferred while connector was not ready: address → {coin: pos}
        # Retried each poll without re-logging TRADER OPENED.
        self._deferred_opens: Dict[str, Dict[str, dict]] = {
            addr: {} for addr in config.trader_addresses
        }
        self._not_ready_since: Optional[float] = None
        self._last_defer_log: float = 0.0

        # Dynamic market subscription (roadmap P2)
        self._pair_ensure_pending: set = set()
        self._pair_ensure_failed: set = set()
        self._last_market_prune: float = 0.0
        self._pair_last_needed: Dict[str, float] = {}

        # Orphan audit (roadmap P3): run a loud startup pass once all leaders fetched
        self._startup_orphan_audit_done: bool = False
        # Stop DASH-style close storms: don't re-close / log CLOSED every tick
        self._close_backoff_until: Dict[str, float] = {}
        self._close_in_flight: set = set()
        self._flip_storm: Dict[str, list] = {}

        # HIP-3 fill tracking: last fill timestamp seen per address (ms)
        self._hip3_last_fill_ts: Dict[str, int] = {
            addr: int(time.time() * 1000) for addr in config.trader_addresses
        }
        # Dedup: set of (address, coin, time_ms) tuples already logged
        self._hip3_seen: set = set()

        # ── Commodity (xyz dex) mirrors ───────────────────────────────────
        # coin → {"side": str, "amount": float, "entry_px": float, "entry_time": str}
        self._commodity_mirrors: Dict[str, dict] = {}
        # SDK objects — initialized lazily on first tick once connector is ready
        self._sdk_exchange: Optional[object] = None
        self._sdk_info: Optional[object] = None
        self._sdk_ready: bool = False

        # Runtime flags from dashboard (copy_shorts override, unfollowed traders, etc.)
        self._runtime_copy_shorts: Optional[bool] = None
        self._runtime_poll_interval: Optional[float] = None
        self._unfollowed_traders: set = set()  # 1-based trader numbers, stop new copies
        self._trader_follow_slots: Dict[int, int] = {}  # T# → 1|2|3 trades to follow
        self._runtime_flags_mtime: float = 0.0
        self._risk_disabled_coins: set = set()  # coins with risk auto-stop OFF
        self._risk_overrides_mtime: float = 0.0
        self._sl_block: Dict[str, dict] = {}
        self._load_sl_block()
        self._load_runtime_flags(force=True)
        try:
            self._load_risk_overrides(force=True)
        except Exception:
            pass

        # Full all-in emergency: close Copy mirrors → LONG/SHORT {coin} with Copy wallet
        # phase: None | "closing" | "opening"
        self._full_allin_phase: Optional[str] = None
        self._full_allin_coin: str = "BTC"
        self._full_allin_side: str = "LONG"
        self._full_allin_leverage_pct: float = 100.0  # % of HL max for that coin
        self._full_allin_started_at: float = 0.0
        self._full_allin_flat_since: Optional[float] = None
        # legacy aliases used by status / open-block
        self._full_btc_phase: Optional[str] = None
        try:
            with open(STATUS_FILE, "r") as f:
                _st = json.load(f) or {}
            _ph = str(_st.get("full_allin_phase") or _st.get("full_btc_phase") or "")
            if _ph in ("closing", "opening"):
                self._full_allin_phase = _ph
                self._full_btc_phase = _ph
                self._full_allin_coin = str(_st.get("full_allin_coin") or "BTC").upper()
                side = str(_st.get("full_allin_side") or "LONG").upper()
                self._full_allin_side = "SHORT" if side == "SHORT" else "LONG"
                self._full_allin_started_at = time.time()
                self._close_in_flight.clear()
                self._close_backoff_until.clear()
                self.logger().info(
                    f"[force-full-allin] resume {_ph} {self._full_allin_coin} from status"
                )
        except Exception:
            pass

        seed = self.markets.get(config.connector_name, set())
        self.logger().info("=" * 60)
        self.logger().info("HL Copy Trader started")
        self.logger().info(f"  Traders  : {len(config.trader_addresses)}")
        self.logger().info(f"  Leverage : trader leverage clamped to {config.min_leverage}x–{config.max_leverage}x")
        n_tr = max(1, len(config.trader_addresses))
        mpp = max(1, int(getattr(config, "max_positions_per_trader", None) or config.min_open_positions or 3))
        self.logger().info(
            f"  Funds    : equity ÷ {n_tr} trader pots; each ≤{mpp} pos @ 1/{mpp} pot "
            f"(alloc={config.allocation_pct*100:.0f}% of slot)"
        )
        self.logger().info(f"  Poll     : every {config.poll_interval_seconds}s")
        self.logger().info(
            f"  Shorts   : {'yes' if self._allow_shorts() else 'NO — longs only'} "
            f"(yml={config.copy_shorts}"
            f"{'' if self._runtime_copy_shorts is None else f', runtime={self._runtime_copy_shorts}'})"
            f"  max ZEC short ${self._max_zec_short_usd():.0f} ntl"
        )
        self.logger().info(f"  Commodity: {config.commodity_allocation_pct*100:.0f}%  max {config.commodity_max_positions} (xyz)")
        self.logger().info(f"  Markets  : {len(seed)} seed pairs (dynamic subscribe for the rest): {sorted(seed)}")
        self.logger().info(f"  Orphans  : {'auto-close ON' if config.close_orphans else 'auto-close OFF'}")
        reserved = sorted(self._reserved_coins())
        self.logger().info(
            f"  Reserved : {reserved or 'none'} (swing bot — copy will not open/close these)"
        )
        self.logger().info(
            f"  Asset lock: {'ON — one position per coin (no multi-trader interference)' if config.one_position_per_asset else 'OFF'}"
        )
        if getattr(config, "risk_management_enabled", True):
            self.logger().info(
                f"  Risk     : max loss/pos={float(config.max_position_loss_pct)*100:.0f}% equity | "
                f"max margin/pos={float(config.max_margin_per_position_pct)*100:.0f}% equity | "
                f"min free={float(config.min_free_margin_pct)*100:.0f}% equity | "
                f"size excl. +uPnL={'ON' if config.size_exclude_unrealized_profit else 'OFF'}"
            )
        else:
            self.logger().info("  Risk     : OFF")
        for i, addr in enumerate(config.trader_addresses, 1):
            self.logger().info(f"  Trader {i} : {addr}")
        self.logger().info("=" * 60)

        # ── Trade log banner ──────────────────────────────────────────────
        sep = "═" * 80
        trade_log(f"\n{sep}")
        trade_log(f"{BOLD}{CYAN}  HL COPY TRADER  —  started {_now()}{RESET}")
        n_tr = max(1, len(config.trader_addresses))
        mpp = max(1, int(getattr(config, "max_positions_per_trader", None) or config.min_open_positions or 3))
        trade_log(
            f"  Max Leverage : {config.max_leverage}x  |  "
            f"Pots: equity/{n_tr} traders × {mpp} pos  |  "
            f"Shorts : {'YES' if self._allow_shorts() else 'NO (longs only)'}"
        )
        for i, addr in enumerate(config.trader_addresses, 1):
            trade_log(f"  Trader #{i} : {addr}")
        trade_log(sep)
        trade_log(f"{'TIME (UTC)':<22} {'WHO':<14} {'ACTION':<8} {'SIDE':<6} {'COIN':<7} {'SIZE':>10}  {'PRICE':>12}  NOTE")
        trade_log("─" * 80)

    @classmethod
    def init_markets(cls, config: HLCopyTraderConfig):
        # Small seed only. Extra pairs are added via connector.add_trading_pair()
        # when a leader opens them — keeps WS/order-book load low (roadmap P2).
        seed = set()
        for p in (config.core_markets or []):
            if p:
                seed.add(p if "-" in p else f"{p}-USD")
        if not seed:
            seed = set(CORE_MARKETS)
        cls.markets = {config.connector_name: seed}

    # ── Dynamic markets (roadmap P2) ─────────────────────────────────────────

    def _registered_pairs(self) -> set:
        return self.markets.setdefault(self.config.connector_name, set())

    def _pair_has_book(self, pair: str) -> bool:
        """True if connector already has a usable mid/order book for pair."""
        connector = self.connectors.get(self.config.connector_name)
        if connector is None:
            return False
        try:
            tracker = getattr(connector, "order_book_tracker", None)
            if tracker is not None and pair in getattr(tracker, "order_books", {}):
                return True
        except Exception:
            pass
        try:
            mid = connector.get_mid_price(pair)
            if mid is not None and float(mid) > 0:
                return True
        except Exception:
            pass
        return False

    def _ensure_pair_ready(self, pair: str) -> bool:
        """
        Ensure the connector tracks `pair`. Returns True when an order book /
        mid price is available so we can place market orders.

        If the pair is not yet subscribed, kicks off an async add and returns
        False so the open is deferred to a later poll.
        """
        if not pair:
            return False
        registered = self._registered_pairs()
        if pair in registered and self._pair_has_book(pair):
            return True
        if self._pair_has_book(pair):
            registered.add(pair)
            return True
        if pair in self._pair_ensure_failed:
            return False
        if pair in self._pair_ensure_pending:
            return False
        wanted = self._wanted_pairs()
        if len(self._registered_pairs()) >= self._max_subscribed() and pair not in wanted:
            self._prune_idle_markets(force=True)
        if len(self._registered_pairs()) >= self._max_subscribed() and pair not in wanted:
            now = time.time()
            last = getattr(self, "_cap_log_ts", {}) or {}
            if now - float(last.get(pair) or 0) >= 60:
                last[pair] = now
                self._cap_log_ts = last
                self.logger().info(
                    f"[markets] Cap {self._max_subscribed()} books — not subscribing {pair}"
                )
            return False
        # Don't pile more books onto a stalled connector unless we already hold it.
        if not self._connector_is_ready() and pair not in wanted:
            return False

        self._pair_ensure_pending.add(pair)
        self._touch_pair(pair)
        self.logger().info(f"[markets] Subscribing order book for {pair} (dynamic)…")
        safe_ensure_future(self._add_pair_async(pair))
        return False

    async def _add_pair_async(self, pair: str):
        connector = self.connectors.get(self.config.connector_name)
        try:
            if connector is None:
                self._pair_ensure_failed.add(pair)
                return
            add_fn = getattr(connector, "add_trading_pair", None)
            if add_fn is None:
                self.logger().warning(
                    f"[markets] Connector has no add_trading_pair — cannot subscribe {pair}"
                )
                self._pair_ensure_failed.add(pair)
                return
            ok = await add_fn(pair)
            if ok:
                self._registered_pairs().add(pair)
                self._pair_ensure_failed.discard(pair)
                self.logger().info(
                    f"[markets] Ready: {pair}  "
                    f"(active markets: {len(self._registered_pairs())})"
                )
            else:
                self._pair_ensure_failed.add(pair)
                self.logger().warning(f"[markets] add_trading_pair returned False for {pair}")
        except Exception as e:
            self._pair_ensure_failed.add(pair)
            self.logger().warning(f"[markets] Failed to add {pair}: {e}")
        finally:
            self._pair_ensure_pending.discard(pair)

    def _sync_coin_map_from_meta(self, meta: dict):
        """Auto-map any HL main-dex coin not already in COIN_TO_PAIR."""
        added = 0
        for asset in meta.get("universe", []):
            coin = asset.get("name") or ""
            if not coin or ":" in coin:
                continue
            if coin not in COIN_TO_PAIR:
                COIN_TO_PAIR[coin] = f"{coin}-USD"
                added += 1
        if added:
            self.logger().info(
                f"[markets] Extended COIN_TO_PAIR with {added} coins from HL meta "
                f"(total {len(COIN_TO_PAIR)})"
            )

    # ── HL REST wrapper with rate-limit tracking ─────────────────────────────

    def _hl_post(self, payload: dict, timeout: int = 6) -> dict:
        """
        Wrapper around every REST POST to HL info endpoint.
        Tracks call timestamps so we can calculate our rolling req/min rate
        and warn before hitting the 1200 req/min limit.
        """
        now = time.time()

        # Evict timestamps older than 60 seconds
        while self._hl_call_timestamps and self._hl_call_timestamps[0] < now - 60:
            self._hl_call_timestamps.popleft()

        # Warn on rolling call count. Weight is tracked in shared hl_ip_budget
        # (subAccounts=20, clearinghouseState=2, 1200 weight/min/IP).
        calls_last_minute = len(self._hl_call_timestamps)
        warn_at = int(self._HL_RATE_LIMIT * self._HL_WARN_THRESHOLD)
        if calls_last_minute >= warn_at:
            pct = calls_last_minute / self._HL_RATE_LIMIT * 100
            self.logger().warning(
                f"[rate-limit] HL API usage: {calls_last_minute}/{self._HL_RATE_LIMIT} req/min "
                f"({pct:.0f}%) — approaching limit!"
            )

        self._hl_call_timestamps.append(now)
        try:
            from hl_ip_budget import record as _hl_ip_record
            _hl_ip_record(payload, src="copy")
        except Exception:
            pass
        r = requests.post(HL_INFO_URL, json=payload, timeout=timeout)
        if r.status_code == 429:
            try:
                from hl_ip_budget import record as _hl_ip_record
                _hl_ip_record(payload, src="copy", is_429=True, weight=0)
            except Exception:
                pass
        r.raise_for_status()
        return r.json()

    def _hl_api_usage(self) -> str:
        """Return a human-readable summary of our current HL API usage rate."""
        now = time.time()
        while self._hl_call_timestamps and self._hl_call_timestamps[0] < now - 60:
            self._hl_call_timestamps.popleft()
        count = len(self._hl_call_timestamps)
        pct = count / self._HL_RATE_LIMIT * 100
        return f"{count}/{self._HL_RATE_LIMIT} req/min ({pct:.0f}%)"

    # ── HL max leverage per coin ──────────────────────────────────────────────

    def _fetch_hl_max_leverage(self):
        """Fetch HL's per-coin max leverage from the meta endpoint and cache it."""
        try:
            data = self._hl_post({"type": "meta"})
            self._sync_coin_map_from_meta(data)
            for asset in data.get("universe", []):
                coin = asset.get("name", "")
                lev = asset.get("maxLeverage")
                if coin and lev:
                    self._hl_max_leverage[coin] = int(lev)
            self.logger().info(
                f"[leverage] Loaded max leverage for {len(self._hl_max_leverage)} coins from HL"
            )
        except Exception as e:
            self.logger().warning(f"[leverage] Could not fetch HL max leverage: {e}")

    def _hl_raw_max_leverage_for(self, coin: str) -> int:
        """
        Hyperliquid's true max leverage for a coin (NOT capped by config.max_leverage).
        Refreshes meta cache if this coin is missing.
        """
        if coin not in self._hl_max_leverage:
            try:
                self._fetch_hl_max_leverage()
            except Exception:
                pass
        hl_max = self._hl_max_leverage.get(coin)
        if hl_max and int(hl_max) > 0:
            return int(hl_max)
        # Fallback if meta unavailable — do not invent high lev blindly
        return max(1, int(_ctrl("max_leverage", self.config.max_leverage or 20)))

    def _max_leverage_for(self, coin: str) -> int:
        """Return HL's max leverage for a coin, capped by config.max_leverage (normal copies)."""
        hl_max = self._hl_raw_max_leverage_for(coin)
        cap = int(_ctrl("max_leverage", self.config.max_leverage or 20))
        return min(hl_max, max(1, cap))

    # ── Restore existing positions on startup ────────────────────────────────

    def _restore_our_positions(self):
        """On startup, check our own account for open positions and register them in _our_mirrors.

        Ownership is left as 'restored' until orphan audit matches a leader or closes it.
        """
        try:
            connector = self.connectors[self.config.connector_name]
            account_positions = connector.account_positions
            if not account_positions:
                self.logger().info("[restore] No open positions on our account")
                return
            for pos_key, pos in account_positions.items():
                pair = pos.trading_pair if hasattr(pos, "trading_pair") else str(pos_key)
                coin = pair.replace("-USD", "")
                amount = abs(Decimal(str(pos.amount))) if hasattr(pos, "amount") else Decimal("0")
                side = "LONG" if (hasattr(pos, "amount") and pos.amount > 0) else "SHORT"
                entry_px = Decimal(str(pos.entry_price)) if hasattr(pos, "entry_price") else Decimal("0")
                if amount > 0 and pair not in self._our_mirrors:
                    if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
                        self.logger().info(
                            f"[restore] Skip {side} {amount} {pair} — reserved for HL swing"
                        )
                        continue
                    # Best-effort leverage from connector position object
                    lev = None
                    for attr in ("leverage", "leverage_level"):
                        if hasattr(pos, attr) and getattr(pos, attr):
                            try:
                                lev = int(getattr(pos, attr))
                            except (TypeError, ValueError):
                                pass
                    self._our_mirrors[pair] = {
                        "trader": "restored",  # matched or closed by orphan audit
                        "coin": coin,
                        "pair": pair,
                        "side": side,
                        "amount": amount,
                        "entry_price": entry_px,
                        "leverage": lev if lev and lev > 0 else None,
                        "entry_time": "restored",
                        "order_id": None,
                    }
                    # Ensure restored pairs stay subscribed for pricing / close
                    self._ensure_pair_ready(pair)
                    self.logger().info(
                        f"[restore] Found existing {side} {amount} {pair} — "
                        f"tracked; orphan audit will keep or close"
                    )
                    trade_log(
                        f"{YELLOW}[restore] Existing {side} {amount} {coin} @ "
                        f"${float(entry_px):,.4f} — tracked (orphan audit pending){RESET}"
                    )
            self._rehydrate_manual_flags_from_status()
        except Exception as e:
            self.logger().warning(f"Position restore failed: {e}")

    # ── SDK init (commodity trading) ─────────────────────────────────────────

    def _init_sdk(self):
        """Initialize hyperliquid-python-sdk using the same wallet as the connector."""
        global _SDK_AVAILABLE, _SDK_WARN_LOGGED, HLExchange, HLInfo, HL_CONSTANTS
        if not _SDK_AVAILABLE:
            try:
                from hyperliquid.exchange import Exchange as _HLExchange
                from hyperliquid.info import Info as _HLInfo
                from hyperliquid.utils import constants as _HL_CONSTANTS
                HLExchange = _HLExchange
                HLInfo = _HLInfo
                HL_CONSTANTS = _HL_CONSTANTS
                _SDK_AVAILABLE = True
            except ImportError as e:
                if not _SDK_WARN_LOGGED:
                    self.logger().warning(f"[commodity] hyperliquid SDK not installed — commodity trading disabled ({e})")
                    _SDK_WARN_LOGGED = True
                return
        try:
            connector = self.connectors[self.config.connector_name]
            wallet = connector.authenticator.wallet
            # account_address = the main HL account (may differ from wallet in API-wallet mode)
            account_address = connector.hyperliquid_perpetual_address
            self._sdk_info = HLInfo(HL_CONSTANTS.MAINNET_API_URL, skip_ws=True, perp_dexs=["", "xyz"])
            self._sdk_exchange = HLExchange(
                wallet, HL_CONSTANTS.MAINNET_API_URL,
                perp_dexs=["", "xyz"],
                account_address=account_address,
            )
            self._sdk_ready = True
            self.logger().info(
                f"[commodity] SDK ready — account {account_address[:10]}...  "
                f"api_wallet {wallet.address[:10]}...  xyz dex enabled"
            )
        except Exception as e:
            self.logger().warning(f"[commodity] SDK init failed: {e}")

    def _commodity_balance(self) -> float:
        """Return the USD budget reserved for commodity positions."""
        try:
            connector = self.connectors[self.config.connector_name]
            total = float(connector.get_available_balance("USD") or 0)
            return total * self.config.commodity_allocation_pct
        except Exception:
            return 0.0

    def _commodity_slot(self) -> float:
        """Per-position budget inside the commodity pool."""
        pool = self._commodity_balance()
        slots = max(1, self.config.commodity_max_positions - len(self._commodity_mirrors))
        return pool / slots

    def _get_xyz_price(self, coin: str) -> float:
        """Fetch latest price for a xyz: coin via candleSnapshot."""
        try:
            start = int(time.time() * 1000) - 120_000
            candles = self._hl_post(
                {"type": "candleSnapshot",
                 "req": {"coin": coin, "interval": "1m", "startTime": start}}
            )
            if candles:
                return float(candles[-1]["c"])
        except Exception:
            pass
        return 0.0

    def _open_commodity(self, coin: str, is_long: bool, ref_px: float):
        """Open a commodity mirror via SDK at 1x leverage, commodity slot size."""
        if not self._sdk_ready:
            return
        if coin in self._commodity_mirrors:
            return
        if coin in self.config.commodity_blocked_coins:
            self.logger().info(f"[commodity] {coin} is blocked — skipping")
            return
        if len(self._commodity_mirrors) >= self.config.commodity_max_positions:
            self.logger().info(f"[commodity] Max positions reached — skipping {coin}")
            return

        px = self._get_xyz_price(coin) or ref_px
        if px <= 0:
            self.logger().warning(f"[commodity] No price for {coin}")
            return

        slot = self._commodity_slot()
        if slot <= 0:
            self.logger().warning(f"[commodity] No budget for {coin}")
            return

        try:
            asset = self._sdk_info.name_to_asset(coin)
            sz_decimals = self._sdk_info.asset_to_sz_decimals.get(asset, 4)
        except Exception:
            sz_decimals = 4
        step = 10 ** (-sz_decimals)
        size = math.ceil(slot / px / step) * step
        size = round(size, sz_decimals)

        side_str = "LONG" if is_long else "SHORT"
        self.logger().info(f"[commodity] Opening {side_str} {coin}  size={size}  px~${px:.4f}  slot=${slot:.2f}")
        try:
            self._sdk_exchange.update_leverage(1, coin, is_cross=True)
            time.sleep(0.2)
            result = self._sdk_exchange.market_open(coin, is_long, size)
            statuses = result.get("response", {}).get("data", {}).get("statuses", [{}])
            if statuses and "filled" in statuses[0]:
                fill = statuses[0]["filled"]
                actual_px = float(fill.get("avgPx", px))
                self._commodity_mirrors[coin] = {
                    "side": side_str, "amount": size,
                    "entry_px": actual_px, "entry_time": _now(),
                }
                self.logger().info(f"[commodity] Opened {side_str} {coin} filled @ ${actual_px:.4f}")
                trade_log(
                    f"{RED}{BOLD}"
                    f"{_now():<22} {'WE COPY':<14} {'OPENED':<8} {side_str:<6} {coin.replace('xyz:',''):<12} "
                    f"{size:>10.6f}  ${actual_px:>12,.4f}  slot=${slot:.2f}  1x  [commodity]"
                    f"{RESET}"
                )
            else:
                err = statuses[0].get("error", str(result)) if statuses else str(result)
                self.logger().warning(f"[commodity] Open failed {coin}: {err}")
                trade_log(
                    f"{PURPLE}{BOLD}"
                    f"{_now():<22} {'WE COPY':<14} {'FAILED':<8} {side_str:<6} {coin.replace('xyz:',''):<12} "
                    f"error: {err}"
                    f"{RESET}"
                )
        except Exception as e:
            self.logger().error(f"[commodity] Open exception {coin}: {e}")

    def _close_commodity(self, coin: str):
        """Close a commodity mirror via SDK."""
        if not self._sdk_ready or coin not in self._commodity_mirrors:
            return
        mirror = self._commodity_mirrors.pop(coin)
        side = mirror["side"]
        entry_px = mirror["entry_px"]
        mid = self._get_xyz_price(coin) or entry_px
        pnl_pct = 0.0
        if entry_px > 0 and mid > 0:
            pnl_pct = (mid - entry_px) / entry_px * 100 if side == "LONG" else (entry_px - mid) / entry_px * 100

        self.logger().info(f"[commodity] Closing {side} {coin} @ ~${mid:.4f}  PnL~{pnl_pct:+.2f}%")
        try:
            result = self._sdk_exchange.market_close(coin)
            self.logger().info(f"[commodity] Close result: {result}")
            trade_log(
                f"{ORANGE}{BOLD}"
                f"{_now():<22} {'WE CLOSE':<14} {'CLOSED':<8} {side:<6} {coin.replace('xyz:',''):<12} "
                f"${mid:>12,.4f}  entry=${entry_px:,.4f}  PnL~{pnl_pct:+.2f}%  [commodity]"
                f"{RESET}"
            )
            trade_log("─" * 80)
        except Exception as e:
            # Put back so next tick retries
            self._commodity_mirrors[coin] = mirror
            self.logger().error(f"[commodity] Close failed {coin}: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _max_subscribed(self) -> int:
        try:
            n = int(_ctrl(
                "max_subscribed_markets",
                getattr(self.config, "max_subscribed_markets", None) or MAX_SUBSCRIBED_MARKETS,
            ))
        except (TypeError, ValueError):
            n = MAX_SUBSCRIBED_MARKETS
        return max(4, min(12, n))

    def _leader_notional(self, pos: dict) -> float:
        try:
            return abs(float(pos.get("size") or 0)) * abs(float(pos.get("entry_px") or 0))
        except (TypeError, ValueError):
            return 0.0

    def _select_copy_coins(self, address: str, new_pos: dict) -> set:
        """Which of this leader's coins to copy.

        Keep mirrors we already hold for this trader, then fill leftover slots
        with the largest remaining notionals. Not random — random would open
        A,B,C,D then swap to E,F,G,H next poll.
        """
        max_pos = self._max_positions_for_trader(address)
        held: List[tuple] = []
        fresh: List[tuple] = []
        for coin, pos in (new_pos or {}).items():
            pair = pos.get("pair")
            if not pair or self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
                continue
            if pos.get("side") == "SHORT" and not self._allow_shorts():
                continue
            ntl = self._leader_notional(pos)
            m = self._our_mirrors.get(pair) if pair else None
            owner = (m or {}).get("trader")
            if m and owner in (address, "restored", "reconciled", "orphan", ""):
                held.append((ntl, coin))
            else:
                fresh.append((ntl, coin))
        held.sort(reverse=True)
        fresh.sort(reverse=True)
        chosen = [c for _, c in held[:max_pos]]
        for _, c in fresh:
            if len(chosen) >= max_pos:
                break
            chosen.append(c)
        return set(chosen)

    def _touch_pair(self, pair: str) -> None:
        if pair:
            self._pair_last_needed[pair] = time.time()

    def _wanted_pairs(self) -> set:
        """Books we still need: a live (or closing) local trade, or an imminent open.

        Leader coins we are not mirroring are idle — keep them subscribed and
        the last-price REST loop burns weight-20 tokens on this IP.
        """
        wanted = set()
        for pair_key, m in (self._our_mirrors or {}).items():
            p = (m or {}).get("pair") or pair_key
            if p:
                wanted.add(p)
        for ctx in (getattr(self, "_pending_closes", {}) or {}).values():
            p = (ctx or {}).get("pair")
            if p:
                wanted.add(p)
        n_def = 0
        for dmap in (self._deferred_opens or {}).values():
            for pos in (dmap or {}).values():
                p = pos.get("pair")
                if p and p not in wanted:
                    wanted.add(p)
                    n_def += 1
                    if n_def >= 1:
                        break
            if n_def >= 1:
                break
        if getattr(self, "_full_allin_phase", None) in ("closing", "opening"):
            coin = str(getattr(self, "_full_allin_coin", None) or "BTC").strip().upper()
            if coin:
                wanted.add(f"{coin}-USD")
        # Keep the BTC seed book forever so we never unsub it during WS handshake.
        wanted.add("BTC-USD")
        # Books for empty follow slots (top-N on that leader, including subaccounts).
        for addr, posmap in (getattr(self, "_trader_positions", None) or {}).items():
            if not self._is_trader_followed(address=addr):
                continue
            held = sum(1 for m in (self._our_mirrors or {}).values() if m.get("trader") == addr)
            if held >= self._max_positions_for_trader(addr):
                continue
            for coin in self._select_copy_coins(addr, posmap):
                p = (posmap.get(coin) or {}).get("pair")
                if p:
                    wanted.add(p)
        cap = self._max_subscribed()
        if len(wanted) > cap:
            pinned = []
            if getattr(self, "_full_allin_phase", None):
                c = str(getattr(self, "_full_allin_coin", None) or "BTC").upper()
                pinned.append(f"{c}-USD")
            for pair_key, m in (self._our_mirrors or {}).items():
                p = (m or {}).get("pair") or pair_key
                if p and p not in pinned:
                    pinned.append(p)
            rest = [p for p in wanted if p not in pinned]
            wanted = set((pinned + rest)[:cap])
        return wanted

    async def _remove_pair_async(self, pair: str):
        connector = self.connectors.get(self.config.connector_name)
        try:
            rem = getattr(connector, "remove_trading_pair", None) if connector else None
            if rem:
                await rem(pair)
            self._registered_pairs().discard(pair)
            self.logger().info(f"[markets] Dropped idle book {pair}")
        except Exception as e:
            self.logger().warning(f"[markets] Drop {pair} failed: {e}")

    def _prune_idle_markets(self, force: bool = False):
        now = time.time()
        allin = getattr(self, "_full_allin_phase", None) in ("closing", "opening")
        ready_for = 0.0
        if getattr(self, "_ready_since", None):
            ready_for = now - float(self._ready_since)
        # Do not unsub during WS handshake — that races and logs ERROR/Failed to remove.
        if not force and not allin and ready_for < 30:
            return
        if not force and not allin and now - getattr(self, "_last_market_prune", 0) < 15:
            return
        self._last_market_prune = now
        wanted = self._wanted_pairs()
        for p in wanted:
            self._touch_pair(p)
        registered = set(self._registered_pairs())
        connector = self.connectors.get(self.config.connector_name)
        try:
            for p in list(getattr(connector, "trading_pairs", None) or []):
                if p:
                    registered.add(p)
                    self._registered_pairs().add(p)
        except Exception:
            pass
        overflow = len(registered) > self._max_subscribed()
        grace = 0.0 if force or overflow or allin else IDLE_MARKET_GRACE_S
        extras = []
        for p in list(registered):
            if p in wanted or p in self._pair_ensure_pending:
                continue
            age = now - float((self._pair_last_needed or {}).get(p) or 0)
            if age < grace:
                continue
            extras.append(p)
        if not wanted and extras:
            seed = "BTC-USD"
            if seed in extras and seed in registered:
                extras = [p for p in extras if p != seed]
        if not extras:
            return
        self.logger().info(
            f"[markets] Cleaning {len(extras)} idle books ({', '.join(extras)}) "
            f"— keep {sorted(wanted) or ['—']} (local trade / pending open)"
        )
        for p in extras:
            self._drop_market(p)

    def _idle_pairs(self) -> list:
        wanted = self._wanted_pairs()
        return sorted(p for p in self._registered_pairs() if p not in wanted)

    def _as_pair(self, coin_or_pair: str) -> str:
        s = str(coin_or_pair or "").strip().upper()
        if not s:
            return ""
        if s.endswith("-USD"):
            return s
        return f"{s}-USD"

    def _drop_market(self, pair: str) -> None:
        if not pair:
            return
        self._registered_pairs().discard(pair)
        self._pair_ensure_pending.discard(pair)
        self._pair_last_needed.pop(pair, None)
        safe_ensure_future(self._remove_pair_async(pair))

    def _standard_coin_cleanup(self, force: bool = False) -> None:
        """Standard routine: unsubscribe any book we are not trading.

        Runs every poll. Idle leader coins (PENDLE/SUI/ETH seed, …) otherwise
        sit on last-price REST and burn IP tokens. Does not close positions.
        """
        self._prune_idle_markets(force=force)

    def _sql_cmd(self, kinds):
        try:
            from matrix_store import ensure, command_next
            ensure()
            return command_next(COPY_BOT_ID, kinds=kinds)
        except Exception:
            return None

    def _sql_done(self, cmd, note=None):
        if not cmd:
            return
        try:
            from matrix_store import command_finish
            command_finish(cmd.get("id"), note=note)
        except Exception:
            pass

    def _sql_kv(self, key, value, source="copy-sl"):
        try:
            from matrix_store import ensure, kv_set
            ensure()
            kv_set(COPY_BOT_ID, key, value, source=source)
        except Exception as e:
            self.logger().warning(f"[sql] kv {key} failed: {e}")

    def _persist_copy_risk_sql(self):
        """Write Copy SL / lock / risk pack to SQLite so it survives file loss."""
        sl = float(_live_sl_roe())
        self._sql_kv("sl_roe_pct", sl, "copy-sl")
        self._sql_kv("sl_block", dict(getattr(self, "_sl_block", None) or {}), "copy-sl")
        self._sql_kv("sl_always", True, "copy-sl")
        self._sql_kv("sl_no_recopy_until_leader_close", True, "copy-sl")
        cfg = self.config
        self._sql_kv("risk_management_enabled", bool(getattr(cfg, "risk_management_enabled", True)), "copy-sl")
        self._sql_kv(
            "max_position_loss_pct",
            float(_ctrl("max_position_loss_pct", getattr(cfg, "max_position_loss_pct", 0.333) or 0.333)),
            "copy-sl",
        )
        self._sql_kv(
            "max_margin_per_position_pct",
            float(getattr(cfg, "max_margin_per_position_pct", 0.333) or 0.333),
            "copy-sl",
        )
        self._sql_kv(
            "min_free_margin_pct",
            float(_ctrl("min_free_margin_pct", getattr(cfg, "min_free_margin_pct", 0.15) or 0.15)),
            "copy-sl",
        )
        self._sql_kv(
            "size_exclude_unrealized_profit",
            bool(getattr(cfg, "size_exclude_unrealized_profit", True)),
            "copy-sl",
        )
        self._sql_kv("close_orphans", bool(getattr(cfg, "close_orphans", True)), "copy-sl")
        self._sql_kv("one_position_per_asset", bool(getattr(cfg, "one_position_per_asset", True)), "copy-sl")
        self._sql_kv("copy_shorts", bool(self._allow_shorts()), "copy-sl")
        self._sql_kv(
            "max_positions_per_trader",
            int(_ctrl("max_positions_per_trader", getattr(cfg, "max_positions_per_trader", 3) or 3)),
            "copy-sl",
        )
        self._sql_kv(
            "max_leverage",
            int(_ctrl("max_leverage", getattr(cfg, "max_leverage", 20) or 20)),
            "copy-sl",
        )
        poll = getattr(self, "_runtime_poll_interval", None)
        if poll is None:
            poll = _ctrl("poll_interval_seconds", getattr(cfg, "poll_interval_seconds", 5) or 5)
        self._sql_kv("poll_interval_seconds", float(poll), "copy-sl")
        slots = getattr(self, "_trader_follow_slots", None) or {}
        if slots:
            self._sql_kv("trader_follow_slots", {str(k): int(v) for k, v in slots.items()}, "copy-sl")
        unf = sorted(int(n) for n in (getattr(self, "_unfollowed_traders", set()) or set()))
        self._sql_kv("unfollowed_traders", unf, "copy-sl")
        try:
            self._sql_kv("max_subscribed_markets", int(self._max_subscribed()), "copy-sl")
        except Exception:
            pass
        self._sql_kv("longs_only", not bool(self._allow_shorts()), "copy-sl")

    def _process_force_drop_markets(self):
        """Home / dashboard: drop idle books.
          {"coin": "ETH"} | {"coins": ["ETH","PENDLE"]} | {"all_idle": true}
        Never closes a live Hyperliquid position.
        """
        cmd = self._sql_cmd(("drop_markets",))
        if not cmd:
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        self._sql_done(cmd)
        if not isinstance(data, dict):
            data = {}
        if data.get("all_idle") or data.get("all"):
            idle = self._idle_pairs()
            self.logger().info(
                f"[markets] Home clean-idle — dropping {idle or 'none'}"
            )
            self._standard_coin_cleanup(force=True)
            return
        coins = []
        if data.get("coin"):
            coins.append(data.get("coin"))
        if data.get("pair"):
            coins.append(data.get("pair"))
        for c in data.get("coins") or data.get("pairs") or []:
            coins.append(c)
        wanted = self._wanted_pairs()
        dropped = []
        skipped = []
        for raw_c in coins:
            pair = self._as_pair(raw_c)
            if not pair:
                continue
            if pair in wanted:
                skipped.append(pair)
                self.logger().info(
                    f"[markets] Home delete skipped {pair} — live copy trade, book stays"
                )
                continue
            self._drop_market(pair)
            dropped.append(pair)
        if dropped or skipped:
            self.logger().info(
                f"[markets] Home delete dropped={dropped or '—'} "
                f"skipped_live={skipped or '—'}"
            )

    def _connector_is_ready(self) -> bool:
        connector = self.connectors.get(self.config.connector_name)
        if connector is None:
            return False
        if getattr(connector, "ready", False):
            return True
        # Do not wait for leftover leader coins. Ready when live mirrors (or
        # any seed book, if flat) have an order book we can trade.
        needed = [m.get("pair") for m in (self._our_mirrors or {}).values() if m.get("pair")]
        if needed:
            return all(self._pair_has_book(p) for p in needed)
        registered = list(self._registered_pairs()) or list(CORE_MARKETS)
        return any(self._pair_has_book(p) for p in registered)

    def _emit_heartbeat(self, now: float, connector_ready: bool):
        """Always emit a 60s heartbeat — even when the connector is not ready."""
        if not hasattr(self, "_last_heartbeat"):
            self._last_heartbeat = 0.0
        if now - self._last_heartbeat < 60:
            return
        self._last_heartbeat = now

        mirrors = len(self._our_mirrors)
        comm = len(self._commodity_mirrors)
        # free = withdrawable; equity = full account value (free + margin in positions)
        free = self._get_free_balance()
        equity = self._get_total_balance()
        ready_str = "ready" if connector_ready else "NOT READY"
        stall_str = ""
        if not connector_ready and getattr(self, "_not_ready_since", None):
            stall_s = int(now - self._not_ready_since)
            stall_str = f"  stalled={stall_s}s"

        n_mkts = len(self._registered_pairs())
        pending = len(self._pair_ensure_pending)
        msg = (
            f"[heartbeat] connector={ready_str}{stall_str}  |  "
            f"mirrors: {mirrors} crypto  {comm} commodity  |  "
            f"markets: {n_mkts} {sorted(self._registered_pairs())}"
            f"{f' (+{pending} pending)' if pending else ''}  |  "
            f"equity: ${float(equity):.2f}  free: ${float(free):.2f}  "
            f"(crypto budget: ${float(free)*(1-self.config.commodity_allocation_pct):.2f}"
            f"  commodity budget: ${float(free)*self.config.commodity_allocation_pct:.2f})  |  "
            f"HL API: {self._hl_api_usage()}"
        )
        try:
            self._write_status_file(equity=equity, free=free, connector_ready=connector_ready)
        except Exception as e:
            self.logger().warning(f"[status] write failed: {e}")
        if connector_ready:
            self.logger().info(msg)
        else:
            stalled_s = 0
            if getattr(self, "_not_ready_since", None):
                stalled_s = int(now - self._not_ready_since)
            if stalled_s >= 20:
                self.logger().warning(msg)

    def on_tick(self):
        now = time.time()
        poll = float(
            getattr(self, "_runtime_poll_interval", None)
            or _ctrl("poll_interval_seconds", self.config.poll_interval_seconds or 5)
        )
        if now - self._last_poll < max(2.0, poll):
            return
        self._last_poll = now

        # Dashboard runtime flags (longs-only, etc.) + force actions
        try:
            self._load_runtime_flags()
        except Exception as e:
            self.logger().warning(f"[runtime] flag load error: {e}")
        try:
            self._process_force_closes()
        except Exception as e:
            self.logger().warning(f"[force-close] handler error: {e}")
        try:
            self._process_force_follows()
        except Exception as e:
            self.logger().warning(f"[force-follow] handler error: {e}")
        try:
            self._process_force_scales()
        except Exception as e:
            self.logger().warning(f"[force-scale] handler error: {e}")
        try:
            self._process_force_full_allin()
        except Exception as e:
            self.logger().warning(f"[force-full-allin] handler error: {e}")
        try:
            self._process_force_add_eth()
        except Exception as e:
            self.logger().warning(f"[force-add-eth] handler error: {e}")
        try:
            self._process_force_risk_toggles()
        except Exception as e:
            self.logger().warning(f"[force-risk-toggle] handler error: {e}")
        try:
            self._process_force_drop_markets()
        except Exception as e:
            self.logger().warning(f"[markets] force-drop handler error: {e}")
        try:
            self._load_risk_overrides()
        except Exception as e:
            self.logger().warning(f"[risk-override] load error: {e}")

        # Track connector readiness. Do NOT bail out of the whole tick when
        # the connector is not ready — that caused multi-hour silent stalls
        # (no heartbeats, no leader polling, no close detection) after HL
        # network blips / order-book re-init storms.
        try:
            self._standard_coin_cleanup()
        except Exception as e:
            self.logger().warning(f"[markets] standard cleanup error: {e}")
        try:
            held = []
            for pair, m in (self._our_mirrors or {}).items():
                p = (m or {}).get("pair") or pair
                if p:
                    held.append(p)
            for p in held:
                if p and not self._pair_has_book(p):
                    self._ensure_pair_ready(p)
            if getattr(self, "_full_allin_phase", None):
                c = str(getattr(self, "_full_allin_coin", None) or "BTC").upper()
                p = f"{c}-USD"
                if p and not self._pair_has_book(p):
                    self._ensure_pair_ready(p)
        except Exception as e:
            self.logger().warning(f"[markets] ensure wanted error: {e}")

        connector_ready = self._connector_is_ready()
        if connector_ready:
            if getattr(self, "_ready_since", None) is None:
                self._ready_since = now
            if getattr(self, "_not_ready_since", None) is not None:
                stalled = int(now - self._not_ready_since)
                if stalled >= 20:
                    self.logger().info(
                        f"[stall] connector ready again after {stalled}s — resuming full trading"
                    )
                self._not_ready_since = None
                self._last_heartbeat = 0.0  # write status immediately
            # Restore positions and fetch HL max leverage once on first ready tick
            if not getattr(self, "_positions_restored", False):
                self._positions_restored = True
                self._fetch_hl_max_leverage()
                self._restore_our_positions()
                try:
                    self._seed_sl_blocks_from_gap()
                except Exception as e:
                    self.logger().warning(f"[risk] sl-block seed error: {e}")
            # Risk: Home SL ROE always; USD stop if enabled
            try:
                self._risk_manage_positions()
            except Exception as e:
                self.logger().warning(f"[risk] manage error: {e}")
            # Init SDK every tick until ready — skip if commodity trading disabled
            if not self._sdk_ready and self.config.commodity_allocation_pct > 0:
                self._init_sdk()
        else:
            self._ready_since = None
            if getattr(self, "_not_ready_since", None) is None:
                self._not_ready_since = now
            stalled = int(now - self._not_ready_since)
            if stalled >= 20 and stalled % 20 < 6:
                self.logger().warning(
                    "[stall] connector not ready — continuing public API polling; "
                    "new opens deferred until ready; closes still attempted"
                )

        # Heartbeat every 60s always (ready or not)
        self._emit_heartbeat(now, connector_ready)

        # Fetch each trader's positions ONCE per tick — shared by reconcile + check_trader.
        # Public HL info API works without the Hummingbot connector being ready.
        # Skip entirely if we are in a 429 backoff window to avoid making the rate limit worse.
        if time.time() < self._api_backoff_until:
            remaining = int(self._api_backoff_until - time.time())
            if remaining % 10 == 0:  # log every 10 s so it's not spammy
                self.logger().warning(f"[rate-limit] HL API backoff — resuming in ~{remaining}s")
            return

        fresh_positions: Dict[str, Dict[str, dict]] = {}
        for address in self.config.trader_addresses:
            try:
                fresh_positions[address] = self._fetch_trader_positions(address)
            except requests.Timeout:
                self.logger().warning(f"Timeout fetching {address[:10]}...")
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if code in (429, 502, 503):
                    wait = 30 if code == 429 else 15
                    self._api_backoff_until = time.time() + wait
                    self.logger().warning(
                        f"[rate-limit] HTTP {code} from HL — backing off all API calls for {wait}s"
                    )
                    break
                self.logger().warning(f"Error fetching {address[:10]}...: {e}")
            except Exception as e:
                err = str(e)
                if "429" in err or "502" in err or "503" in err:
                    wait = 30 if "429" in err else 15
                    self._api_backoff_until = time.time() + wait
                    self.logger().warning(
                        f"[rate-limit] HL API error — backing off all API calls for {wait}s"
                    )
                    break
                self.logger().warning(f"Error fetching {address[:10]}...: {e}")

        # Reconcile / orphan close (public API + close attempts).
        # Opens are gated inside _open_mirror when the connector is not ready.
        try:
            self._reconcile_positions(fresh_positions)
        except Exception as e:
            self.logger().warning(f"Reconcile error: {e}")

        for address in self.config.trader_addresses:
            if address not in fresh_positions:
                continue
            try:
                self._check_trader(address, fresh_positions[address])
            except Exception as e:
                self.logger().warning(f"Error checking {address[:10]}...: {e}")
            if self.config.commodity_max_positions > 0:
                try:
                    self._check_hip3_fills(address)
                except Exception as e:
                    self.logger().warning(f"HIP-3 fill check failed for {address[:10]}...: {e}")

    # ── Dashboard force close / force follow ──────────────────────────────────

    def _process_force_closes(self):
        """
        Close requested coin(s) (or all mirrors) immediately via market orders.
        SQL payload: {"coin": "SOL"} or {"coins": ["SOL","BTC"]} or {"all": true}
        """
        cmd = self._sql_cmd(("close",))
        if not cmd:
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        self._sql_done(cmd)

        close_all = bool(data.get("all"))
        coins: List[str] = []
        if data.get("coins"):
            coins = [str(c).strip().upper() for c in data["coins"] if c]
        elif data.get("coin"):
            coins = [str(data["coin"]).strip().upper()]

        if not close_all and not coins:
            self.logger().warning("[force-close] empty signal — nothing to close")
            return

        if close_all:
            pairs = list(self._our_mirrors.keys())
            if not pairs:
                # Fall back to on-chain so we can still flatten
                our = self._fetch_our_onchain_positions() or {}
                for coin in our:
                    pairs.append(f"{coin}-USD")
            self.logger().info(
                f"[force-close] Dashboard CLOSE ALL — {len(pairs)} position(s)"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'CLOSE ALL':<8} "
                f"{len(pairs)} position(s){RESET}"
            )
            for pair in pairs:
                self._force_close_pair_or_coin(pair=pair)
            return

        for coin in coins:
            self.logger().info(f"[force-close] Dashboard close requested for {coin}")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FORCE':<8} "
                f"{'':6} {coin:<7} close signal{RESET}"
            )
            self._force_close_pair_or_coin(coin=coin)

    def _process_force_follows(self):
        """
        Dashboard manual follow of a leader position we are not currently in.
        File format (JSON):
          {"trader": 1, "coin": "LIT", "side": "SHORT"}   # side optional
        Opens at live HL mid with normal sizing, registers mirror under that
        leader so when they close we auto-close (same path as normal copies).
        """
        cmd = self._sql_cmd(("follow",))
        if not cmd:
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        self._sql_done(cmd)

        coin = str(data.get("coin") or "").strip()
        if not coin or ":" in coin:
            self.logger().warning(f"[force-follow] invalid/unsupported coin: {coin!r}")
            return

        # Resolve leader address
        address = None
        trader_num = data.get("trader")
        if data.get("address"):
            addr_req = str(data["address"]).strip().lower()
            for a in self.config.trader_addresses:
                if a.lower() == addr_req:
                    address = a
                    break
        if address is None and trader_num is not None:
            try:
                idx = int(trader_num) - 1
                if 0 <= idx < len(self.config.trader_addresses):
                    address = self.config.trader_addresses[idx]
            except (TypeError, ValueError):
                pass
        if not address:
            self.logger().warning(
                f"[force-follow] cannot resolve trader from {data!r}"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"{'':6} {coin:<7} failed — unknown trader{RESET}"
            )
            return

        trader_num = self.config.trader_addresses.index(address) + 1
        side_req = str(data.get("side") or "").strip().upper() or None

        if not self._is_trader_followed(address=address, trader_num=trader_num):
            self.logger().warning(
                f"[force-follow] T#{trader_num} is unfollowed — re-follow first"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"{'':6} {coin:<7} blocked — T#{trader_num} unfollowed{RESET}"
            )
            return

        # Live leader position (must still be open)
        try:
            leader_pos = self._fetch_trader_positions(address)
        except Exception as e:
            self.logger().warning(f"[force-follow] leader fetch failed: {e}")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"{'':6} {coin:<7} failed — leader fetch error{RESET}"
            )
            return

        pos = leader_pos.get(coin)
        if pos is None:
            # case-insensitive coin match (HL is usually exact)
            for c, p in leader_pos.items():
                if c.upper() == coin.upper():
                    coin, pos = c, p
                    break
        if pos is None:
            self.logger().warning(
                f"[force-follow] T#{trader_num} no longer holds {coin} — abort"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"{'':6} {coin:<7} failed — leader flat{RESET}"
            )
            return

        if side_req and pos["side"] != side_req:
            self.logger().warning(
                f"[force-follow] T#{trader_num} {coin} is {pos['side']}, "
                f"requested {side_req} — abort"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"{pos['side']:<6} {coin:<7} failed — side mismatch ({side_req}){RESET}"
            )
            return

        pair = pos.get("pair") or COIN_TO_PAIR.get(coin) or f"{coin}-USD"
        pos = {**pos, "pair": pair}

        if pair in self._our_mirrors:
            self.logger().info(
                f"[force-follow] already mirroring {pair} — nothing to do"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"{pos['side']:<6} {coin:<7} already mirrored{RESET}"
            )
            return

        if pos["side"] == "SHORT" and not self._allow_shorts():
            self.logger().info(
                f"[force-follow] SHORT {coin} blocked — longs only mode"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                f"SHORT  {coin:<7} blocked — longs only{RESET}"
            )
            return

        # Clear late-entry / skip blocks so this open is allowed
        self._skipped_positions.setdefault(address, set()).discard(coin)
        self._pair_ensure_failed.discard(pair)

        # Keep trader snapshot in sync so subsequent close detection works
        if address in self._trader_positions:
            self._trader_positions[address][coin] = pos
        else:
            self._trader_positions[address] = dict(leader_pos)
        self._initialized_traders.add(address)

        self.logger().info(
            f"[force-follow] Dashboard FOLLOW T#{trader_num} {pos['side']} {coin} "
            f"@ leader entry ${pos['entry_px']:,.6f} — opening at live mid"
        )
        trade_log(
            f"{CYAN}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
            f"{pos['side']:<6} {coin:<7} T#{trader_num} manual follow — live mid"
            f"{RESET}"
        )

        # force=True: user override of trader cap / skip path; asset lock still applies
        pos["_force"] = True
        ok = self._open_mirror(address, coin, pos, force=True)
        if ok:
            # Ensure mirror is attributed to this leader for auto-close ownership
            if pair in self._our_mirrors:
                self._our_mirrors[pair]["trader"] = address
                self._our_mirrors[pair]["force_follow"] = True
            self._deferred_opens.get(address, {}).pop(coin, None)
            self.logger().info(
                f"[force-follow] OPEN registered for {pair} owner=T#{trader_num} "
                f"— will auto-close when leader closes"
            )
        else:
            # Defer until connector/book ready — same retry path as normal opens
            self._deferred_opens.setdefault(address, {})[coin] = pos
            self.logger().info(
                f"[force-follow] deferred {coin} — will retry when markets ready"
            )

    def _force_close_pair_or_coin(self, coin: Optional[str] = None, pair: Optional[str] = None):
        """Close a tracked mirror, or invent one from on-chain if only the coin is known."""
        targets: List[str] = []
        if pair and pair in self._our_mirrors:
            targets.append(pair)
        if coin:
            for p, m in list(self._our_mirrors.items()):
                mcoin = (m.get("coin") or p.replace("-USD", "")).upper()
                if mcoin == coin.upper() or p.upper() == f"{coin.upper()}-USD":
                    if p not in targets:
                        targets.append(p)

        if not targets and coin:
            # Not in local mirror state — try live account position
            our = self._fetch_our_onchain_positions() or {}
            pos = our.get(coin) or our.get(coin.upper()) or our.get(coin.capitalize())
            # coins are case-sensitive on HL (e.g. kPEPE) — try exact then upper map
            if pos is None:
                for c, pdata in our.items():
                    if c.upper() == coin.upper():
                        pos = pdata
                        coin = c
                        break
            if pos is not None:
                szi = float(pos.get("szi") or 0)
                if abs(szi) > 0:
                    pair = f"{coin}-USD"
                    side = "LONG" if szi > 0 else "SHORT"
                    self._our_mirrors[pair] = {
                        "trader": "dashboard",
                        "coin": coin,
                        "side": side,
                        "amount": Decimal(str(abs(szi))),
                        "entry_price": Decimal(str(pos.get("entryPx") or 0)),
                        "entry_time": "force-close",
                        "order_id": None,
                    }
                    targets.append(pair)
                    self.logger().info(
                        f"[force-close] Registered on-chain {side} {coin} for close"
                    )

        if not targets and pair:
            # pair given but not mirrored — try coin from pair
            c = pair.replace("-USD", "")
            return self._force_close_pair_or_coin(coin=c)

        if not targets:
            self.logger().warning(
                f"[force-close] No open position found for "
                f"{coin or pair or '?'} — already flat?"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {'':6} {(coin or pair or '?'):<7} "
                f"already flat / not found"
            )
            return

        for p in targets:
            self._close_mirror(p, reason="dashboard force close")

    def _allow_shorts(self) -> bool:
        """Whether new SHORT mirrors are allowed (runtime override > yml)."""
        if self._runtime_copy_shorts is not None:
            return bool(self._runtime_copy_shorts)
        return bool(_ctrl("copy_shorts", self.config.copy_shorts))

    def _max_zec_short_usd(self) -> float:
        try:
            v = float(_ctrl("max_zec_short_usd", getattr(self.config, "max_zec_short_usd", 0) or 0))
        except (TypeError, ValueError):
            v = 0.0
        return max(0.0, v)

    def _write_status_file(self, equity=None, free=None, connector_ready: bool = True):
        """Persist live mirrors for the dashboard (source of truth for Active positions)."""
        try:
            from risk_park import maybe_send
            maybe_send(COPY_BOT_ID, self.connectors.get(self.config.connector_name))
        except Exception:
            pass
        if equity is None:
            equity = self._get_total_balance()
        if free is None:
            free = self._get_free_balance()
        mirrors_out = []
        self._status_chain = None
        for pair, m in list(self._our_mirrors.items()):
            coin = m.get("coin") or pair.replace("-USD", "")
            side = m.get("side") or "?"
            try:
                amount = float(m.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            try:
                entry = float(m.get("entry_price") or 0)
            except (TypeError, ValueError):
                entry = 0.0
            lev = m.get("leverage") or 0
            try:
                lev = int(lev) if lev else 0
            except (TypeError, ValueError):
                lev = 0
            mar = m.get("margin")
            if mar is None:
                mar = float(self._mirror_margin_usd(m))
            else:
                try:
                    mar = float(mar)
                except (TypeError, ValueError):
                    mar = 0.0
            trader = m.get("trader")
            tnum = None
            if isinstance(trader, str) and trader in (self.config.trader_addresses or []):
                tnum = self.config.trader_addresses.index(trader) + 1
            elif trader in ("full_btc", "full_eth", "full_allin"):
                tnum = None
            is_allin = bool(
                m.get("full_allin") or m.get("full_btc") or m.get("full_eth")
                or trader in ("full_btc", "full_eth", "full_allin")
            )
            manual = bool(m.get("manual_close_only") or is_allin or m.get("add_eth"))
            risk_off = bool(
                m.get("risk_auto_close_disabled")
                or (str(coin).upper() in getattr(self, "_risk_disabled_coins", set()))
            )
            src = "live"
            if is_allin:
                src = f"full_{(m.get('coin') or coin or 'all').lower()}"
            elif m.get("add_eth"):
                src = "add_eth"
            mark = 0.0
            try:
                mid = self._get_mid_price(pair)
                if mid is not None:
                    mark = float(mid)
            except Exception:
                mark = 0.0
            ch = None
            try:
                our = getattr(self, "_status_chain", None)
                if not isinstance(our, dict):
                    our = self._fetch_our_onchain_positions() or {}
                    self._status_chain = our
                ch = our.get(str(coin).upper()) or our.get(coin)
            except Exception:
                ch = None
            if mark <= 0 and isinstance(ch, dict):
                try:
                    szi = float(ch.get("szi") or 0)
                    up = float(ch.get("unrealizedPnl") or 0)
                    ent = float(ch.get("entryPx") or entry or 0)
                    if szi != 0 and ent:
                        mark = ent + up / szi
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            try:
                upnl = float(self._mirror_upnl_usd(m))
            except Exception:
                upnl = 0.0
            if isinstance(ch, dict):
                try:
                    cup = float(ch.get("unrealizedPnl") or 0)
                    upnl = cup
                except (TypeError, ValueError):
                    pass
            roe = self._mirror_roe_pct(m, ch if isinstance(ch, dict) else None)
            try:
                roe_f = float(roe) if roe is not None else 0.0
            except (TypeError, ValueError):
                roe_f = 0.0
            mirrors_out.append({
                "coin": coin,
                "side": side,
                "size": amount,
                "entry_px": entry,
                "mark_px": round(mark, 8) if mark else 0.0,
                "unrealized_pnl": round(upnl, 4),
                "pnl_usd": round(upnl, 4),
                "roe_pct": round(roe_f, 2),
                "leverage": lev,
                "margin": round(mar, 2),
                "notional": round(amount * entry, 2) if amount and entry else 0.0,
                "open_time": m.get("entry_time") or "",
                "source": src,
                "trader": tnum,
                "all_traders": is_allin,
                "full_allin": is_allin,
                "manual_close_only": manual,
                "add_eth": bool(m.get("add_eth")),
                "risk_auto_close_disabled": risk_off,
                "pair": pair,
            })
        wallet = None
        try:
            conn = self.connectors.get(self.config.connector_name)
            wallet = getattr(conn, "hyperliquid_perpetual_address", None)
        except Exception:
            wallet = None
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": float(equity) if equity is not None else None,
            "free": float(free) if free is not None else None,
            "wallet": wallet,
            "connector_ready": bool(connector_ready),
            "full_allin_phase": getattr(self, "_full_allin_phase", None),
            "full_allin_coin": getattr(self, "_full_allin_coin", None),
            "full_allin_side": getattr(self, "_full_allin_side", None) or "LONG",
            "full_btc_phase": getattr(self, "_full_allin_phase", None),  # legacy
            "copy_shorts": self._allow_shorts(),
            "longs_only": not self._allow_shorts(),
            "max_zec_short_usd": self._max_zec_short_usd(),
            "max_leverage": int(_ctrl("max_leverage", self.config.max_leverage or 20)),
            "max_positions_per_trader": int(_ctrl(
                "max_positions_per_trader",
                self.config.max_positions_per_trader or self.config.min_open_positions or 3,
            )),
            "max_subscribed_markets": int(self._max_subscribed()),
            "poll_interval_seconds": float(
                getattr(self, "_runtime_poll_interval", None)
                or _ctrl("poll_interval_seconds", self.config.poll_interval_seconds or 5)
            ),
            "max_position_loss_pct": float(_ctrl(
                "max_position_loss_pct",
                getattr(self.config, "max_position_loss_pct", 0.333) or 0.333,
            )),
            "min_free_margin_pct": float(_ctrl(
                "min_free_margin_pct",
                getattr(self.config, "min_free_margin_pct", 0.15) or 0.15,
            )),
            "unfollowed_traders": sorted(
                int(n) for n in (getattr(self, "_unfollowed_traders", set()) or set())
            ),
            "n_traders": int(self._n_traders()),
            "n_traders_configured": max(1, len(self.config.trader_addresses or [])),
            "risk": {
                "enabled": bool(getattr(self.config, "risk_management_enabled", True)),
                "max_position_loss_pct": float(_ctrl("max_position_loss_pct", getattr(self.config, "max_position_loss_pct", 0.333) or 0.333)),
                "max_margin_per_position_pct": float(getattr(self.config, "max_margin_per_position_pct", 0.333) or 0.333),
                "min_free_margin_pct": float(_ctrl("min_free_margin_pct", getattr(self.config, "min_free_margin_pct", 0.15) or 0.15)),
                "size_exclude_unrealized_profit": bool(getattr(self.config, "size_exclude_unrealized_profit", True)),
                "max_loss_usd": float(self._risk_max_position_loss_usd(Decimal(str(equity or 0)))) if equity else None,
                "sizing_equity": float(self._sizing_equity()),
                "total_upnl": float(self._total_unrealized_pnl()),
            },
            "mirrors": mirrors_out,
            "n_mirrors": len(mirrors_out),
            "markets": sorted(self._registered_pairs()),
            "n_markets": len(self._registered_pairs()),
            "idle_markets": self._idle_pairs(),
            "sl_roe_pct": float(_live_sl_roe()),
            "sl_block": sorted(self._sl_block.keys()) if getattr(self, "_sl_block", None) else [],
        }
        with open(STATUS_FILE, "w") as f:
            json.dump(payload, f)
        try:
            from matrix_store import try_status_put
            try_status_put(COPY_BOT_ID, payload)
        except Exception:
            pass

    def _full_allin_deployable_margin(self) -> tuple:
        """
        Emergency all-in size = Copy wallet only (this connector).
        Other hummingbots / subaccounts are never used.
        Returns (allocation, equity, free, n_traders, pot_each).
        """
        equity = self._get_total_balance()
        free = self._get_free_balance()
        n = self._n_traders()
        copy_funds = equity
        pot_each = copy_funds / Decimal(str(n)) if n else copy_funds
        target = copy_funds
        deployable = free if free > 0 else target
        if free > 0:
            deployable = min(free, target) if target > 0 else free
        allocation = deployable - Decimal("1.00")
        if allocation < 0:
            allocation = Decimal("0")
        return allocation, equity, free, n, pot_each

    def _arm_full_allin(self, coin: str, leverage_pct: float, label: str = "FULL", side: str = "LONG"):
        """Start close-all Copy mirrors → LONG or SHORT {coin} with Copy wallet only."""
        coin = (coin or "BTC").strip().upper()
        if ":" in coin:
            self.logger().warning(f"[force-full-allin] unsupported coin {coin}")
            return
        side = "SHORT" if str(side or "LONG").upper() == "SHORT" else "LONG"
        pct = max(1.0, min(100.0, float(leverage_pct)))
        self._full_allin_coin = coin
        self._full_allin_side = side
        self._full_allin_leverage_pct = pct
        self._full_allin_phase = "closing"
        self._full_btc_phase = "closing"  # legacy block for copy opens
        self._full_allin_started_at = time.time()
        self._full_allin_flat_since = None
        self._close_in_flight.clear()
        self._close_backoff_until.clear()
        n = self._n_traders()
        eq = self._get_total_balance()
        hl_max = self._hl_raw_max_leverage_for(coin)
        eff = max(1, int(round(hl_max * pct / 100.0)))
        tag = f"FULL {coin}"
        self.logger().info(
            f"[force-full-allin] START — close Copy mirrors, then {side} {coin} with "
            f"Copy wallet only (equity ${float(eq):.2f}, {n} internal pot(s)) "
            f"@ {pct:.0f}% of HL max {coin} leverage ({hl_max}x → ~{eff}x) "
            f"— other bots untouched"
        )
        trade_log(
            f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {tag:<8} "
            f"{side:<6} {coin:<7} Copy wallet only → close Copy → {side} {coin} "
            f"@ {pct:.0f}% of HL {hl_max}x (→{eff}x){RESET}"
        )
        pairs = list(self._our_mirrors.keys())
        our = self._fetch_our_onchain_positions() or {}
        for c in our:
            p = f"{c}-USD"
            if p not in pairs:
                pairs.append(p)
        for pair in pairs:
            self._force_close_pair_or_coin(pair=pair)

    def _process_force_full_allin(self):
        """
        Dashboard full-scale long (BTC / ETH / …):
          close Copy mirrors only → wait flat + free settled → LONG {coin}
          with Copy wallet equity only (other hummingbots untouched)
          at leverage_pct% of Hyperliquid max for that coin (config max ignored).

        Signal files:
          force_full_allin_hl_copy.json  {"coin":"ETH","leverage_pct":100}
          force_full_btc_hl_copy.json    legacy → BTC @ leverage_pct default 100
        """
        cmd = self._sql_cmd(("full_allin",))
        if cmd:
            data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
            self._sql_done(cmd)
            coin = str(data.get("coin") or "BTC").strip().upper()
            try:
                pct = float(data.get("leverage_pct", data.get("leverage_percent", 100)))
            except (TypeError, ValueError):
                pct = 100.0
            side = str(data.get("side") or "LONG").upper()
            self._arm_full_allin(coin, pct, side=side)

        if not self._full_allin_phase:
            self._full_btc_phase = None
            return

        coin = self._full_allin_coin or "BTC"
        tag = f"FULL {coin}"

        if time.time() - self._full_allin_started_at > 600:
            our_left = self._fetch_our_onchain_positions()
            if our_left:
                self.logger().warning(
                    f"[force-full-allin] still flattening after 600s "
                    f"({len(our_left)} on-chain) — keep going"
                )
                self._full_allin_started_at = time.time()
            else:
                self.logger().warning(f"[force-full-allin] timed out after 600s — abort {coin}")
                trade_log(
                    f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {tag:<8} "
                    f"abort — timeout{RESET}"
                )
                self._full_allin_phase = None
                self._full_btc_phase = None
                self._full_allin_flat_since = None
                return

        if self._full_allin_phase == "closing":
            pending_n = len(self._pending_closes)
            mirrors_n = len(self._our_mirrors)
            our = self._fetch_our_onchain_positions()
            if our is None:
                return
            onchain_n = len(our)

            if onchain_n == 0 and mirrors_n == 0:
                if pending_n > 0:
                    self.logger().info(
                        f"[force-full-allin] on-chain flat — clearing {pending_n} stale pending close(s)"
                    )
                    self._pending_closes.clear()
                if self._full_allin_flat_since is None:
                    self._full_allin_flat_since = time.time()
                equity = self._get_total_balance()
                free = self._get_free_balance()
                settled = False
                if equity > 0 and free >= equity * Decimal("0.92"):
                    settled = True
                elif free > Decimal("5") and (time.time() - self._full_allin_flat_since) >= 8:
                    settled = True
                if not settled:
                    self.logger().info(
                        f"[force-full-allin] flat — waiting free to settle "
                        f"(free=${float(free):.2f} equity=${float(equity):.2f})"
                    )
                    return
                self._full_allin_phase = "opening"
                self._full_btc_phase = "opening"
                self.logger().info(
                    f"[force-full-allin] flat + settled — opening LONG {coin} "
                    f"free=${float(free):.2f} equity=${float(equity):.2f}"
                )
                return

            if mirrors_n > 0:
                for pair in list(self._our_mirrors.keys()):
                    if not self._pair_has_book(pair):
                        self._ensure_pair_ready(pair)
                        self.logger().info(
                            f"[force-full-allin] waiting for {pair} book before close"
                        )
                        continue
                    m = self._our_mirrors.get(pair) or {}
                    started = float(m.get("_close_started") or 0)
                    # Give an in-flight close a short window, then retry.
                    if started and (time.time() - started) < 12:
                        continue
                    self._close_in_flight.discard(pair)
                    self._close_backoff_until.pop(pair, None)
                    m["_closing"] = False
                    self._our_mirrors[pair] = m
                    self._force_close_pair_or_coin(pair=pair)
                return

            if onchain_n > 0:
                for c, pos in (our or {}).items():
                    self._force_close_pair_or_coin(coin=c)
                return

            if pending_n > 0:
                return

            self._full_allin_phase = "opening"
            self._full_btc_phase = "opening"
            return

        if self._full_allin_phase == "opening":
            pair = f"{coin}-USD"
            if not self._pair_has_book(pair):
                self._ensure_pair_ready(pair)
            if not self._connector_is_ready() and not self._pair_has_book(pair):
                return
            side = getattr(self, "_full_allin_side", None) or "LONG"
            ok = self._open_full_allin_long(coin, side=side)
            if ok:
                self._full_allin_phase = None
                self._full_btc_phase = None
                self._full_allin_flat_since = None
                self.logger().info(f"[force-full-allin] DONE — {side} {coin} registered")
                try:
                    self._write_status_file()
                except Exception:
                    pass

    def _open_full_allin_long(self, coin: str, side: str = "LONG") -> bool:
        """
        Open LONG or SHORT {coin}-USD with Copy wallet funds only, at leverage_pct% of HL max.
        Dashboard emergency — ignores longs-only. Other hummingbots untouched.
        """
        coin = (coin or "BTC").strip().upper()
        side = "SHORT" if str(side or "LONG").upper() == "SHORT" else "LONG"
        pair = f"{coin}-USD"
        pct = float(self._full_allin_leverage_pct or 100)
        hl_max = self._hl_raw_max_leverage_for(coin)
        lev = max(1, int(round(hl_max * pct / 100.0)))
        lev = min(lev, hl_max)
        lev = max(1, lev)
        tag = f"FULL {coin}"

        if not self._ensure_pair_ready(pair):
            self.logger().info(f"[force-full-allin] waiting for {pair} book")
            return False

        allocation, equity, free, n_traders, pot_each = self._full_allin_deployable_margin()
        if allocation < Decimal("2"):
            self.logger().warning(
                f"[force-full-allin] deployable ${float(allocation):.2f} too small "
                f"(equity=${float(equity):.2f} free=${float(free):.2f})"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {tag:<8} "
                f"{side:<6} {coin:<7} skip — funds ${float(allocation):.2f} too small{RESET}"
            )
            if free < equity * Decimal("0.5") and (time.time() - self._full_allin_started_at) < 120:
                return False
            return True

        try:
            self.connectors[self.config.connector_name].set_leverage(pair, lev)
        except Exception as e:
            self.logger().warning(f"[force-full-allin] set_leverage {coin} {lev}x: {e}")

        mid = self._get_mid_price(pair)
        if not mid or mid <= 0:
            self.logger().info(f"[force-full-allin] no {coin} mid yet")
            return False

        notional = allocation * Decimal(str(lev))
        step = Decimal("0.001") if coin == "ZEC" else Decimal("0.00001")
        amount = (notional / mid).quantize(step)
        if amount <= 0:
            self.logger().warning(f"[force-full-allin] amount too small for {coin}: {amount}")
            return True

        self._our_mirrors[pair] = {
            "trader": f"full_{coin.lower()}",
            "coin": coin,
            "side": side,
            "amount": amount,
            "entry_price": mid,
            "leverage": lev,
            "margin": allocation,
            "entry_time": _now(),
            "order_id": None,
            "full_allin": True,
            "full_btc": coin == "BTC",
            "full_eth": coin == "ETH",
            "manual_close_only": True,
            "hl_max_leverage": hl_max,
        }

        self.logger().info(
            f"[force-full-allin] OPEN {side} {coin} amount={amount} @ ~${float(mid):,.4f} "
            f"margin=${float(allocation):.2f} {lev}x notional=${float(notional):.2f} | "
            f"Copy wallet only equity=${float(equity):.2f} free=${float(free):.2f} "
            f"(lev={pct:.0f}% of HL {coin} max {hl_max}x)"
        )
        trade_log(
            f"{RED}{BOLD}"
            f"{_now():<22} {'WE COPY':<14} {'OPENED':<8} {side:<6} {coin:<7} "
            f"{float(amount):>10.5f}  ${float(mid):>14,.6f}  "
            f"margin=${float(allocation):.2f}  {lev}x  notional=${float(notional):.2f}"
            f"{RESET}"
        )
        trade_log(
            f"{CYAN}{BOLD}{_now():<22} {'DASHBOARD':<14} {tag:<8} "
            f"{side:<6} {coin:<7} Copy wallet "
            f"(${float(allocation):.2f} margin) "
            f"@ {lev}x ({pct:.0f}% of HL {hl_max}x){RESET}"
        )

        try:
            if side == "LONG":
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
            if order_id:
                self._pending_order_ids.add(order_id)
                self._our_mirrors[pair]["order_id"] = order_id
        except Exception as e:
            self.logger().error(f"[force-full-allin] order failed {coin}: {e}")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {tag:<8} "
                f"{side:<6} {coin:<7} order error: {e}{RESET}"
            )
        return True

    def _is_manual_close_only(self, pair: Optional[str] = None, mirror: Optional[dict] = None) -> bool:
        """Full-scale / add-ETH: only dashboard force-close may exit them.

        Risk stops set risk_stop=True and may clear this — capital protection wins.
        """
        m = mirror
        if m is None and pair:
            m = self._our_mirrors.get(pair)
        if not m:
            return False
        if m.get("risk_stop"):
            return False
        if m.get("manual_close_only") or m.get("full_allin") or m.get("full_btc") or m.get("full_eth") or m.get("add_eth"):
            return True
        t = m.get("trader")
        if isinstance(t, str) and (t.startswith("full_") or t in ("add_eth", "dashboard_eth")):
            return True
        return False

    def _rehydrate_manual_flags_from_status(self):
        """After restart, re-apply manual_close_only from last status snapshot."""
        try:
            if not os.path.exists(STATUS_FILE):
                return
            with open(STATUS_FILE, "r") as f:
                data = json.load(f) or {}
        except Exception:
            return
        for sm in data.get("mirrors") or []:
            if not (
                sm.get("manual_close_only")
                or sm.get("full_allin")
                or sm.get("add_eth")
                or str(sm.get("source") or "").startswith("full_")
            ):
                continue
            coin = sm.get("coin")
            if not coin:
                continue
            pair = f"{coin}-USD"
            if pair not in self._our_mirrors:
                continue
            m = self._our_mirrors[pair]
            m["manual_close_only"] = True
            if sm.get("add_eth") or sm.get("source") == "add_eth":
                m["add_eth"] = True
                m["trader"] = m.get("trader") or "add_eth"
            if sm.get("full_allin") or str(sm.get("source") or "").startswith("full_"):
                m["full_allin"] = True
                m["full_btc"] = coin == "BTC"
                m["full_eth"] = coin == "ETH"
                m["trader"] = f"full_{coin.lower()}"
            self.logger().info(
                f"[restore] re-applied manual_close_only on {pair}"
            )

    def _process_force_add_eth(self):
        """
        Dashboard Add ETH: open/add LONG ETH using funds_pct% of free balance
        without closing other positions. Manual-close only.
        File: {"funds_pct": 50|100}
        """
        cmd = self._sql_cmd(("add_eth",))
        if not cmd:
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        self._sql_done(cmd)

        try:
            funds_pct = float(data.get("funds_pct", data.get("pct", 50)))
        except (TypeError, ValueError):
            funds_pct = 50.0
        funds_pct = 100.0 if funds_pct >= 75 else 50.0

        if getattr(self, "_full_allin_phase", None):
            self.logger().warning("[force-add-eth] blocked — full all-in in progress")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'ADD ETH':<8} "
                f"skip — full all-in running{RESET}"
            )
            return

        if not self._connector_is_ready():
            try:
                with open(FORCE_ADD_ETH_FILE, "w") as f:
                    json.dump({"funds_pct": funds_pct, "retry": True}, f)
            except Exception:
                pass
            return

        coin = "ETH"
        pair = "ETH-USD"
        if not self._ensure_pair_ready(pair):
            try:
                with open(FORCE_ADD_ETH_FILE, "w") as f:
                    json.dump({"funds_pct": funds_pct, "retry": True}, f)
            except Exception:
                pass
            self.logger().info("[force-add-eth] waiting for ETH-USD book — re-queued")
            return

        free = self._get_free_balance()
        equity = self._get_total_balance()
        allocation = free * Decimal(str(funds_pct / 100.0)) - Decimal("0.50")
        if allocation < Decimal("2"):
            self.logger().warning(
                f"[force-add-eth] free=${float(free):.2f} too small for {funds_pct:.0f}% "
                f"(equity=${float(equity):.2f})"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'ADD ETH':<8} "
                f"LONG   ETH     skip — free ${float(free):.2f} too small for "
                f"{funds_pct:.0f}% (need free margin){RESET}"
            )
            return

        hl_max = self._hl_raw_max_leverage_for(coin)
        lev = max(1, hl_max)
        try:
            self.connectors[self.config.connector_name].set_leverage(pair, lev)
        except Exception as e:
            self.logger().warning(f"[force-add-eth] set_leverage: {e}")

        mid = self._get_mid_price(pair)
        if not mid or mid <= 0:
            self.logger().warning("[force-add-eth] no ETH mid")
            return

        notional = allocation * Decimal(str(lev))
        amount = (notional / mid).quantize(Decimal("0.00001"))
        if amount <= 0:
            return

        existing = self._our_mirrors.get(pair)
        prev_amt = Decimal(str(existing.get("amount") or 0)) if existing else Decimal("0")
        if existing and existing.get("side") == "SHORT":
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'ADD ETH':<8} "
                f"skip — already SHORT ETH (close manually first){RESET}"
            )
            return

        if existing and existing.get("side") == "LONG":
            new_amt = prev_amt + amount
            try:
                old_e = Decimal(str(existing.get("entry_price") or mid))
                if prev_amt > 0:
                    existing["entry_price"] = (old_e * prev_amt + mid * amount) / new_amt
            except Exception:
                existing["entry_price"] = mid
            existing["amount"] = new_amt
            existing["leverage"] = lev
            existing["manual_close_only"] = True
            existing["add_eth"] = True
            existing["margin"] = self._mirror_margin_usd(existing) + allocation
            self._our_mirrors[pair] = existing
            action = "SCALED"
            total = new_amt
        else:
            self._our_mirrors[pair] = {
                "trader": "add_eth",
                "coin": coin,
                "side": "LONG",
                "amount": amount,
                "entry_price": mid,
                "leverage": lev,
                "margin": allocation,
                "entry_time": _now(),
                "order_id": None,
                "manual_close_only": True,
                "add_eth": True,
            }
            action = "OPENED"
            total = amount

        self.logger().info(
            f"[force-add-eth] {action} LONG ETH +{amount} @ ~${float(mid):,.4f} "
            f"margin=${float(allocation):.2f} {lev}x ({funds_pct:.0f}% free "
            f"${float(free):.2f}) total={float(total):.5f}"
        )
        trade_log(
            f"{CYAN}{BOLD}{_now():<22} {'DASHBOARD':<14} {'ADD ETH':<8} "
            f"LONG   ETH     {funds_pct:.0f}% free → margin=${float(allocation):.2f} "
            f"@ {lev}x (manual close only){RESET}"
        )
        trade_log(
            f"{RED}{BOLD}"
            f"{_now():<22} {'WE COPY':<14} {action:<8} LONG   ETH     "
            f"{float(amount):>10.5f}  ${float(mid):>14,.6f}  "
            f"margin=${float(allocation):.2f}  {lev}x  notional=${float(notional):.2f}"
            f"{RESET}"
        )

        try:
            order_id = self.buy(
                self.config.connector_name,
                pair,
                amount,
                OrderType.MARKET,
                mid * Decimal("1.01"),
                PositionAction.OPEN,
            )
            if order_id:
                self._pending_order_ids.add(order_id)
                self._our_mirrors[pair]["order_id"] = order_id
        except Exception as e:
            self.logger().error(f"[force-add-eth] order failed: {e}")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'ADD ETH':<8} "
                f"order error: {e}{RESET}"
            )
        try:
            self._write_status_file()
        except Exception:
            pass

    def _load_runtime_flags(self, force: bool = False):
        """
        Load dashboard runtime flags from conf/hl_copy_runtime.json.
        Does not delete the file (persistent toggle). Reloads when mtime changes.
        """
        data = {}
        mtime = 0.0
        if os.path.exists(RUNTIME_FLAGS_FILE):
            try:
                mtime = os.path.getmtime(RUNTIME_FLAGS_FILE)
            except OSError:
                mtime = 0.0
            if not force and mtime <= getattr(self, "_runtime_flags_mtime", 0.0):
                pass
            else:
                try:
                    with open(RUNTIME_FLAGS_FILE, "r") as f:
                        raw = f.read().strip()
                    data = json.loads(raw) if raw else {}
                except Exception as e:
                    self.logger().warning(f"[runtime] bad flags file: {e}")
                    data = {}
        try:
            from matrix_store import ensure, kv_map
            ensure()
            sql = kv_map("hummingbot-hl")
            if sql:
                data = dict(data or {})
                for k, v in sql.items():
                    if k in ("updated", "source", "ts"):
                        continue
                    data[k] = v
                mtime = max(mtime, time.time())
        except Exception:
            pass
        if not data:
            return

        self._runtime_flags_mtime = mtime
        prev = self._runtime_copy_shorts
        prev_unf = set(getattr(self, "_unfollowed_traders", set()) or set())

        if "copy_shorts" in data:
            self._runtime_copy_shorts = bool(data["copy_shorts"])
        elif "longs_only" in data:
            self._runtime_copy_shorts = not bool(data["longs_only"])

        prev_poll = getattr(self, "_runtime_poll_interval", None)
        if "poll_interval_seconds" in data:
            try:
                n = float(data["poll_interval_seconds"])
                self._runtime_poll_interval = n if 2 <= n <= 30 else None
            except (TypeError, ValueError):
                self._runtime_poll_interval = None
        if self._runtime_poll_interval != prev_poll and self._runtime_poll_interval:
            self.logger().info(f"[runtime] poll interval → {self._runtime_poll_interval:.0f}s")

        # Unfollowed traders: stop NEW opens/flips immediately (closes still tracked)
        unf: set = set()
        raw_unf = data.get("unfollowed_traders")
        if raw_unf is None:
            raw_unf = data.get("disabled_traders")
        if isinstance(raw_unf, list):
            for x in raw_unf:
                try:
                    n = int(x)
                    if n >= 1:
                        unf.add(n)
                except (TypeError, ValueError):
                    continue
        # Optional address list
        raw_addrs = data.get("unfollowed_addresses")
        if isinstance(raw_addrs, list):
            for a in raw_addrs:
                try:
                    addr = str(a).strip().lower()
                    for i, configured in enumerate(self.config.trader_addresses or [], 1):
                        if str(configured).strip().lower() == addr:
                            unf.add(i)
                            break
                except Exception:
                    pass
        self._unfollowed_traders = unf

        prev_slots = dict(getattr(self, "_trader_follow_slots", {}) or {})
        slots: Dict[int, int] = {}
        raw_slots = data.get("trader_follow_slots")
        if raw_slots is None:
            raw_slots = data.get("trader_max_positions")
        if isinstance(raw_slots, dict):
            for k, v in raw_slots.items():
                try:
                    num = int(k)
                    nslot = int(v)
                    if num >= 1 and 1 <= nslot <= 3:
                        slots[num] = nslot
                except (TypeError, ValueError):
                    continue
        self._trader_follow_slots = slots
        if slots != prev_slots:
            parts = ", ".join(f"T#{n}={slots[n]}" for n in sorted(slots)) or "yml default"
            self.logger().info(f"[runtime] follow slots → {parts} (each trade = pot/N)")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SLOTS':<8} "
                f"{'':6} {'':7} {parts}{RESET}"
            )

        if prev != self._runtime_copy_shorts:
            mode = "LONGS ONLY" if not self._allow_shorts() else "LONGS + SHORTS"
            self.logger().info(f"[runtime] copy mode → {mode}")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'MODE':<8} "
                f"{'':6} {'':7} {mode}{RESET}"
            )
        raw_block = data.get("sl_block")
        if isinstance(raw_block, dict):
            parsed = self._parse_sl_block_map(raw_block)
            self._sl_block = parsed

        now = time.time()
        last = float(getattr(self, "_last_sql_persist", 0) or 0)
        if force or now - last >= 30:
            self._last_sql_persist = now
            try:
                self._persist_copy_risk_sql()
            except Exception as e:
                self.logger().warning(f"[sql] persist copy risk failed: {e}")

        if unf != prev_unf:
            if unf:
                labels = ", ".join(f"T#{n}" for n in sorted(unf))
                self.logger().info(f"[runtime] unfollowed traders → {labels} (flatten our clips)")
                trade_log(
                    f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'UNFOLLOW':<8} "
                    f"{'':6} {'':7} {labels} — mute, flatten our clips{RESET}"
                )
            else:
                self.logger().info("[runtime] unfollowed traders → none (all followed)")
                trade_log(
                    f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'FOLLOW':<8} "
                    f"{'':6} {'':7} all traders — copy resumes{RESET}"
                )
            # Drop deferred opens for newly unfollowed leaders and flatten our mirrors.
            for n in unf - prev_unf:
                try:
                    idx = n - 1
                    if 0 <= idx < len(self.config.trader_addresses or []):
                        addr = self.config.trader_addresses[idx]
                        if addr in self._deferred_opens:
                            self._deferred_opens[addr] = {}
                        pairs = []
                        owners = []
                        for pair, m in list((self._our_mirrors or {}).items()):
                            owner = (m or {}).get("trader")
                            owners.append(f"{pair}:{owner}")
                            hit = False
                            if owner == addr or owner == n or str(owner) == str(n):
                                hit = True
                            elif isinstance(owner, str):
                                if owner.strip().lower() == str(addr).strip().lower():
                                    hit = True
                                else:
                                    tn = self._trader_num_for_address(owner)
                                    if tn == n:
                                        hit = True
                            if hit:
                                pairs.append(pair)
                        if not pairs:
                            self.logger().info(
                                f"[runtime] mute T#{n} — no mirrors tagged to this leader "
                                f"(mirrors={owners})"
                            )
                            trade_log(
                                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'UNFOLLOW':<8} "
                                f"{'':6} {'T#'+str(n):<7} muted — no clips tagged T#{n}{RESET}"
                            )
                        for pair in pairs:
                            self.logger().info(f"[runtime] mute T#{n} — closing {pair}")
                            try:
                                self._close_mirror(pair, reason=f"muted T#{n}")
                            except Exception as e:
                                self.logger().error(f"[runtime] mute close {pair} failed: {e}")
                except Exception as e:
                    self.logger().error(f"[runtime] mute flatten T#{n} failed: {e}")

    def _trader_num_for_address(self, address: str) -> Optional[int]:
        try:
            return self.config.trader_addresses.index(address) + 1
        except (ValueError, AttributeError):
            return None

    def _is_trader_followed(self, address: Optional[str] = None, trader_num: Optional[int] = None) -> bool:
        """False if dashboard unfollowed this leader (no new opens/flips)."""
        unf = getattr(self, "_unfollowed_traders", None) or set()
        if not unf:
            return True
        if trader_num is None and address:
            trader_num = self._trader_num_for_address(address)
        if trader_num is None:
            return True
        try:
            return int(trader_num) not in unf
        except (TypeError, ValueError):
            return True

    def _position_leverage(self, pos: dict, coin: Optional[str] = None) -> int:
        """Extract leverage from HL position payload or mirror dict."""
        lev = pos.get("leverage")
        if isinstance(lev, dict):
            try:
                v = int(lev.get("value") or 0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        if lev is not None and not isinstance(lev, dict):
            try:
                v = int(lev)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        # Conservative for margin-used: lower lev → higher margin estimate
        floor = int(self.config.min_leverage or 1)
        if coin:
            return max(floor, min(self._max_leverage_for(coin), floor) or floor)
        return max(1, floor)

    def _mirror_margin_usd(self, mirror: dict) -> Decimal:
        """
        Margin USD locked in a registered mirror.

        Prefer explicit margin / on-chain marginUsed. If estimating from size,
        use a *conservative* (not-too-high) leverage so used is not understated —
        understating used is what let Full scale spend other traders' free pot.
        """
        try:
            if mirror.get("margin") is not None and Decimal(str(mirror["margin"])) > 0:
                return Decimal(str(mirror["margin"]))
            if mirror.get("margin_used") is not None and Decimal(str(mirror["margin_used"])) > 0:
                return Decimal(str(mirror["margin_used"]))
            amount = Decimal(str(mirror.get("amount") or 0))
            entry = Decimal(str(mirror.get("entry_price") or 0))
            if amount <= 0 or entry <= 0:
                return Decimal("0")
            lev_raw = mirror.get("leverage")
            try:
                lev = int(lev_raw) if lev_raw not in (None, "", 0, "0") else 0
            except (TypeError, ValueError):
                lev = 0
            if lev <= 0:
                # Unknown lev: assume min_leverage so margin is NOT understated
                lev = max(1, int(self.config.min_leverage or 1))
            return amount * entry / Decimal(str(lev))
        except Exception:
            return Decimal("0")

    def _trader_pot_usd(self) -> Decimal:
        """Equal equity pot for one configured trader (crypto pool only).

        Uses sizing equity (excludes positive unrealized PnL when risk is on)
        so paper profits do not enlarge Full-scale / pot math.
        """
        funds = self._sizing_equity()
        free = self._get_free_balance()
        if funds <= 0:
            funds = free if free > 0 else Decimal("0")
        funds = funds * Decimal(str(1.0 - self.config.commodity_allocation_pct))
        return funds / Decimal(str(self._n_traders()))

    def _sync_mirror_from_chain(self, coin: str, pair: str, mirror: dict) -> dict:
        """Refresh size / entry / leverage / marginUsed from our HL account."""
        our = self._fetch_our_onchain_positions() or {}
        pos = our.get(coin)
        if pos is None:
            for c, pdata in our.items():
                if c.upper() == coin.upper():
                    pos = pdata
                    coin = c
                    break
        if not pos:
            return mirror
        try:
            szi = abs(float(pos.get("szi") or 0))
            if szi <= 0:
                return mirror
            mirror["amount"] = Decimal(str(szi))
            mirror["coin"] = coin
            if pos.get("entryPx") is not None:
                mirror["entry_price"] = Decimal(str(pos.get("entryPx") or 0))
            lev = self._position_leverage(pos, coin)
            if lev > 0:
                mirror["leverage"] = lev
            mu = pos.get("marginUsed")
            if mu is not None and float(mu) > 0:
                mirror["margin"] = Decimal(str(mu))
                mirror["margin_used"] = Decimal(str(mu))
            self._our_mirrors[pair] = mirror
        except Exception as e:
            self.logger().warning(f"[force-scale] chain sync failed for {coin}: {e}")
        return mirror

    def _margin_used_by_trader(self) -> Dict[str, Decimal]:
        """
        Map trader_addr → estimated margin in our mirrors.
        Unattributed mirrors (restored/orphan) are tracked under key '' .
        """
        used: Dict[str, Decimal] = {a: Decimal("0") for a in (self.config.trader_addresses or [])}
        used[""] = Decimal("0")
        for m in self._our_mirrors.values():
            mar = self._mirror_margin_usd(m)
            t = m.get("trader")
            if t in used and t not in ("", "restored", "reconciled", "orphan", "dashboard", "unknown"):
                used[t] = used.get(t, Decimal("0")) + mar
            else:
                used[""] = used.get("", Decimal("0")) + mar
        return used

    def _remaining_trader_funds(self, trader_addr: str) -> Decimal:
        """
        Cash Full scale may add to this trader's open mirror.

        Mute released pots are not reserved. Other unmuted traders still
        keep unused room in their pot. Whatever is left in wallet free
        (equity − margin, not just HL withdrawable) is dumped into the add.
        """
        if not self._is_trader_followed(address=trader_addr):
            return Decimal("0")
        pot = self._trader_pot_usd()
        used_map = self._margin_used_by_trader()
        my_used = used_map.get(trader_addr, Decimal("0"))
        remaining_pot = pot - my_used
        if remaining_pot < 0:
            remaining_pot = Decimal("0")

        others_reserved = Decimal("0")
        for addr in (self.config.trader_addresses or []):
            if addr == trader_addr:
                continue
            if not self._is_trader_followed(address=addr):
                continue
            others_reserved += max(Decimal("0"), pot - used_map.get(addr, Decimal("0")))
        unattributed = used_map.get("", Decimal("0"))

        free = self._get_free_balance()
        # Paper unused-pot of other live traders is not cash. Mute leftover
        # already sits in `free`. Full scale dumps all tradable free into this
        # mirror (keep $0.50 dust). Don't let others_reserved zero a $1 wallet.
        remaining = free - Decimal("0.50") if free > Decimal("1") else free
        if remaining < 0:
            remaining = Decimal("0")
        free_for_me = remaining

        self.logger().info(
            f"[force-scale] pot math {trader_addr[:10]}…: pot=${float(pot):.2f} "
            f"my_used=${float(my_used):.2f} remaining_pot=${float(remaining_pot):.2f} "
            f"others_reserved=${float(others_reserved):.2f} free=${float(free):.2f} "
            f"free_for_me=${float(free_for_me):.2f} → alloc=${float(remaining):.2f}"
            f"{f' unattr=${float(unattributed):.2f}' if unattributed > 0 else ''}"
        )
        return remaining

    def _process_force_scales(self):
        """
        Dashboard "Full scale": add remaining trader-pot funds to an open mirror.
        File format (JSON):
          {"coin": "SOL"}  or  {"coin": "SOL", "trader": 1}
        Places a same-side market OPEN for the leftover pot (capped by free margin).
        """
        cmd = self._sql_cmd(("scale",))
        if not cmd:
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        self._sql_done(cmd)

        coin = str(data.get("coin") or "").strip()
        if not coin or ":" in coin:
            self.logger().warning(f"[force-scale] invalid/unsupported coin: {coin!r}")
            return

        # Resolve open mirror for this coin
        pair = None
        mirror = None
        for p, m in list(self._our_mirrors.items()):
            mcoin = (m.get("coin") or p.replace("-USD", ""))
            if mcoin.upper() == coin.upper() or p.upper() == f"{coin.upper()}-USD":
                pair, mirror = p, m
                coin = m.get("coin") or coin
                break

        if mirror is None:
            # Fall back to on-chain position if bot state lost it
            our = self._fetch_our_onchain_positions() or {}
            pos = None
            for c, pdata in our.items():
                if c.upper() == coin.upper():
                    pos = pdata
                    coin = c
                    break
            if pos is not None and abs(float(pos.get("szi") or 0)) > 0:
                szi = float(pos["szi"])
                pair = f"{coin}-USD"
                side = "LONG" if szi > 0 else "SHORT"
                lev = self._position_leverage(pos, coin)
                mu = pos.get("marginUsed")
                mirror = {
                    "trader": "dashboard",
                    "coin": coin,
                    "side": side,
                    "amount": Decimal(str(abs(szi))),
                    "entry_price": Decimal(str(pos.get("entryPx") or 0)),
                    "leverage": lev,
                    "margin": Decimal(str(mu)) if mu is not None else None,
                    "entry_time": "scale-restore",
                    "order_id": None,
                }
                self._our_mirrors[pair] = mirror
                self.logger().info(
                    f"[force-scale] Registered on-chain {side} {coin} for scale-up"
                )
            else:
                self.logger().warning(
                    f"[force-scale] no open mirror for {coin} — nothing to scale"
                )
                trade_log(
                    f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
                    f"{'':6} {coin:<7} failed — no open position{RESET}"
                )
                return

        # Resolve owner trader — MUST be a real configured leader (no free-for-all)
        trader_num = data.get("trader")
        trader_addr = None
        existing = mirror.get("trader")
        if isinstance(existing, str) and existing in (self.config.trader_addresses or []):
            trader_addr = existing
        if trader_num is not None:
            try:
                idx = int(trader_num) - 1
                if 0 <= idx < len(self.config.trader_addresses):
                    dash_addr = self.config.trader_addresses[idx]
                    # Prefer dashboard trader when mirror is unowned / restored;
                    # if already owned by another leader, keep owner (don't re-pot).
                    if trader_addr is None or trader_addr == dash_addr:
                        trader_addr = dash_addr
                        mirror["trader"] = trader_addr
                    elif trader_addr != dash_addr:
                        self.logger().warning(
                            f"[force-scale] dashboard T#{trader_num} != mirror owner "
                            f"{trader_addr[:10]}… — using mirror owner pot only"
                        )
            except (TypeError, ValueError):
                pass

        if not trader_addr or trader_addr not in (self.config.trader_addresses or []):
            self.logger().warning(
                f"[force-scale] {coin}: no trader attribution — refuse scale "
                f"(would risk spending other pots). Pass trader # from dashboard."
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
                f"{(mirror.get('side') or ''):<6} {coin:<7} failed — unknown trader "
                f"(refusing free-for-all){RESET}"
            )
            return

        # Refresh size/lev/margin from chain BEFORE pot math (restored mirrors lack lev)
        mirror = self._sync_mirror_from_chain(coin, pair, mirror)
        coin = mirror.get("coin") or coin
        side = mirror.get("side") or "LONG"

        if not self._connector_is_ready():
            self.logger().warning(
                f"[force-scale] connector not ready — re-queue {coin}"
            )
            try:
                with open(FORCE_SCALE_FILE, "w") as f:
                    json.dump({"coin": coin, "trader": trader_num, "retry": True}, f)
            except Exception:
                pass
            return

        if not self._ensure_pair_ready(pair):
            self.logger().warning(
                f"[force-scale] market not ready for {pair} — re-queue"
            )
            try:
                with open(FORCE_SCALE_FILE, "w") as f:
                    json.dump({"coin": coin, "trader": trader_num, "retry": True}, f)
            except Exception:
                pass
            return

        remaining = self._remaining_trader_funds(trader_addr)
        t_label = f"T#{self.config.trader_addresses.index(trader_addr) + 1}"

        # Dust threshold — don't place tiny orders
        if remaining < Decimal("1"):
            self.logger().info(
                f"[force-scale] {coin}: remaining pot ${float(remaining):.2f} "
                f"< $1 — nothing to add ({t_label})"
            )
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
                f"{side:<6} {coin:<7} skip — no remaining funds "
                f"({t_label} pot left ${float(remaining):.2f}){RESET}"
            )
            return

        # Use actual position leverage (from chain sync); never invent max lev for size
        leverage = int(mirror.get("leverage") or 0)
        if leverage <= 0:
            leverage = self._max_leverage_for(coin)
        try:
            self.connectors[self.config.connector_name].set_leverage(pair, leverage)
        except Exception as e:
            self.logger().warning(f"[force-scale] set_leverage failed for {pair}: {e}")

        mid = self._get_mid_price(pair) or Decimal(str(mirror.get("entry_price") or 0))
        if mid <= 0:
            self.logger().warning(f"[force-scale] no price for {pair} — abort")
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
                f"{side:<6} {coin:<7} failed — no mid price{RESET}"
            )
            return

        allocation = remaining
        pot = self._trader_pot_usd()
        used_map = self._margin_used_by_trader()
        my_used_total = used_map.get(trader_addr, Decimal("0"))
        if allocation < Decimal("1"):
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
                f"{side:<6} {coin:<7} skip — no tradable free "
                f"({t_label} used ${float(my_used_total):.2f} / pot ${float(pot):.2f} "
                f"free ${float(self._get_free_balance()):.2f}){RESET}"
            )
            return

        notional = allocation * Decimal(str(leverage))
        amount = (notional / mid).quantize(Decimal("0.00001"))
        if amount <= 0:
            self.logger().warning(f"[force-scale] amount too small for {pair}: {amount}")
            return

        prev_amount = Decimal(str(mirror.get("amount") or 0))
        free = self._get_free_balance()

        self.logger().info(
            f"[force-scale] FULL SCALE {t_label} {side} {coin}: "
            f"+margin=${float(allocation):.2f} amount=+{amount} @ ~${float(mid):,.6f} "
            f"| pot=${float(pot):.2f} my_used=${float(my_used_total):.2f} "
            f"free=${float(free):.2f} lev={leverage}x "
            f"prev_size={float(prev_amount):.5f} → {float(prev_amount + amount):.5f}"
        )
        trade_log(
            f"{CYAN}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
            f"{side:<6} {coin:<7} {t_label} full scale — "
            f"add margin=${float(allocation):.2f} "
            f"(only {t_label} pot; used ${float(my_used_total):.2f}/${float(pot):.2f}){RESET}"
        )

        # Bump registered size BEFORE order so close path always covers the add
        mirror["amount"] = prev_amount + amount
        mirror["leverage"] = leverage
        # Blend entry toward mid weighted by size (approx average entry)
        try:
            if prev_amount > 0 and mirror.get("entry_price"):
                old_e = Decimal(str(mirror["entry_price"]))
                new_e = (old_e * prev_amount + mid * amount) / (prev_amount + amount)
                mirror["entry_price"] = new_e
            else:
                mirror["entry_price"] = mid
        except Exception:
            mirror["entry_price"] = mid
        self._our_mirrors[pair] = mirror

        try:
            if side == "LONG":
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
            if order_id:
                self._pending_order_ids.add(order_id)
                mirror["order_id"] = order_id
        except Exception as e:
            self.logger().error(f"[force-scale] order failed for {pair}: {e}")
            # Keep inflated amount — on-chain size may still grow; reconciler helps
            trade_log(
                f"{YELLOW}{BOLD}{_now():<22} {'DASHBOARD':<14} {'SCALE':<8} "
                f"{side:<6} {coin:<7} order error: {e}{RESET}"
            )
            return

        total_amount = Decimal(str(mirror["amount"]))
        total_margin = self._mirror_margin_usd(mirror)
        trade_log(
            f"{RED}{BOLD}"
            f"{_now():<22} {'WE COPY':<14} {'SCALED':<8} {side:<6} {coin:<7} "
            f"{float(amount):>10.5f}  ${float(mid):>14,.6f}  "
            f"margin=${float(allocation):.2f}  {leverage}x  notional=${float(notional):.2f}  "
            f"total={float(total_amount):.5f}  total_margin=${float(total_margin):.2f}"
            f"{RESET}"
        )

    # ── Reconciliation / orphan audit (roadmap P3) ────────────────────────────

    def _fetch_our_onchain_positions(self) -> Optional[Dict[str, dict]]:
        """
        Return {coin: position_dict} for our non-zero main-dex positions, or None on error.
        """
        try:
            addr = self.connectors[self.config.connector_name].hyperliquid_perpetual_address
            data = self._hl_post({"type": "clearinghouseState", "user": addr})
            return {
                p["position"]["coin"]: p["position"]
                for p in data.get("assetPositions", [])
                if float(p["position"]["szi"]) != 0 and ":" not in p["position"]["coin"]
            }
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                self._api_backoff_until = time.time() + 30
                self.logger().warning("[rate-limit] 429 on reconcile — backing off 30s")
            return None
        except Exception:
            return None

    def _trader_holdings_by_side(
        self, fresh_positions: Dict[str, Dict[str, dict]]
    ) -> Dict[str, Dict[str, str]]:
        """
        coin → {"sides": set[str], "owners": {side: trader_addr}}
        """
        out: Dict[str, dict] = {}
        for address, pos_map in fresh_positions.items():
            for coin, p in pos_map.items():
                side = p.get("side")
                if not side:
                    continue
                entry = out.setdefault(coin, {"sides": set(), "owners": {}})
                entry["sides"].add(side)
                # first owner of this side wins for attribution
                entry["owners"].setdefault(side, address)
        return out

    def _reconcile_positions(self, fresh_positions: Dict[str, Dict[str, dict]]):
        """
        Keep our on-chain positions aligned with tracked leaders.

        - Ghost mirrors: in _our_mirrors but flat on-chain → drop local state
        - Orphans: we hold coin+side that no leader holds → close (if close_orphans)
        - Match: sync size from chain and attribute owner when possible

        Never closes orphans if any leader fetch failed this tick (avoids false closes).
        """
        our_raw = self._fetch_our_onchain_positions()
        if our_raw is None:
            return  # API error — do not change state

        # Drop local mirrors for positions already flat on-chain
        dropped_idle = False
        for pair in list(self._our_mirrors.keys()):
            coin = self._our_mirrors[pair].get("coin") or pair.replace("-USD", "")
            if coin not in our_raw:
                # Race: right after OPEN the order may not be in clearinghouse yet
                m = self._our_mirrors.get(pair) or {}
                et = str(m.get("entry_time") or "")
                # Protect freshly registered opens for ~20s (full-btc / copy)
                age_ok = True
                try:
                    if et and "UTC" in et:
                        # entry_time format: "2026-08-02 22:20:17 UTC"
                        from datetime import datetime as _dt
                        opened = _dt.strptime(et.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=timezone.utc
                        )
                        age_ok = (time.time() - opened.timestamp()) > 20
                except Exception:
                    age_ok = True
                if (m.get("full_btc") or m.get("full_eth") or m.get("full_allin")) and not age_ok:
                    self.logger().info(
                        f"[orphan] skip ghost-clear {pair} — full-allin open still settling"
                    )
                    continue
                if not age_ok and m.get("order_id"):
                    self.logger().info(
                        f"[orphan] skip ghost-clear {pair} — recent open still settling"
                    )
                    continue
                self.logger().info(
                    f"[orphan] Ghost mirror {pair} — already flat on-chain, clearing local state"
                )
                m = self._our_mirrors.pop(pair, None) or {}
                self._close_in_flight.discard(pair)
                self._close_backoff_until.pop(pair, None)
                dropped_idle = True
                if m.get("_closing"):
                    coin = m.get("coin") or pair.replace("-USD", "")
                    side = m.get("side") or ""
                    amt = float(m.get("_dust_real_amount") or m.get("amount") or 0)
                    entry = float(m.get("entry_price") or 0)
                    try:
                        exit_px = float(m.get("_sent_exit_px")) if m.get("_sent_exit_px") is not None else entry
                    except (TypeError, ValueError):
                        exit_px = entry
                    pnl = m.get("_sent_pnl_pct")
                    pnl_str = f"  PnL~{float(pnl):+.2f}%" if pnl is not None else ""
                    _tn = self._trader_num_for_address(str(m.get("trader") or ""))
                    trade_log(
                        f"{RED}{BOLD}"
                        f"{_now():<22} {'WE CLOSE':<14} {'CLOSED':<8} {side:<6} {coin:<7} "
                        f"{amt:>10.5f}  ${float(exit_px):>14,.6f}  "
                        f"entry=${entry:,.6f}  opened={m.get('entry_time', '?')}{pnl_str}  filled on-chain"
                        f"{('  T#' + str(_tn)) if _tn else ''}"
                        f"{RESET}"
                    )
                    trade_log("─" * 80)

        if dropped_idle:
            try:
                self._prune_idle_markets(force=True)
            except Exception as e:
                self.logger().warning(f"[markets] prune after close failed: {e}")

        if not our_raw:
            return

        complete = len(fresh_positions) >= len(self.config.trader_addresses)
        if not complete:
            missing = len(self.config.trader_addresses) - len(fresh_positions)
            self.logger().warning(
                f"[orphan] Incomplete leader snapshot ({missing} missing) — "
                f"skipping orphan closes this tick"
            )
            return

        holdings = self._trader_holdings_by_side(fresh_positions)
        is_startup = not self._startup_orphan_audit_done
        if is_startup:
            self.logger().info(
                f"[orphan] Startup audit — our positions: {len(our_raw)}  "
                f"leaders fully fetched: {len(fresh_positions)}"
            )

        closed_n = 0
        kept_n = 0

        for coin, pos_data in our_raw.items():
            pair = f"{coin}-USD"
            szi = float(pos_data["szi"])
            side = "LONG" if szi > 0 else "SHORT"
            amount = Decimal(str(abs(szi)))
            entry_px = Decimal(str(pos_data.get("entryPx", 0)))

            if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
                self._our_mirrors.pop(pair, None)
                kept_n += 1
                if is_startup:
                    self.logger().info(
                        f"[orphan] KEEP {side} {amount} {pair} — reserved for HL swing"
                    )
                continue

            already_closing = any(ctx["pair"] == pair for ctx in self._pending_closes.values())
            if already_closing:
                continue

            h = holdings.get(coin)
            leaders_have_same_side = bool(h and side in h["sides"])

            if leaders_have_same_side:
                owner = h["owners"].get(side, "unknown")
                lev = self._position_leverage(pos_data, coin)
                mu = pos_data.get("marginUsed")
                margin = Decimal(str(mu)) if mu is not None and float(mu) > 0 else None
                if pair not in self._our_mirrors:
                    self._our_mirrors[pair] = {
                        "trader": owner,
                        "coin": coin,
                        "pair": pair,
                        "side": side,
                        "amount": amount,
                        "entry_price": entry_px,
                        "leverage": lev,
                        "margin": margin,
                        "entry_time": "restored" if is_startup else "reconciled",
                        "order_id": None,
                    }
                else:
                    m = self._our_mirrors[pair]
                    m["pair"] = pair
                    m["amount"] = amount
                    m["side"] = side
                    m["leverage"] = lev or m.get("leverage")
                    if margin is not None:
                        m["margin"] = margin
                    # Never reassign ownership of manual-only full-scale / add-ETH
                    if not self._is_manual_close_only(mirror=m):
                        if m.get("trader") in ("", "restored", "reconciled", "orphan"):
                            m["trader"] = owner
                kept_n += 1
                if is_startup:
                    self.logger().info(
                        f"[orphan] KEEP {side} {amount} {pair} — leader {owner[:10]}… still holds"
                    )
                continue

            # Orphan: no tracked leader holds this coin+side
            if not self.config.close_orphans:
                self.logger().warning(
                    f"[orphan] {side} {amount} {pair} has no matching leader — "
                    f"close_orphans=false, leaving open"
                )
                continue

            # Full-scale / add-ETH: never auto-orphan-close
            if pair in self._our_mirrors and self._is_manual_close_only(pair=pair):
                m = self._our_mirrors[pair]
                m["amount"] = amount
                m["side"] = side
                kept_n += 1
                if is_startup:
                    self.logger().info(
                        f"[orphan] KEEP {side} {amount} {pair} — manual_close_only "
                        f"(dashboard close only)"
                    )
                continue

            if pair not in self._our_mirrors:
                self._our_mirrors[pair] = {
                    "trader": "orphan",
                    "coin": coin,
                    "pair": pair,
                    "side": side,
                    "amount": amount,
                    "entry_price": entry_px,
                    "entry_time": "orphan",
                    "order_id": None,
                }
            else:
                self._our_mirrors[pair]["amount"] = amount
                self._our_mirrors[pair]["side"] = side

            if self._close_blocked(pair):
                continue
            why = "no leader holds this coin" if not h else f"leaders only hold {sorted(h['sides'])}"
            self.logger().warning(
                f"[orphan] CLOSE {side} {amount} {pair} — {why}"
            )
            trade_log(
                f"{YELLOW}{BOLD}"
                f"{_now():<22} {'ORPHAN':<14} {'CLOSE':<8} {side:<6} {coin:<7} "
                f"{float(amount):>10.5f}  ${float(entry_px):>14,.6f}  {why}"
                f"{RESET}"
            )
            self._close_mirror(pair, reason=f"orphan — {why}")
            closed_n += 1

        if is_startup:
            self._startup_orphan_audit_done = True
            self.logger().info(
                f"[orphan] Startup audit done — kept={kept_n}  closed={closed_n}  "
                f"close_orphans={self.config.close_orphans}"
            )
            trade_log(
                f"{CYAN}[orphan] Startup audit done — kept={kept_n} closed={closed_n}{RESET}"
            )
    # ── Trader monitoring ─────────────────────────────────────────────────────

    def _positions_from_state(self, data: dict) -> Dict[str, dict]:
        result = {}
        for entry in (data or {}).get("assetPositions", []) or []:
            pos = entry.get("position", {}) or {}
            coin = pos.get("coin")
            szi = float(pos.get("szi", 0) or 0)
            entry_px = float(pos.get("entryPx") or 0)
            lev = pos.get("leverage", {})
            leverage = int(lev.get("value", 1)) if isinstance(lev, dict) else 1
            if coin and abs(szi) > 1e-9:
                pair = COIN_TO_PAIR.get(coin)
                if pair is None and ":" not in coin:
                    pair = f"{coin}-USD"
                result[coin] = {
                    "side": "LONG" if szi > 0 else "SHORT",
                    "size": abs(szi),
                    "entry_px": entry_px,
                    "leverage": leverage,
                    "pair": pair,
                }
        return result

    def _merge_pos_dicts(self, base: Dict[str, dict], extra: Dict[str, dict]) -> Dict[str, dict]:
        out = {k: dict(v) for k, v in (base or {}).items()}
        for coin, p in (extra or {}).items():
            if coin not in out:
                out[coin] = dict(p)
                continue
            a = out[coin]
            sa = a["size"] if a.get("side") == "LONG" else -a["size"]
            sb = p["size"] if p.get("side") == "LONG" else -p["size"]
            net = sa + sb
            if abs(net) < 1e-9:
                out.pop(coin, None)
                continue
            tot = abs(sa) + abs(sb)
            if tot > 0:
                a["entry_px"] = (float(a.get("entry_px") or 0) * abs(sa) + float(p.get("entry_px") or 0) * abs(sb)) / tot
            a["side"] = "LONG" if net > 0 else "SHORT"
            a["size"] = abs(net)
            a["leverage"] = max(int(a.get("leverage") or 1), int(p.get("leverage") or 1))
            pair = COIN_TO_PAIR.get(coin)
            if pair is None and ":" not in str(coin):
                pair = f"{coin}-USD"
            a["pair"] = a.get("pair") or pair
            out[coin] = a
        return out

    def _subaccount_addrs(self, address: str) -> list:
        """Leader subaccount addresses. subAccounts is weight 20 — cache 5 min."""
        now = time.time()
        hit = self._subacct_cache.get(address)
        if hit and now - float(hit[0]) < self._subacct_ttl_s:
            return list(hit[1])
        try:
            rows = self._hl_post({"type": "subAccounts", "user": address}) or []
        except Exception:
            return list(hit[1]) if hit else []
        if not isinstance(rows, list):
            rows = []
        addrs = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            u = str(row.get("subAccountUser") or row.get("subAccount") or "").strip()
            if u and u.lower() != str(address).lower():
                addrs.append(u)
        self._subacct_cache[address] = (now, addrs)
        return list(addrs)

    def _fetch_trader_positions(self, address: str) -> Dict[str, dict]:
        """Open perps on this leader, including Hyperliquid subaccounts."""
        data = self._hl_post({"type": "clearinghouseState", "user": address})
        result = self._positions_from_state(data or {})
        for sub in self._subaccount_addrs(address):
            try:
                extra = self._positions_from_state(
                    self._hl_post({"type": "clearinghouseState", "user": sub}) or {}
                )
            except Exception:
                extra = {}
            if extra:
                result = self._merge_pos_dicts(result, extra)
        return result

    def _check_hip3_fills(self, address: str):
        """Poll userFills for HIP-3 (xyz:) activity, log it, and copy trades via SDK."""
        since_ts = self._hip3_last_fill_ts[address]
        fills = self._hl_post({"type": "userFills", "user": address, "startTime": since_ts + 1})

        hip3_fills = [f for f in fills if ":" in f.get("coin", "")]
        if not hip3_fills:
            if fills:
                self._hip3_last_fill_ts[address] = max(f["time"] for f in fills)
            return

        self._hip3_last_fill_ts[address] = max(f["time"] for f in fills)

        trader_num = self.config.trader_addresses.index(address) + 1
        trader_tag = f"TRADER #{trader_num}"

        # Group fills by coin+direction+time_bucket, dedup by exact fill key
        grouped = {}
        for f in sorted(hip3_fills, key=lambda x: x["time"]):
            coin = f["coin"]
            direction = f.get("dir", "?")
            t_ms = f["time"]
            dedup_key = (address, coin, direction, t_ms)
            if dedup_key in self._hip3_seen:
                continue
            self._hip3_seen.add(dedup_key)
            bucket = f"{coin}|{direction}|{t_ms // 10000}"
            if bucket not in grouped:
                grouped[bucket] = {"coin": coin, "dir": direction, "size": 0.0, "px": float(f["px"]), "time": t_ms}
            grouped[bucket]["size"] += float(f["sz"])

        for entry in grouped.values():
            coin = entry["coin"]
            direction = entry["dir"]
            size = entry["size"]
            px = entry["px"]
            is_open = "Open" in direction
            is_long = "Long" in direction
            action = "OPENED" if is_open else "CLOSED"
            side = "LONG" if is_long else "SHORT"
            label = coin.replace("xyz:", "")

            # Log trader activity in purple
            trade_log(
                f"{PURPLE}{BOLD}"
                f"{_now():<22} {trader_tag:<14} {action:<8} {side:<6} {label:<12} "
                f"{size:>10.4f}  ${px:>12,.4f}  [commodity]"
                f"{RESET}"
            )

            # Mirror the trade via SDK
            if is_open:
                self._open_commodity(coin, is_long, px)
            else:
                self._close_commodity(coin)

    def _check_trader(self, address: str, new_pos: Dict[str, dict]):
        old_pos = self._trader_positions[address]

        trader_num = self.config.trader_addresses.index(address) + 1
        trader_tag = f"TRADER #{trader_num}"
        followed = self._is_trader_followed(address=address, trader_num=trader_num)

        # ── First poll: snapshot. Skip names outside this trader's slot budget.
        # Top-N (empty slots) are left eligible so we can fill 0/2 instead of
        # waiting forever for a brand-new open.
        if address not in self._initialized_traders:
            self._initialized_traders.add(address)
            chosen = self._select_copy_coins(address, new_pos) if followed else set()
            max_pos = self._max_positions_for_trader(address)
            for coin in new_pos:
                if coin in chosen and followed:
                    self.logger().info(
                        f"[{address[:10]}] Startup snapshot: {coin} in top {max_pos} — will copy"
                    )
                    continue
                self._skipped_positions[address].add(coin)
                self.logger().info(
                    f"[{address[:10]}] Startup snapshot: {coin} already open — will not copy"
                )
            self._trader_positions[address] = new_pos
            return

        # Detect closed / side-flipped positions
        for coin, old in list(old_pos.items()):
            new = new_pos.get(coin)
            if new is None:
                # Trader closed this coin — clear skip flag so a fresh re-entry gets copied
                self._skipped_positions[address].discard(coin)
                self._deferred_opens.get(address, {}).pop(coin, None)
                pair = old.get("pair")
                # Calculate approximate PnL using current mid price
                _pnl_str = ""
                _entry = old.get("entry_px", 0)
                if pair and _entry and _entry > 0:
                    _mid = self._get_mid_price(pair)
                    if _mid and _mid > 0:
                        _lev = old.get("leverage", self.config.max_leverage)
                        if old["side"] == "LONG":
                            _pnl_pct = (float(_mid) - float(_entry)) / float(_entry) * 100 * float(_lev)
                        else:
                            _pnl_pct = (float(_entry) - float(_mid)) / float(_entry) * 100 * float(_lev)
                        _pnl_str = f"  PnL~{_pnl_pct:+.2f}%"
                trade_log(
                    f"{GREEN}{BOLD}{_now():<22} {trader_tag:<14} {'CLOSED':<8} {old['side']:<6} {coin:<7} "
                    f"{'':>10}  {'':>12}  {address[:10]}...{address[-6:]}{_pnl_str}{RESET}"
                )
                # Still exit our mirrors when leader closes — even if unfollowed
                try:
                    self._sl_block_release_flat(coin, None)
                except Exception:
                    pass
                if pair and self._can_close_or_flip_pair(address, pair, "close"):
                    self.logger().info(f"[{address[:10]}] Closed {old['side']} {coin} → closing our mirror")
                    self._close_mirror(pair, reason="trader closed")
            elif new["side"] != old["side"]:
                # Side flipped LONG↔SHORT
                pair = old.get("pair") or new.get("pair")
                self._note_flip(coin)
                trade_log(
                    f"{YELLOW}{BOLD}{_now():<22} {trader_tag:<14} {'FLIP':<8} {old['side']}→{new['side']:<4} {coin:<7} "
                    f"{new['size']:>10.4f}  ${new['entry_px']:>14,.6f}  side-flip{RESET}"
                )
                if pair and pair in self._our_mirrors:
                    if self._can_close_or_flip_pair(address, pair, "flip"):
                        self.logger().info(
                            f"[{address[:10]}] Side flip on {coin}: {old['side']}→{new['side']}"
                        )
                        # Close first ONLY — never open the other side in the same tick.
                        # Simultaneous CLOSE+OPEN sells race on HL one-way mode and can
                        # look like long+short or overshoot to flat.
                        self._close_mirror(pair, reason="side flip")
                        if not followed:
                            trade_log(
                                f"{'':>22} {'':>14} {'SKIP':<8} {new['side']:<6} {coin:<7} "
                                f"T#{trader_num} unfollowed — no flip open"
                            )
                        else:
                            # Re-open opposite only after flat (deferred open path)
                            self._deferred_opens.setdefault(address, {})[coin] = {
                                **new,
                                "_after_flip": True,
                                "_flip_from": old.get("side"),
                            }
                            trade_log(
                                f"{'':>22} {'':>14} {'FLIP→WAIT':<8} {new['side']:<6} {coin:<7} "
                                f"close first — open when flat"
                            )
                    # else: another trader owns this asset — must not flip their position
                elif pair and followed:
                    # We never held (or already closed) this coin — treat as a fresh open
                    # of the new side. Without this, flips after orphan-close / mid-session
                    # are logged but never mirrored (gap: coin stays in old_pos so not "NEW").
                    if self._flip_storm_skip(coin):
                        trade_log(
                            f"{'':>22} {'':>14} {'SKIP':<8} {new['side']:<6} {coin:<7} "
                            f"flip storm — wait"
                        )
                    elif new["side"] == "SHORT" and not self._allow_shorts():
                        self.logger().info(
                            f"[{address[:10]}] Flip→SHORT on {coin} skipped (longs only)"
                        )
                        trade_log(
                            f"{'':>22} {'':>14} {'SKIP':<8} SHORT  {coin:<7} longs only"
                        )
                    elif not self._can_open_pair(address, pair, coin, new["side"]):
                        pass  # locked / already logged
                    else:
                        self.logger().info(
                            f"[{address[:10]}] Flip {old['side']}→{new['side']} on {coin} "
                            f"with no local mirror — opening {new['side']} as new"
                        )
                        trade_log(
                            f"{'':>22} {'':>14} {'FLIP→OPEN':<8} {new['side']:<6} {coin:<7} "
                            f"no local mirror — treat as new open"
                        )
                        if not self._open_mirror(address, coin, new):
                            self._deferred_opens.setdefault(address, {})[coin] = new
                        else:
                            self._deferred_opens.get(address, {}).pop(coin, None)
                elif pair and not followed:
                    trade_log(
                        f"{'':>22} {'':>14} {'SKIP':<8} {new['side']:<6} {coin:<7} "
                        f"T#{trader_num} unfollowed — no open"
                    )

        # Detect new positions. If the leader dumps 10+ at once, copy only the
        # slot budget: keep existing mirrors, fill the rest by largest notional.
        selected = self._select_copy_coins(address, new_pos) if followed else set()
        max_pos = self._max_positions_for_trader(address)
        for coin, pos in new_pos.items():
            if coin not in old_pos:
                pair = pos.get("pair")
                trade_log(
                    f"{RED}{BOLD}{_now():<22} {trader_tag:<14} {'OPENED':<8} {pos['side']:<6} {coin:<7} "
                    f"{pos['size']:>10.4f}  ${pos['entry_px']:>14,.6f}  {address[:10]}...{address[-6:]}{RESET}"
                )
                if not followed:
                    trade_log(
                        f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} "
                        f"T#{trader_num} unfollowed — no copy"
                    )
                    continue
                if pair is None:
                    if ":" in coin:
                        pass  # xyz/commodity positions handled by _check_hip3_fills via SDK
                    else:
                        self.logger().info(f"[{address[:10]}] Skipping unsupported coin: {coin}")
                        trade_log(f"{'':>22} {'':>14} {'SKIP':<8} {'':6} {coin:<7} unsupported coin")
                    self._skipped_positions[address].add(coin)
                    continue
                if coin in self._skipped_positions[address]:
                    self.logger().info(f"[{address[:10]}] {coin} was skipped earlier — not entering late")
                    trade_log(f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} skipped earlier — not entering late")
                    continue
                # One account = one position per asset: refuse if locked by any trader
                if not self._can_open_pair(address, pair, coin, pos["side"]):
                    continue
                if pos["side"] == "SHORT" and not self._allow_shorts():
                    self.logger().info(f"[{address[:10]}] Skipping SHORT on {coin} (longs only)")
                    trade_log(f"{'':>22} {'':>14} {'SKIP':<8} SHORT  {coin:<7} longs only")
                    continue

                if coin not in selected:
                    ntl = self._leader_notional(pos)
                    self.logger().info(
                        f"[{address[:10]}] Skip {coin} — not in top {max_pos} by size "
                        f"(ntl=${ntl:.0f}; keep {sorted(selected) or 'existing'})"
                    )
                    trade_log(
                        f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} "
                        f"not top {max_pos} by size"
                    )
                    continue

                active = sum(1 for m in self._our_mirrors.values() if m.get("trader") == address)
                if active >= max_pos:
                    self.logger().info(
                        f"[{address[:10]}] Position cap reached ({active}/{max_pos}) — skipping {coin}"
                    )
                    trade_log(
                        f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} "
                        f"trader cap {max_pos} reached"
                    )
                    continue

                self.logger().info(
                    f"[{address[:10]}] NEW {pos['side']} {pos['size']:.4f} {coin} "
                    f"@ ${pos['entry_px']:,.6f} — opening mirror"
                )
                if not self._open_mirror(address, coin, pos):
                    self._deferred_opens.setdefault(address, {})[coin] = pos
                else:
                    self._deferred_opens.get(address, {}).pop(coin, None)

        # Empty slots: copy current top-N even if they were already open when
        # we attached (otherwise a subaccount book like T#2 stays 0 forever).
        if followed:
            n_held = sum(1 for m in self._our_mirrors.values() if m.get("trader") == address)
            if n_held < max_pos:
                for coin in selected:
                    if n_held >= max_pos:
                        break
                    pos = new_pos.get(coin)
                    if not pos:
                        continue
                    pair = pos.get("pair")
                    if not pair or pair in self._our_mirrors:
                        continue
                    if pair in (getattr(self, "_pending_closes", {}) or {}):
                        continue
                    self._skipped_positions[address].discard(coin)
                    if pos.get("side") == "SHORT" and not self._allow_shorts():
                        continue
                    if not self._can_open_pair(address, pair, coin, pos["side"]):
                        continue
                    if not self._ensure_pair_ready(pair):
                        self._deferred_opens.setdefault(address, {})[coin] = pos
                        continue
                    self.logger().info(
                        f"[{address[:10]}] FILL {pos['side']} {coin} "
                        f"(empty slot {n_held}/{max_pos}) — opening mirror"
                    )
                    if self._open_mirror(address, coin, pos):
                        n_held += 1
                        self._deferred_opens.get(address, {}).pop(coin, None)
                    else:
                        self._deferred_opens.setdefault(address, {})[coin] = pos

        # Retry opens deferred (connector not ready, or after side-flip close)
        if followed:
            for coin, deferred in list(self._deferred_opens.get(address, {}).items()):
                if coin not in new_pos:
                    self._deferred_opens[address].pop(coin, None)
                    continue
                # Refresh size/side from latest snapshot; keep private flags
                live = new_pos[coin]
                pos = dict(live)
                for k, v in deferred.items():
                    if str(k).startswith("_"):
                        pos[k] = v
                pair = pos.get("pair")
                if not pair:
                    self._deferred_opens[address].pop(coin, None)
                    continue
                # Side-flip: wait until close is done and account is flat on this coin
                if pos.get("_after_flip"):
                    if pair in self._our_mirrors:
                        continue
                    if any(
                        ctx.get("pair") == pair
                        for ctx in (getattr(self, "_pending_closes", {}) or {}).values()
                    ):
                        continue
                    try:
                        our = self._fetch_our_onchain_positions() or {}
                        on = our.get(coin)
                        if on is None:
                            for c, p in our.items():
                                if str(c).upper() == str(coin).upper():
                                    on = p
                                    break
                        if on is not None and abs(float(on.get("szi") or 0)) > 1e-8:
                            # still open on-chain — wait for close fill
                            continue
                    except Exception:
                        continue
                # Drop if asset locked (we still hold it / another leader owns it)
                if not self._can_open_pair(address, pair, coin, pos["side"]):
                    if pos.get("_after_flip"):
                        # close not finished yet — keep deferred
                        continue
                    self._deferred_opens[address].pop(coin, None)
                    continue
                force = bool(pos.get("_force"))
                if self._open_mirror(address, coin, pos, force=force):
                    self.logger().info(
                        f"[{address[:10]}] Deferred open filled for {coin}"
                        f"{' (after flip)' if pos.get('_after_flip') else ''}"
                    )
                    if pair in self._our_mirrors and force:
                        self._our_mirrors[pair]["trader"] = address
                        self._our_mirrors[pair]["force_follow"] = True
                    self._deferred_opens[address].pop(coin, None)
                else:
                    self._deferred_opens[address][coin] = pos
        else:
            # Unfollowed — drop any pending deferred opens for this leader
            if address in self._deferred_opens:
                self._deferred_opens[address] = {}

        self._trader_positions[address] = new_pos

    # ── Asset lock (one position per coin on a single HL account) ─────────────

    def _normalize_reserved_coin(self, coin: str) -> str:
        c = str(coin or "").upper().strip()
        if ":" in c:
            return c  # HIP-3 / xyz coins are never swing-reserved
        return c.replace("-USD", "").replace("-USDT", "")

    def _reserved_coins(self) -> set:
        """Optional reserved coins from yml / hl_reserved_coins.json only.

        Swing/Helliot now run on their own HL subaccounts, so their status
        files must not lock BTC/ETH/LTC on the copy wallet.
        """
        now = time.time()
        cached = getattr(self, "_reserved_coins_cache", None)
        cache_t = getattr(self, "_reserved_coins_ts", 0.0)
        if cached is not None and (now - cache_t) < 2.0:
            return cached
        coins = set()
        for c in (self.config.reserved_coins or []):
            n = self._normalize_reserved_coin(c)
            if n and ":" not in n:
                coins.add(n)
        try:
            with open(RESERVED_COINS_FILE, "r") as f:
                data = json.load(f) or {}
            extra = data.get("coins") or data.get("reserved_coins") or []
            if isinstance(extra, list):
                for c in extra:
                    n = self._normalize_reserved_coin(c)
                    if n and ":" not in n:
                        coins.add(n)
            for p in (data.get("pairs") or []):
                n = self._normalize_reserved_coin(str(p).split("-")[0])
                if n and ":" not in n:
                    coins.add(n)
        except Exception:
            pass
        self._reserved_coins_cache = coins
        self._reserved_coins_ts = now
        return coins

    def _is_reserved_coin(self, coin: str) -> bool:
        n = self._normalize_reserved_coin(coin)
        if not n or ":" in n:
            return False
        return n in self._reserved_coins()

    def _mirror_owner(self, pair: str) -> Optional[str]:
        """Return the leader address that owns our mirror on `pair`, or None."""
        m = self._our_mirrors.get(pair)
        if not m:
            return None
        owner = m.get("trader")
        if not owner or owner in ("restored", "reconciled", "orphan", "unknown", ""):
            return None
        return owner

    def _pair_is_locked(self, pair: str) -> bool:
        """True if we already have an open mirror on this trading pair."""
        if not self.config.one_position_per_asset:
            return False
        return pair in self._our_mirrors

    def _can_open_pair(self, trader_addr: str, pair: str, coin: str, side: str) -> bool:
        """
        Guard for opens: with one_position_per_asset, refuse any new open if we
        already hold that pair — even for the same trader (size add is not supported)
        or another trader (would interfere on a single account).
        """
        if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
            self.logger().info(
                f"[{trader_addr[:10]}] SKIP OPEN {side} {coin}: reserved for HL swing"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {side:<6} {coin:<7} reserved for HL swing"
            )
            return False
        if not self.config.one_position_per_asset:
            return True
        if pair not in self._our_mirrors:
            return True
        owner = self._mirror_owner(pair) or self._our_mirrors[pair].get("trader", "?")
        our_side = self._our_mirrors[pair].get("side", "?")
        same = (owner == trader_addr) if owner else False
        if same:
            reason = f"already in {our_side} from same trader (no size add)"
        else:
            owner_short = (owner[:10] + "…") if isinstance(owner, str) and owner.startswith("0x") else str(owner)
            reason = f"asset locked by {owner_short} ({our_side}) — single account, no interference"
        self.logger().info(
            f"[{trader_addr[:10]}] LOCK skip OPEN {side} {coin}: {reason}"
        )
        trade_log(
            f"{'':>22} {'':>14} {'LOCK':<8} {side:<6} {coin:<7} {reason}"
        )
        return False

    def _can_close_or_flip_pair(self, trader_addr: str, pair: str, action: str) -> bool:
        """
        Only the owner of the mirror may close or flip it.
        Other leaders on the same asset must not move our position.
        Manual-close-only (full-scale / add-ETH) never auto-close from leaders.
        """
        if pair not in self._our_mirrors:
            return False  # nothing to close/flip
        coin = self._our_mirrors[pair].get("coin", pair)
        if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
            self.logger().info(
                f"[{trader_addr[:10]}] LOCK skip {action} {coin}: reserved for HL swing"
            )
            trade_log(
                f"{'':>22} {'':>14} {'LOCK':<8} {'':<6} {str(coin):<7} "
                f"skip {action} — reserved for HL swing"
            )
            return False
        if self._is_manual_close_only(pair=pair):
            coin = self._our_mirrors[pair].get("coin", pair)
            our_side = self._our_mirrors[pair].get("side", "?")
            self.logger().info(
                f"[{trader_addr[:10]}] LOCK skip {action} {coin}: manual_close_only "
                f"(dashboard close only)"
            )
            trade_log(
                f"{'':>22} {'':>14} {'LOCK':<8} {our_side:<6} {coin:<7} "
                f"skip {action} — manual close only"
            )
            return False
        if not self.config.one_position_per_asset:
            return self._our_mirrors[pair].get("trader") == trader_addr
        owner = self._mirror_owner(pair)
        # Unowned restored/orphan mirrors: only orphan reconcile should close them
        if owner is None:
            if self._our_mirrors[pair].get("trader") in ("restored", "reconciled", "orphan", ""):
                self.logger().info(
                    f"[{trader_addr[:10]}] LOCK skip {action} {pair}: mirror not owned by a leader yet"
                )
                return False
            # legacy empty owner — allow if tags match loosely
            return self._our_mirrors[pair].get("trader") == trader_addr
        if owner != trader_addr:
            coin = self._our_mirrors[pair].get("coin", pair)
            our_side = self._our_mirrors[pair].get("side", "?")
            self.logger().info(
                f"[{trader_addr[:10]}] LOCK skip {action} {coin}: owned by {owner[:10]}… "
                f"({our_side}) — other trader must not change our position"
            )
            trade_log(
                f"{'':>22} {'':>14} {'LOCK':<8} {our_side:<6} {coin:<7} "
                f"skip {action} — owned by {owner[:10]}…"
            )
            return False
        return True

    # ── Order helpers ─────────────────────────────────────────────────────────

    def _live_account(self) -> dict:
        """Fresh HL clearinghouse for OUR copy wallet. Cached ~5s. Never trades."""
        now = time.time()
        cached = getattr(self, "_live_acct_cache", None)
        if cached and now - float(cached.get("ts") or 0) < 5:
            return cached
        out = {"ts": now, "equity": Decimal("0"), "free": Decimal("0"), "margin": Decimal("0")}
        try:
            addr = self.connectors[self.config.connector_name].hyperliquid_perpetual_address
            data = self._hl_post({"type": "clearinghouseState", "user": addr}) or {}
            ms = data.get("marginSummary") or data.get("crossMarginSummary") or {}
            eq = Decimal(str(ms.get("accountValue") or 0))
            mu = Decimal(str(ms.get("totalMarginUsed") or 0))
            wd = Decimal(str(data.get("withdrawable") or 0))
            est = eq - mu
            if est < 0:
                est = Decimal("0")
            free = wd if wd > est else est
            if free < 0:
                free = Decimal("0")
            out.update({"equity": eq if eq > 0 else Decimal("0"), "free": free, "margin": mu})
        except Exception:
            pass
        self._live_acct_cache = out
        return out

    def _get_free_balance(self) -> Decimal:
        """
        Free margin available for new opens / adds.

        Hyperliquid's `withdrawable` can stay 0 with open cross-margin positions
        even after depositing (equity rises, but withdrawable does not). For
        *trading* we need free-to-open ≈ max(withdrawable, equity − margin used).
        """
        try:
            connector = self.connectors[self.config.connector_name]
            withdrawable = connector.get_available_balance("USD")
            free = Decimal(str(withdrawable)) if withdrawable is not None else Decimal("0")
            if free < 0:
                free = Decimal("0")

            # Total equity (accountValue)
            try:
                eq_raw = connector.get_balance("USD")
                equity = Decimal(str(eq_raw)) if eq_raw is not None else Decimal("0")
            except Exception:
                equity = Decimal("0")

            # Margin locked in our known mirrors (+ connector positions if any)
            margin_used = Decimal("0")
            for m in (getattr(self, "_our_mirrors", {}) or {}).values():
                try:
                    margin_used += self._mirror_margin_usd(m)
                except Exception:
                    pass
            try:
                # Prefer live connector account positions if exposed
                positions = getattr(connector, "account_positions", None) or {}
                if isinstance(positions, dict) and positions:
                    pos_margin = Decimal("0")
                    for _k, pos in positions.items():
                        mu = getattr(pos, "margin", None) or getattr(pos, "margin_used", None)
                        if mu is not None and Decimal(str(mu)) > 0:
                            pos_margin += Decimal(str(mu))
                            continue
                        # estimate from amount * entry / leverage
                        try:
                            amt = abs(Decimal(str(getattr(pos, "amount", 0) or 0)))
                            entry = Decimal(str(getattr(pos, "entry_price", 0) or 0))
                            lev = Decimal(str(getattr(pos, "leverage", 0) or 0))
                            if amt > 0 and entry > 0 and lev > 0:
                                pos_margin += (amt * entry) / lev
                        except Exception:
                            pass
                    if pos_margin > margin_used:
                        margin_used = pos_margin
            except Exception:
                pass

            if equity > 0:
                free_est = equity - margin_used
                if free_est < 0:
                    free_est = Decimal("0")
                # Use the larger of withdrawable and equity−margin (tradable free)
                if free_est > free:
                    free = free_est

            live = self._live_account()
            if live.get("free") and live["free"] > free:
                free = live["free"]
            if live.get("equity") and live["equity"] > 0:
                est = live["equity"] - live.get("margin", Decimal("0"))
                if est > free:
                    free = est
            try:
                from risk_park import deduct
                return deduct(COPY_BOT_ID, free)
            except Exception:
                return free
        except Exception:
            live = self._live_account()
            free = live.get("free") or Decimal("0")
            try:
                from risk_park import deduct
                return deduct(COPY_BOT_ID, free)
            except Exception:
                return free

    def _get_total_balance(self) -> Decimal:
        """
        Full account equity (HL accountValue): free + margin used in positions +/- uPnL.
        This matches what Hyperliquid UI usually shows as total / available equity.
        """
        live = self._live_account()
        if live.get("equity") and live["equity"] > 0:
            return live["equity"]
        try:
            connector = self.connectors[self.config.connector_name]
            # ConnectorBase: get_balance = total, get_available_balance = free
            bal = connector.get_balance("USD")
            if bal is not None and Decimal(str(bal)) > 0:
                return Decimal(str(bal))
        except Exception:
            pass
        # Do not recurse into free (which may estimate from equity); hard 0
        try:
            connector = self.connectors[self.config.connector_name]
            w = connector.get_available_balance("USD")
            return Decimal(str(w)) if w else Decimal("0")
        except Exception:
            return Decimal("0")

    def _mirror_upnl_usd(self, mirror: dict, mid: Optional[Decimal] = None) -> Decimal:
        """Unrealized PnL in USD for a local mirror (mark-to-mid)."""
        try:
            amount = Decimal(str(mirror.get("amount") or 0))
            entry = Decimal(str(mirror.get("entry_price") or 0))
            if amount <= 0 or entry <= 0:
                return Decimal("0")
            pair = mirror.get("pair") or (
                f"{mirror.get('coin')}-USD" if mirror.get("coin") else None
            )
            if mid is None or mid <= 0:
                mid = self._get_mid_price(pair) if pair else None
            if mid is None or mid <= 0:
                mid = entry
            mid = Decimal(str(mid))
            side = (mirror.get("side") or "LONG").upper()
            if side == "LONG":
                return (mid - entry) * amount
            return (entry - mid) * amount
        except Exception:
            return Decimal("0")

    def _mirror_roe_pct(self, mirror: dict, chain: Optional[dict] = None) -> Optional[float]:
        """Mark-to-mid ROE % (price move × leverage). Falls back to HL chain mark."""
        try:
            entry = float(mirror.get("entry_price") or mirror.get("entry_px") or 0)
            if chain:
                try:
                    hx = float(chain.get("entryPx") or 0)
                    if hx > 0:
                        entry = hx
                except (TypeError, ValueError):
                    pass
            if entry <= 0:
                return None
            pair = mirror.get("pair") or (
                f"{mirror.get('coin')}-USD" if mirror.get("coin") else None
            )
            mid = self._get_mid_price(pair) if pair else None
            try:
                mid_f = float(mid) if mid is not None else 0.0
            except (TypeError, ValueError):
                mid_f = 0.0
            if mid_f <= 0 and chain:
                try:
                    szi = float(chain.get("szi") or 0)
                    upnl = float(chain.get("unrealizedPnl") or 0)
                    if szi != 0:
                        mid_f = entry + (upnl / szi)
                except (TypeError, ValueError, ZeroDivisionError):
                    mid_f = 0.0
            if mid_f <= 0:
                return None
            lev = float(mirror.get("leverage") or 1) or 1.0
            if chain:
                try:
                    lv = float((chain.get("leverage") or {}).get("value") or 0)
                    if lv > 0:
                        lev = lv
                except (TypeError, ValueError, AttributeError):
                    pass
            side = (mirror.get("side") or "LONG").upper()
            if side == "SHORT":
                return (entry - mid_f) / entry * lev * 100.0
            return (mid_f - entry) / entry * lev * 100.0
        except Exception:
            return None

    def _total_unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for m in self._our_mirrors.values():
            total += self._mirror_upnl_usd(m)
        return total

    def _sizing_equity(self) -> Decimal:
        """
        Equity used for pot / allocation math.

        If size_exclude_unrealized_profit: strip *positive* uPnL so paper profits
        do not inflate pot sizes and fund larger new positions. Negative uPnL
        already reduces HL equity and is left as-is.
        """
        equity = self._get_total_balance()
        free = self._get_free_balance()
        if equity <= 0:
            return free if free > 0 else Decimal("0")
        if not getattr(self.config, "size_exclude_unrealized_profit", True):
            return equity
        if not getattr(self.config, "risk_management_enabled", True):
            return equity
        upnl = self._total_unrealized_pnl()
        if upnl > 0:
            conservative = equity - upnl
            if free > 0:
                conservative = max(conservative, free)
            return conservative if conservative > 0 else free
        return equity

    def _risk_max_position_loss_usd(self, equity: Optional[Decimal] = None) -> Decimal:
        """Max allowed unrealized loss per position in USD (default 1/3 equity)."""
        if equity is None:
            equity = self._get_total_balance()
        pct = Decimal(str(_ctrl("max_position_loss_pct", getattr(self.config, "max_position_loss_pct", 0.333) or 0.333)))
        if pct <= 0 or equity <= 0:
            return Decimal("0")
        return equity * pct

    def _risk_allows_new_open(self, allocation: Decimal):
        """Cover-first gate before any new open. Returns (ok, reason)."""
        if not getattr(self.config, "risk_management_enabled", True):
            return True, ""
        equity = self._get_total_balance()
        free = self._get_free_balance()
        if equity <= 0 and free <= 0:
            return False, "no equity/free"
        min_free_pct = Decimal(str(_ctrl("min_free_margin_pct", getattr(self.config, "min_free_margin_pct", 0.15) or 0)))
        if equity > 0 and min_free_pct > 0:
            need = equity * min_free_pct
            if free < need:
                return (
                    False,
                    f"cover first: free ${float(free):.2f} < "
                    f"{float(min_free_pct)*100:.0f}% equity (${float(need):.2f})",
                )
        max_m_pct = Decimal(str(getattr(self.config, "max_margin_per_position_pct", 0.333) or 0))
        if equity > 0 and max_m_pct > 0 and allocation > equity * max_m_pct + Decimal("0.01"):
            return (
                False,
                f"margin ${float(allocation):.2f} > "
                f"{float(max_m_pct)*100:.0f}% equity cap",
            )
        return True, ""

    def _load_risk_overrides(self, force: bool = False):
        """
        Load conf/hl_risk_overrides.json — coins with risk auto-stop disabled.
        Applied to open mirrors each tick.
        """
        path = RISK_OVERRIDES_FILE
        if not os.path.exists(path):
            if not hasattr(self, "_risk_disabled_coins"):
                self._risk_disabled_coins = set()
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if (
            not force
            and mtime <= getattr(self, "_risk_overrides_mtime", 0.0)
        ):
            # Still re-apply flags onto mirrors (new restores)
            self._apply_risk_overrides_to_mirrors()
            return
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            data = json.loads(raw) if raw else {}
        except Exception as e:
            self.logger().warning(f"[risk-override] bad file: {e}")
            return
        self._risk_overrides_mtime = mtime
        disabled = set()
        for c in (data.get("disabled_risk_coins") or data.get("coins") or []):
            try:
                disabled.add(str(c).strip().upper())
            except Exception:
                pass
        prev = getattr(self, "_risk_disabled_coins", set()) or set()
        self._risk_disabled_coins = disabled
        if disabled != prev:
            if disabled:
                self.logger().info(
                    f"[risk-override] auto-stop OFF for: {', '.join(sorted(disabled))}"
                )
            else:
                self.logger().info("[risk-override] auto-stop ON for all positions")
        self._apply_risk_overrides_to_mirrors()

    def _apply_risk_overrides_to_mirrors(self):
        disabled = getattr(self, "_risk_disabled_coins", set()) or set()
        for pair, m in list(self._our_mirrors.items()):
            coin = str(m.get("coin") or pair.replace("-USD", "")).upper()
            m["risk_auto_close_disabled"] = coin in disabled

    def _process_force_risk_toggles(self):
        """
        Dashboard toggle: enable/disable risk auto-stop for a coin.
        File: force_risk_toggle_hl_copy.json
          {"coin":"ETH","enabled":false}  # disable auto-stop
          {"coin":"ETH","enabled":true}   # re-enable
        """
        cmd = self._sql_cmd(("risk_toggle",))
        if not cmd:
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        self._sql_done(cmd)
        coin = str(data.get("coin") or "").strip().upper()
        if not coin or ":" in coin:
            return
        if "enabled" in data:
            want_on = bool(data["enabled"])
        elif "disable" in data:
            want_on = not bool(data["disable"])
        else:
            want_on = False  # default: disable auto-stop
        # Update persistent overrides
        disabled = set(getattr(self, "_risk_disabled_coins", set()) or set())
        if want_on:
            disabled.discard(coin)
            action = "ON"
        else:
            disabled.add(coin)
            action = "OFF"
        self._risk_disabled_coins = disabled
        try:
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": "dashboard",
                "disabled_risk_coins": sorted(disabled),
            }
            with open(RISK_OVERRIDES_FILE, "w") as f:
                json.dump(payload, f, indent=2)
            try:
                self._risk_overrides_mtime = os.path.getmtime(RISK_OVERRIDES_FILE)
            except OSError:
                pass
        except Exception as e:
            self.logger().error(f"[force-risk-toggle] persist failed: {e}")
        self._apply_risk_overrides_to_mirrors()
        self.logger().info(f"[risk] auto-stop {action} for {coin}")
        trade_log(
            f"{YELLOW}{BOLD}"
            f"{_now():<22} {'RISK':<14} {'AUTO':<8} {'':6} {coin:<7} "
            f"auto-stop {action}"
            f"{RESET}"
        )
        try:
            self._write_status_file()
        except Exception:
            pass

    def _parse_sl_block_map(self, coins) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        if not isinstance(coins, dict):
            return out
        for k, v in coins.items():
            coin = str(k or "").upper()
            if not coin:
                continue
            if isinstance(v, dict):
                side = str(v.get("side") or "").upper()
                if side in ("LONG", "SHORT"):
                    out[coin] = v
            else:
                side = str(v or "").upper()
                if side in ("LONG", "SHORT"):
                    out[coin] = {"side": side}
        return out

    def _load_sl_block(self):
        self._sl_block = {}
        try:
            with open(SL_BLOCK_FILE, "r") as f:
                data = json.load(f) or {}
            coins = data.get("coins") if isinstance(data, dict) else data
            self._sl_block = self._parse_sl_block_map(coins)
        except Exception:
            self._sl_block = {}
        try:
            from matrix_store import ensure, kv_get
            ensure()
            sql = kv_get(COPY_BOT_ID, "sl_block", None)
            if isinstance(sql, dict):
                parsed = self._parse_sl_block_map(sql)
                # SQL is source of truth when the key exists (including empty = cleared).
                self._sl_block = parsed
        except Exception:
            pass

    def _save_sl_block(self):
        payload = {
            "updated": _now(),
            "sl_roe_pct": float(_live_sl_roe()),
            "coins": self._sl_block,
        }
        try:
            with open(SL_BLOCK_FILE, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            self.logger().warning(f"[risk] sl-block save failed: {e}")
        try:
            self._sql_kv("sl_block", dict(self._sl_block or {}), "copy-sl")
            self._sql_kv("sl_roe_pct", float(_live_sl_roe()), "copy-sl")
            self._sql_kv("sl_always", True, "copy-sl")
            self._sql_kv("sl_no_recopy_until_leader_close", True, "copy-sl")
        except Exception as e:
            self.logger().warning(f"[risk] sl-block sql failed: {e}")

    def _sl_block_add(self, coin: str, side: str, why: str = "SL"):
        coin = str(coin or "").upper()
        side = str(side or "").upper()
        if not coin or side not in ("LONG", "SHORT"):
            return
        self._sl_block[coin] = {
            "side": side,
            "why": str(why)[:80],
            "ts": _now(),
            "sl_roe_pct": float(_live_sl_roe()),
        }
        self._save_sl_block()
        self.logger().warning(
            f"[risk] SL-block {side} {coin} — will not re-copy until leader closes"
        )
        trade_log(
            f"{YELLOW}{BOLD}{_now():<22} {'RISK':<14} {'BLOCK':<8} {side:<6} {coin:<7} "
            f"no re-copy until leader closes{RESET}"
        )

    def _sl_block_release_flat(self, coin: str, leader_side: Optional[str]):
        coin = str(coin or "").upper()
        rec = self._sl_block.get(coin)
        if not rec:
            return
        blocked = str(rec.get("side") or "").upper()
        ls = str(leader_side or "").upper()
        if ls == blocked:
            return
        self._sl_block.pop(coin, None)
        self._save_sl_block()
        self.logger().info(f"[risk] SL-block cleared {coin} — leader no longer {blocked}")

    def _leader_clip_roe(self, pos: dict) -> Optional[float]:
        """Leader clip ROE at our live mid. Used to refuse copying an already-blown trade."""
        try:
            entry = float(pos.get("entry_px") or pos.get("entry_price") or 0)
            if entry <= 0:
                return None
            pair = pos.get("pair")
            mid = self._get_mid_price(pair) if pair else None
            try:
                mid_f = float(mid) if mid is not None else 0.0
            except (TypeError, ValueError):
                mid_f = 0.0
            if mid_f <= 0:
                return None
            lev = float(pos.get("leverage") or 1) or 1.0
            side = str(pos.get("side") or "LONG").upper()
            if side == "SHORT":
                return (entry - mid_f) / entry * lev * 100.0
            return (mid_f - entry) / entry * lev * 100.0
        except Exception:
            return None

    def _sl_open_blocked(self, coin: str, side: str) -> bool:
        coin = str(coin or "").upper()
        side = str(side or "").upper()
        rec = self._sl_block.get(coin)
        if not rec:
            return False
        return str(rec.get("side") or "").upper() == side

    def _leader_holds_side(self, coin: str, side: str) -> bool:
        coin = str(coin or "").upper()
        side = str(side or "").upper()
        for _addr, posmap in (getattr(self, "_trader_positions", None) or {}).items():
            if not isinstance(posmap, dict):
                continue
            p = posmap.get(coin)
            if isinstance(p, dict) and str(p.get("side") or "").upper() == side:
                return True
        return False

    def _seed_sl_blocks_from_gap(self):
        """Coins we listed as mirrors that are gone on-chain: do not re-copy while leader still in."""
        try:
            with open(STATUS_FILE, "r") as f:
                last = json.load(f) or {}
        except Exception:
            return
        our = self._fetch_our_onchain_positions() or {}
        if not our:
            return
        held = {}
        for c, p in our.items():
            try:
                szi = float(p.get("szi") or 0)
            except (TypeError, ValueError):
                continue
            if szi == 0:
                continue
            held[str(c).upper()] = "LONG" if szi > 0 else "SHORT"
        for m in last.get("mirrors") or []:
            if not isinstance(m, dict):
                continue
            coin = str(m.get("coin") or "").upper()
            side = str(m.get("side") or "").upper()
            if not coin or side not in ("LONG", "SHORT"):
                continue
            if held.get(coin) == side:
                continue
            self._sl_block_add(coin, side, "flattened — wait for leader close")

    def _risk_manage_positions(self):
        """
        Close ANY mirror whose ROE hits Home SL (bot_sl.json, default 20%),
        every trader, every coin. Then block re-copy until the leader closes.
        USD equity stop still respects per-coin dashboard disable.
        """
        if not self._our_mirrors:
            return

        pending_pairs = {
            ctx.get("pair") for ctx in (getattr(self, "_pending_closes", {}) or {}).values()
        }
        disabled = getattr(self, "_risk_disabled_coins", set()) or set()
        sl = float(_live_sl_roe())
        chain = {}
        try:
            chain = self._fetch_our_onchain_positions() or {}
        except Exception:
            chain = {}

        for pair, mirror in list(self._our_mirrors.items()):
            if pair in pending_pairs:
                continue
            coin = str(mirror.get("coin") or pair.replace("-USD", "")).upper()
            if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
                continue
            roe = self._mirror_roe_pct(mirror, chain.get(coin) or chain.get(coin.lower()))
            if roe is None or roe > -sl:
                continue
            side = mirror.get("side") or "?"
            self.logger().warning(
                f"[risk] CLOSE {side} {coin} — ROE {roe:.2f}% <= -{sl:g}%"
            )
            trade_log(
                f"{YELLOW}{BOLD}"
                f"{_now():<22} {'RISK':<14} {'SL':<8} {side:<6} {coin:<7} "
                f"ROE={roe:.2f}%  stop=-{sl:g}%"
                f"{RESET}"
            )
            try:
                mirror["risk_stop"] = True
                mirror["manual_close_only"] = False
                mirror["full_allin"] = False
                mirror["full_btc"] = False
                mirror["full_eth"] = False
                mirror["add_eth"] = False
            except Exception:
                pass
            self._sl_block_add(coin, str(side).upper(), f"SL ROE {roe:.2f}% <= -{sl:g}%")
            try:
                self._close_mirror(pair, reason=f"SL ROE {roe:.2f}% <= -{sl:g}%")
            except Exception as e:
                self.logger().error(f"[risk] SL close failed for {pair}: {e}")
            pending_pairs.add(pair)

        if not getattr(self.config, "risk_management_enabled", True):
            return
        equity = self._get_total_balance()
        if equity <= 0:
            return
        max_loss = self._risk_max_position_loss_usd(equity)
        if max_loss <= 0:
            return

        for pair, mirror in list(self._our_mirrors.items()):
            if pair in pending_pairs:
                continue
            coin = str(mirror.get("coin") or pair.replace("-USD", "")).upper()
            if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
                continue
            if (
                mirror.get("risk_auto_close_disabled")
                or coin in disabled
            ):
                continue
            upnl = self._mirror_upnl_usd(mirror)
            if upnl > -max_loss:
                continue
            side = mirror.get("side") or "?"
            self.logger().warning(
                f"[risk] CLOSE {side} {coin} — uPnL ${float(upnl):.2f} <= "
                f"-{float(max_loss):.2f} ({float(self.config.max_position_loss_pct)*100:.0f}% "
                f"of equity ${float(equity):.2f})"
            )
            trade_log(
                f"{YELLOW}{BOLD}"
                f"{_now():<22} {'RISK':<14} {'STOP':<8} {side:<6} {coin:<7} "
                f"uPnL=${float(upnl):.2f}  max=-${float(max_loss):.2f}  "
                f"({float(self.config.max_position_loss_pct)*100:.0f}% equity)"
                f"{RESET}"
            )
            try:
                mirror["risk_stop"] = True
                mirror["manual_close_only"] = False
                mirror["full_allin"] = False
                mirror["full_btc"] = False
                mirror["full_eth"] = False
                mirror["add_eth"] = False
            except Exception:
                pass
            self._sl_block_add(
                coin, str(side).upper(),
                f"USD stop ${float(upnl):.2f}",
            )
            try:
                self._close_mirror(
                    pair,
                    reason=(
                        f"risk stop: loss ${float(upnl):.2f} >= "
                        f"{float(self.config.max_position_loss_pct)*100:.0f}% equity"
                    ),
                )
            except Exception as e:
                self.logger().error(f"[risk] close failed for {pair}: {e}")

    def _n_traders(self) -> int:
        """Configured leaders that are not muted. Muted pots go to the rest."""
        n_cfg = max(1, len(self.config.trader_addresses or []))
        unf = getattr(self, "_unfollowed_traders", None) or set()
        n_active = n_cfg - sum(1 for i in range(1, n_cfg + 1) if i in unf)
        return max(1, n_active)

    def _max_positions_for_trader(self, trader_addr: str) -> int:
        """Per-trader open-mirror cap. Dashboard 1/2/3 splits that trader's pot 1/N."""
        try:
            num = self._trader_num_for_address(trader_addr)
            slots = getattr(self, "_trader_follow_slots", None) or {}
            if num is not None:
                if num in slots:
                    return max(1, min(3, int(slots[num])))
                if str(num) in slots:
                    return max(1, min(3, int(slots[str(num)])))
        except Exception:
            pass
        if trader_addr in (self.config.trader_max_positions or {}):
            return max(1, min(3, int(self.config.trader_max_positions[trader_addr])))
        return max(1, min(8, int(_ctrl(
            "max_positions_per_trader",
            self.config.max_positions_per_trader or self.config.min_open_positions or 3,
        ))))

    def _allocation_for_trader(self, trader_addr: str) -> Decimal:
        """
        Equal pots for every configured trader, then equal position slots:

          sizing_equity = equity (- positive uPnL if risk sizing on)
          trader_pot    = sizing_equity / N_traders
          allocation    = trader_pot / max_positions_per_trader x allocation_pct

        Capped by free, and by max_margin_per_position_pct of equity.
        Muted leaders get $0 new size; their pot is split among the rest.
        """
        if not self._is_trader_followed(address=trader_addr):
            return Decimal("0")
        equity = self._get_total_balance()
        free = self._get_free_balance()
        funds = self._sizing_equity()
        if funds <= 0:
            funds = free if free > 0 else Decimal("0")
        funds = funds * Decimal(str(1.0 - self.config.commodity_allocation_pct))

        n_traders = self._n_traders()
        max_pos = self._max_positions_for_trader(trader_addr)
        trader_pot = funds / Decimal(str(n_traders))
        used = self._margin_used_by_trader().get(trader_addr, Decimal("0"))
        remain_pot = trader_pot - used
        if remain_pot < 0:
            remain_pot = Decimal("0")
        active = sum(
            1 for m in (self._our_mirrors or {}).values()
            if m.get("trader") == trader_addr
        )
        remain_slots = max(1, int(max_pos) - int(active))
        slot = remain_pot / Decimal(str(remain_slots))
        allocation = slot * Decimal(str(self.config.allocation_pct))

        # One configured slot makes this the whole pot. Still clamp to the
        # margin cap. Leaving it uncapped makes the risk gate refuse the open.
        if getattr(self.config, "risk_management_enabled", True) and equity > 0:
            max_m = equity * Decimal(
                str(getattr(self.config, "max_margin_per_position_pct", 0.333) or 0.333)
            )
            if max_m > 0 and allocation > max_m:
                allocation = max_m

        if free > 0 and allocation > free:
            allocation = free
        elif free <= 0:
            allocation = Decimal("0")
        return allocation

    def _get_mid_price(self, pair: str) -> Optional[Decimal]:
        try:
            connector = self.connectors[self.config.connector_name]
            price = connector.get_mid_price(pair)
            return Decimal(str(price)) if price else None
        except Exception:
            return None

    def _open_mirror(self, trader_addr: str, coin: str, pos: dict, force: bool = False) -> bool:
        """
        Open a mirror position. Returns True if the open was attempted/registered,
        False if deferred (caller must not mark the coin as already handled so the
        next poll re-detects it as NEW).

        force=True: dashboard manual follow — bypass trader position cap (user override);
        asset lock (one position per coin) still applies. Live mid price is used.
        """
        pair = pos.get("pair") or COIN_TO_PAIR.get(coin) or f"{coin}-USD"
        pos = {**pos, "pair": pair}
        side = str(pos.get("side") or "").upper()
        coin_u = str(coin or "").upper()

        if self._sl_open_blocked(coin_u, side):
            if not self._leader_holds_side(coin_u, side):
                self._sl_block_release_flat(coin_u, None)
            else:
                self.logger().info(
                    f"[{trader_addr[:10]}] OPEN {side} {coin_u} blocked — SL lock "
                    f"(wait for leader close)"
                )
                trade_log(
                    f"{'':>22} {'':>14} {'SKIP':<8} {side:<6} {coin_u:<7} "
                    f"SL lock until leader closes"
                )
                return True

        sl = float(_live_sl_roe())
        lroe = self._leader_clip_roe(pos)
        if lroe is not None and lroe <= -sl:
            self._sl_block_add(
                coin_u, side, f"leader already {lroe:.1f}% <= -{sl:g}%"
            )
            self.logger().info(
                f"[{trader_addr[:10]}] OPEN {side} {coin_u} blocked — leader ROE "
                f"{lroe:.1f}% already through SL -{sl:g}%"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {side:<6} {coin_u:<7} "
                f"leader ROE {lroe:.1f}% <= -{sl:g}%"
            )
            return True

        if self._is_reserved_coin(coin) or self._is_reserved_coin(pair):
            self.logger().info(
                f"[{trader_addr[:10]}] OPEN {pos.get('side')} {coin} blocked — reserved for HL swing"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {pos.get('side',''):<6} {coin:<7} reserved for HL swing"
            )
            return True

        # During full all-in flatten/redeploy, do not open any copy mirrors
        if getattr(self, "_full_allin_phase", None) or getattr(self, "_full_btc_phase", None):
            tgt = getattr(self, "_full_allin_coin", None) or "BTC"
            self.logger().info(
                f"[{trader_addr[:10]}] OPEN {pos.get('side')} {coin} blocked — full-{tgt} in progress"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {pos.get('side',''):<6} {coin:<7} full-{tgt} mode"
            )
            return True

        # Longs-only mode: never open new shorts (including deferred / force-follow)
        if pos.get("side") == "SHORT" and not self._allow_shorts():
            self.logger().info(
                f"[{trader_addr[:10]}] OPEN SHORT {coin} blocked — longs only"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} SHORT  {coin:<7} longs only"
            )
            return True  # handled — drop deferred retries for this short

        # Safety net: never open if another (or same) leader already locks this asset
        if not self._can_open_pair(trader_addr, pair, coin, pos["side"]):
            return True  # handled — do not keep retrying a locked asset

        # Cap BEFORE subscribing the book — otherwise a 12-coin dump adds 12
        # markets, connector.ready waits on all of them, and we 429-stall.
        max_pos = self._max_positions_for_trader(trader_addr)
        active = sum(1 for m in self._our_mirrors.values() if m.get("trader") == trader_addr)
        if active >= max_pos and not force:
            self.logger().info(
                f"[{trader_addr[:10]}] Cap {active}/{max_pos} — not opening {coin}"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {pos.get('side',''):<6} {coin:<7} "
                f"trader cap {max_pos} reached"
            )
            return True
        if force and active >= max_pos:
            self.logger().info(
                f"[{trader_addr[:10]}] FORCE follow {coin} — over trader cap "
                f"({active}/{max_pos})"
            )

        # Defer new opens while the connector is re-initialising so we do not
        # place orders into a half-ready market (and so a temporary not-ready
        # state cannot silently swallow open intent forever without logging).
        if not self._connector_is_ready():
            # Throttle logs — deferred opens retry every poll (~2s)
            now = time.time()
            last = getattr(self, "_last_defer_log", 0.0)
            if now - last >= 30:
                self._last_defer_log = now
                self.logger().warning(
                    f"[stall] connector not ready — deferring OPEN {pos['side']} {coin} "
                    f"(will retry on next poll once ready)"
                )
                trade_log(
                    f"{YELLOW}{BOLD}"
                    f"{_now():<22} {'DEFER':<14} {'OPEN':<8} {pos['side']:<6} {coin:<7} "
                    f"connector not ready — retry next poll"
                    f"{RESET}"
                )
            return False

        # Dynamic market subscribe (roadmap P2) — wait for order book before open
        if not self._ensure_pair_ready(pair):
            if pair in self._pair_ensure_failed:
                self.logger().warning(
                    f"[markets] Cannot trade {pair} — subscribe failed; skipping {coin}"
                )
                if not force:
                    self._skipped_positions[trader_addr].add(coin)
                trade_log(
                    f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} "
                    f"market subscribe failed"
                )
                return True  # handled (skip permanently for this open)
            now = time.time()
            if now - getattr(self, "_last_defer_log", 0.0) >= 30:
                self._last_defer_log = now
                self.logger().info(
                    f"[markets] Deferring OPEN {pos['side']} {coin} — waiting for {pair} book"
                )
            return False

        leverage = self._max_leverage_for(coin)
        self.logger().info(f"Using HL max leverage for {coin}: {leverage}x (config cap: {self.config.max_leverage}x)")

        # Set leverage first
        try:
            self.connectors[self.config.connector_name].set_leverage(pair, leverage)
        except Exception as e:
            self.logger().warning(f"set_leverage failed for {pair}: {e}")

        # Per-trader pot: equity/N_traders, then 1/max_positions of that pot per open
        equity = self._get_total_balance()
        free = self._get_free_balance()
        n_traders = self._n_traders()
        sizing = self._sizing_equity()
        trader_pot = (sizing if sizing > 0 else free) * Decimal(
            str(1.0 - self.config.commodity_allocation_pct)
        ) / Decimal(str(n_traders))
        allocation = self._allocation_for_trader(trader_addr)
        if allocation <= 0:
            self.logger().warning(f"No balance available — skipping {coin}")
            self._skipped_positions[trader_addr].add(coin)
            trade_log(f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} no balance — will not re-enter late")
            return True
        # Risk: cover open positions first; block if free margin too thin
        ok_risk, risk_why = self._risk_allows_new_open(allocation)
        if not ok_risk:
            self.logger().info(
                f"[{trader_addr[:10]}] OPEN {pos['side']} {coin} blocked by risk — {risk_why}"
            )
            trade_log(
                f"{'':>22} {'':>14} {'SKIP':<8} {pos['side']:<6} {coin:<7} risk: {risk_why}"
            )
            # Do not permanent-skip: free may recover when other positions close
            return True
        self.logger().info(
            f"Funds: equity=${float(equity):.2f} free=${float(free):.2f} | "
            f"sizing=${float(self._sizing_equity()):.2f} | "
            f"traders={n_traders} pot/trader=${float(trader_pot):.2f} | "
            f"pos_slots={max_pos} active={active} | "
            f"margin this open=${float(allocation):.2f}"
        )

        # Use live mid-price if available, else fall back to trader's entry price
        mid = self._get_mid_price(pair) or Decimal(str(pos["entry_px"]))
        if mid <= 0:
            self.logger().warning(f"Invalid price for {pair}: {mid} — will retry next poll")
            return False

        notional = allocation * leverage
        # Quantize to 4 decimal places (safe for BTC); connectors clamp further if needed
        amount = (notional / mid).quantize(Decimal("0.00001"))

        if str(coin).upper() == "ZEC" and str(pos.get("side") or "").upper() == "SHORT":
            cap = self._max_zec_short_usd()
            if cap > 0:
                ntl = float(notional)
                if ntl > cap:
                    amount = (Decimal(str(cap)) / mid).quantize(Decimal("0.00001"))
                    notional = amount * mid
                    self.logger().info(
                        f"[{trader_addr[:10]}] ZEC SHORT clipped to max ${cap:.2f} "
                        f"notional (slot was ${ntl:.2f})"
                    )
                    trade_log(
                        f"{'':>22} {'':>14} {'CAP':<8} SHORT  ZEC     "
                        f"max ${cap:.0f} ntl (was ${ntl:.0f})"
                    )
                if amount <= 0:
                    self.logger().info(
                        f"[{trader_addr[:10]}] OPEN SHORT ZEC blocked — max short ZEC ${cap:.2f} too small"
                    )
                    trade_log(
                        f"{'':>22} {'':>14} {'SKIP':<8} SHORT  ZEC     max short too small"
                    )
                    return True

        if amount <= 0:
            self.logger().warning(f"Amount too small for {pair}: {amount}")
            self._skipped_positions[trader_addr].add(coin)
            return True

        self.logger().info(
            f"Opening {pos['side']} mirror: {pair}  amount={amount}  "
            f"price~${mid:,.6f}  margin=${allocation:.2f}  notional=${notional:.2f}"
        )

        # Register mirror FIRST so close detection always works even if order errors
        self._our_mirrors[pair] = {
            "trader": trader_addr,
            "coin": coin,
            "side": pos["side"],
            "amount": amount,
            "entry_price": mid,
            "leverage": leverage,
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
        _tn = self._trader_num_for_address(trader_addr)
        trade_log(
            f"{RED}{BOLD}"
            f"{_now():<22} {'WE COPY':<14} {'OPENED':<8} {pos['side']:<6} {coin:<7} "
            f"{float(amount):>10.5f}  ${float(mid):>14,.6f}  "
            f"margin=${float(allocation):.2f}  {leverage}x  notional=${float(notional):.2f}"
            f"{('  T#' + str(_tn)) if _tn else ''}"
            f"{RESET}"
        )
        return True

    def _hl_min_notional(self) -> Decimal:
        """Hyperliquid min order notional (USD) — dust below this cannot close."""
        return Decimal("10")

    def _hl_min_order_size(self, pair: str) -> Decimal:
        """
        HL min base order size for the pair (e.g. ZEC-USD is 0.01).
        Rescue adds must meet this; notional-only padding of 0.00476 fails.
        """
        try:
            conn = self.connectors.get(self.config.connector_name)
            rules = None
            if conn is not None:
                rules = getattr(conn, "trading_rules", None) or getattr(conn, "_trading_rules", None)
            tr = rules.get(pair) if rules else None
            if tr is not None:
                for attr in ("min_order_size", "min_base_amount_increment"):
                    v = getattr(tr, attr, None)
                    if v is not None and Decimal(str(v)) > 0:
                        return Decimal(str(v))
        except Exception:
            pass
        return Decimal("0.01")

    def _sync_mirror_amount_from_chain(self, pair: str, mirror: dict) -> dict:
        """Refresh size from on-chain so we close the real position."""
        coin = mirror.get("coin") or pair.replace("-USD", "")
        our = self._fetch_our_onchain_positions() or {}
        pos = our.get(coin)
        if pos is None:
            for c, p in our.items():
                if c.upper() == str(coin).upper():
                    pos = p
                    coin = c
                    break
        if pos is not None:
            szi = abs(float(pos.get("szi") or 0))
            if szi > 0:
                mirror["amount"] = Decimal(str(szi))
                mirror["coin"] = coin
                if pos.get("entryPx") is not None:
                    try:
                        mirror["entry_price"] = Decimal(str(pos.get("entryPx")))
                    except Exception:
                        pass
        return mirror

    def _dust_pad_close_amount(self, pair: str, amount: Decimal, mid: Decimal) -> Decimal:
        """
        Pad close size so order notional ≥ HL min ($10).

        Reduce-only CLOSE fills only the real position; we inflate the order size
        so Hummingbot/HL min-notional checks pass. Do NOT open a rescue add —
        tiny adds often fail min notional themselves (e.g. 0.01 ZEC ≈ $5).

        `mid` should be the *order* price (slippage-haircut for longs) — not
        mark — because the connector checks amount * submitted_price.
        """
        if mid <= 0 or amount <= 0:
            return amount
        min_n = self._hl_min_notional()
        min_sz = self._hl_min_order_size(pair)
        notional = amount * mid
        # Haircuted market closes of ~$10.4 dust fail at $9.82. Always pad
        # when the submitted notional is under 1.15× min.
        if notional >= min_n * Decimal("1.15"):
            return amount

        # Slightly above $10 so fees/slippage still clear validation
        target_notional = min_n * Decimal("1.20")
        padded = target_notional / mid
        if min_sz > 0:
            steps = (padded / min_sz).to_integral_value(rounding=ROUND_CEILING)
            if steps < 1:
                steps = Decimal("1")
            padded = steps * min_sz
        if padded < amount:
            padded = amount
        try:
            conn = self.connectors.get(self.config.connector_name)
            if conn is not None and hasattr(conn, "quantize_order_amount"):
                q = conn.quantize_order_amount(pair, padded)
                if q and q >= amount and q * mid >= min_n:
                    padded = q
                elif q and q > 0 and min_sz > 0:
                    # quantize may round down under min notional — step up
                    steps = (max(padded, amount) / min_sz).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                    padded = max(steps, Decimal("1")) * min_sz
        except Exception:
            pass

        coin = pair.replace("-USD", "")
        self.logger().warning(
            f"[dust-close] {pair} size={amount} notional=${float(notional):.2f} "
            f"< ${float(min_n):.0f} min — padding CLOSE order to {padded} "
            f"(~${float(padded * mid):.2f}, reduce-only fills real size)"
        )
        trade_log(
            f"{YELLOW}{BOLD}{_now():<22} {'DUST':<14} {'PAD':<8} {'':<6} {coin:<7} "
            f"{float(amount):.5f}→{float(padded):.5f} for HL min notional "
            f"${float(min_n):.0f}{RESET}"
        )
        return padded

    def _dust_rescue_before_close(self, pair: str, mirror: dict, mid: Decimal) -> Decimal:
        """Compat wrapper: pad close amount for dust positions (no open add)."""
        amount = Decimal(str(mirror.get("amount") or 0))
        padded = self._dust_pad_close_amount(pair, amount, mid)
        mirror["amount"] = padded
        return padded

    def _close_blocked(self, pair: str) -> bool:
        until = float(self._close_backoff_until.get(pair) or 0)
        if time.time() < until:
            return True
        if pair in self._close_in_flight:
            m = self._our_mirrors.get(pair) or {}
            started = float(m.get("_close_started") or 0)
            if started and (time.time() - started) < 60:
                return True
            self._close_in_flight.discard(pair)
        return False

    def _note_flip(self, coin: str) -> int:
        now = time.time()
        arr = [t for t in (self._flip_storm.get(coin) or []) if now - t < 600]
        arr.append(now)
        self._flip_storm[coin] = arr
        return len(arr)

    def _flip_storm_skip(self, coin: str) -> bool:
        """T2 DASH-style: 4+ flips in 10 min → do not open the other side."""
        now = time.time()
        n = len([t for t in (self._flip_storm.get(coin) or []) if now - t < 600])
        return n >= 4

    def _close_mirror(self, pair: str, reason: str = ""):
        if pair not in self._our_mirrors:
            return
        allin = bool(getattr(self, "_full_allin_phase", None))
        if not allin and self._close_blocked(pair):
            return
        mirror = self._our_mirrors.get(pair) or {}
        if mirror.get("_closing"):
            started = float(mirror.get("_close_started") or 0)
            hold = 12 if allin else 45
            if started and (time.time() - started) < hold:
                return

        # Flatten cannot close until the book + trading rules exist.
        if not self._pair_has_book(pair):
            self._ensure_pair_ready(pair)
            self.logger().info(f"[close] waiting for {pair} book")
            return

        # Keep local mirror until on-chain flat (ghost-clear logs the real CLOSED).
        # Popping first made FLIP→OPEN think we were flat while HL still held DASH.
        mirror = dict(mirror)
        self._our_mirrors[pair] = mirror
        # Prefer live on-chain size (dust leftovers often wrong in local state)
        mirror = self._sync_mirror_amount_from_chain(pair, mirror)
        amount = Decimal(str(mirror.get("amount") or 0))
        side = mirror["side"]
        coin = mirror["coin"]
        entry_price = mirror.get("entry_price", Decimal("0"))
        entry_time = mirror.get("entry_time", "?")

        mid = self._get_mid_price(pair) or entry_price or Decimal("0")
        if isinstance(mid, float):
            mid = Decimal(str(mid))
        if isinstance(entry_price, float):
            entry_price = Decimal(str(entry_price))

        # Connector min-notional uses the submitted price (mid ± 1% slippage),
        # not mark. Pad against that haircut so ~$10 dust can close.
        order_px = mid * Decimal("0.99") if side == "LONG" else mid * Decimal("1.01")
        if order_px <= 0:
            order_px = mid
        if mid > 0 and amount > 0:
            real_amt = amount
            padded = self._dust_pad_close_amount(pair, amount, order_px)
            if padded > amount:
                amount = padded
                mirror["amount"] = amount
                mirror["_dust_real_amount"] = real_amt
                mirror["_dust_rescue_done"] = True

        # Calculate PnL using the leverage actually used for this position
        actual_leverage = mirror.get("leverage", self.config.max_leverage)
        pnl_pct = None
        pnl_str = ""
        if entry_price and entry_price > 0 and mid > 0:
            if side == "LONG":
                pnl_pct = (float(mid) - float(entry_price)) / float(entry_price) * 100 * actual_leverage
            else:
                pnl_pct = (float(entry_price) - float(mid)) / float(entry_price) * 100 * actual_leverage
            pnl_str = f"  PnL~{pnl_pct:+.2f}%"
        if mid and mid > 0:
            mirror["_sent_exit_px"] = float(mid)
        if pnl_pct is not None:
            mirror["_sent_pnl_pct"] = float(pnl_pct)

        reason_str = f"  ({reason})" if reason else ""
        self.logger().info(
            f"Closing mirror {side} {amount} {pair} @ ~${mid:,.6f}{pnl_str}{reason_str}"
        )

        mirror["_closing"] = True
        mirror["_close_started"] = time.time()
        self._our_mirrors[pair] = mirror
        self._close_in_flight.add(pair)

        # SENT until ghost-clear sees flat on-chain — never log CLOSED here
        # (that spam is what filled the DASH dashboard with fake rows).
        _tn = self._trader_num_for_address(str(mirror.get("trader") or ""))
        trade_log(
            f"{YELLOW}{BOLD}"
            f"{_now():<22} {'WE CLOSE':<14} {'SENT':<8} {side:<6} {coin:<7} "
            f"{float(amount):>10.5f}  ${float(mid):>14,.6f}  "
            f"entry=${float(entry_price):,.6f}  opened={entry_time}{pnl_str}"
            f"{('  ' + reason) if reason else ''}"
            f"{('  T#' + str(_tn)) if _tn else ''}"
            f"{RESET}"
        )

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
            # Register for retry in case the IoC order fails to fill
            mirror["amount"] = amount  # include dust-padded size for retries
            self._pending_closes[order_id] = {
                "pair": pair,
                "mirror": mirror,
                "attempts": 1,
            }
        else:
            # Placement failed — keep mirror, back off so we do not log SENT every tick
            if pair not in self._our_mirrors:
                self._our_mirrors[pair] = mirror
            self._close_in_flight.discard(pair)
            self._close_backoff_until[pair] = time.time() + 45

    def did_fail_order(self, order_failed_event):
        """Called by the framework when any order fails. Retry failed close orders with larger slippage."""
        order_id = order_failed_event.order_id
        if order_id not in self._pending_closes:
            return
        ctx = self._pending_closes.pop(order_id)
        pair     = ctx["pair"]
        mirror   = ctx["mirror"]
        attempts = ctx["attempts"]

        err = str(
            getattr(order_failed_event, "error_message", None)
            or getattr(order_failed_event, "message", None)
            or ""
        )
        min_notional_fail = "minimum notional" in err.lower() or "min_notional" in err.lower()
        if min_notional_fail:
            # More slippage makes the submitted notional *smaller*. Pad size
            # and retry at 1% instead of climbing the ladder.
            n = int(mirror.get("_min_n_retries") or 0) + 1
            mirror["_min_n_retries"] = n
            if pair not in self._our_mirrors:
                self._our_mirrors[pair] = dict(mirror)
            if n > 8:
                self._our_mirrors[pair]["_closing"] = True
                self._close_in_flight.discard(pair)
                self._close_backoff_until[pair] = time.time() + 20
                self.logger().warning(
                    f"[close-retry] {pair} min-notional still failing after {n} pads — wait 20s"
                )
                return
            mid = self._get_mid_price(pair) or Decimal(str(mirror.get("entry_price") or 0))
            amt = Decimal(str(mirror.get("amount") or 0))
            side = mirror.get("side") or "LONG"
            order_px = mid * Decimal("0.99") if side == "LONG" else mid * Decimal("1.01")
            if order_px <= 0:
                order_px = mid
            if amt > 0 and order_px > 0:
                factor = Decimal("1.20") + Decimal("0.25") * n
                target = (self._hl_min_notional() * factor) / order_px
                padded = max(amt, target, self._dust_pad_close_amount(pair, amt, order_px))
                self._our_mirrors[pair]["amount"] = padded
                mirror["amount"] = padded
            self._our_mirrors[pair]["_closing"] = False
            self._our_mirrors[pair]["_dust_rescue_done"] = True
            self._our_mirrors[pair]["_min_n_retries"] = n
            self._close_in_flight.discard(pair)
            self._close_backoff_until.pop(pair, None)
            self.logger().warning(
                f"[close-retry] {pair} min-notional fail — padding CLOSE "
                f"{amt}→{mirror.get('amount')} and retrying (#{n})"
            )
            self._retry_close(pair, mirror, max(attempts, 1), Decimal("0.01"))
            return

        # Slippage ladder: 1% (initial, already tried) → 3% → 5% → 10% → 20%
        slippage_ladder = [
            Decimal("0.01"),
            Decimal("0.03"),
            Decimal("0.05"),
            Decimal("0.10"),
            Decimal("0.20"),
        ]
        if attempts >= len(slippage_ladder):
            # Last resort once: dust pad then one more close (min notional under $10)
            mid = self._get_mid_price(pair) or Decimal(str(mirror.get("entry_price") or 0))
            amt = Decimal(str(mirror.get("amount") or 0))
            if (
                mid > 0
                and amt > 0
                and (amt * Decimal(str(mid))) < self._hl_min_notional()
                and not mirror.get("_dust_rescue_done")
            ):
                self.logger().warning(
                    f"[close-retry] {pair} ladder exhausted — trying dust rescue close"
                )
                if pair not in self._our_mirrors:
                    self._our_mirrors[pair] = dict(mirror)
                self._our_mirrors[pair]["_dust_rescue_done"] = False
                self._close_mirror(pair, reason="dust rescue after failed closes")
                return
            # Keep the mirror and wait — retrying every poll is the DASH close storm.
            if pair not in self._our_mirrors:
                self._our_mirrors[pair] = mirror
            self._our_mirrors[pair]["_closing"] = True
            self._close_in_flight.discard(pair)
            self._close_backoff_until[pair] = time.time() + 180
            self.logger().warning(
                f"[close-retry] {pair} — slippage ladder exhausted, "
                f"backing off 180s before another close"
            )
            trade_log(
                f"{YELLOW}{BOLD}"
                f"{_now():<22} {'RETRY WAIT':<14} {'WARN':<8} {mirror['side']:<6} {mirror['coin']:<7} "
                f"slippage ladder exhausted — retry in 180s"
                f"{RESET}"
            )
            return

        next_slippage = slippage_ladder[attempts]
        self.logger().warning(
            f"[close-retry] Close failed for {pair} (attempt {attempts}) — "
            f"retrying with {float(next_slippage)*100:.0f}% slippage"
        )
        self._retry_close(pair, mirror, attempts + 1, next_slippage)

    def _retry_close(self, pair: str, mirror: dict, attempts: int, slippage: Decimal):
        """Re-place a close order with the given slippage and re-register for further retry if it fails again."""
        side   = mirror["side"]
        amount = Decimal(str(mirror["amount"]))
        coin   = mirror["coin"]
        entry_px = mirror.get("entry_price", Decimal("0"))
        mid      = self._get_mid_price(pair) or entry_px or Decimal("0")
        # Dust: pad against the slippage-haircut price the connector will use
        order_px = Decimal(str(mid)) * (Decimal("1") - slippage) if side == "LONG" else Decimal(str(mid)) * (Decimal("1") + slippage)
        if order_px <= 0:
            order_px = Decimal(str(mid))
        if order_px > 0 and amount > 0:
            padded = self._dust_pad_close_amount(pair, amount, order_px)
            if padded > amount:
                amount = padded
                mirror["amount"] = amount

        trade_log(
            f"{YELLOW}{BOLD}"
            f"{_now():<22} {'RETRY':<14} {'CLOSE':<8} {side:<6} {coin:<7} "
            f"${float(mid):>14,.6f}  slippage={float(slippage)*100:.0f}%  attempt #{attempts}"
            f"{RESET}"
        )

        try:
            if side == "LONG":
                order_id = self.sell(
                    self.config.connector_name, pair, amount,
                    OrderType.MARKET,
                    mid * (Decimal("1") - slippage),
                    PositionAction.CLOSE,
                )
            else:
                order_id = self.buy(
                    self.config.connector_name, pair, amount,
                    OrderType.MARKET,
                    mid * (Decimal("1") + slippage),
                    PositionAction.CLOSE,
                )
            if order_id:
                self._pending_order_ids.add(order_id)
                self._pending_closes[order_id] = {
                    "pair": pair, "mirror": mirror, "attempts": attempts,
                }
        except Exception as e:
            self.logger().error(f"[close-retry] Retry order placement failed for {pair}: {e}")

    # ── Status display ────────────────────────────────────────────────────────

    def format_status(self) -> str:
        n_mkts = len(self._registered_pairs())
        pending = len(getattr(self, "_pair_ensure_pending", set()))
        lines = [
            "",
            "── HL Copy Trader ─────────────────────────────────────────────",
            f"Traders: {len(self.config.trader_addresses)}  |  "
            f"Leverage: {self.config.max_leverage}x  |  "
            f"Alloc: {self.config.allocation_pct*100:.0f}% balance  |  "
            f"Poll: {self.config.poll_interval_seconds}s  |  "
            f"Markets: {n_mkts}"
            f"{f' (+{pending} pending)' if pending else ''}",
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
                        f"@ ${p['entry_px']:>14,.6f}{mirror_tag}"
                    )
            else:
                lines.append(f"Trader #{i} ({short}): no open positions")

        lines.append("")
        lines.append(f"Commodity mirrors  ({self.config.commodity_allocation_pct*100:.0f}% pool):")
        if self._commodity_mirrors:
            for coin, m in self._commodity_mirrors.items():
                mid = self._get_xyz_price(coin)
                pnl_str = ""
                if mid > 0 and m["entry_px"] > 0:
                    pnl = (mid - m["entry_px"]) / m["entry_px"] * 100 if m["side"] == "LONG" else (m["entry_px"] - mid) / m["entry_px"] * 100
                    pnl_str = f"  PnL~{pnl:+.2f}%"
                lines.append(f"  {m['side']:<5} {m['amount']:>10.4f} {coin:<20} entry=${m['entry_px']:,.4f}{pnl_str}")
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append(f"Crypto mirrors  ({(1-self.config.commodity_allocation_pct)*100:.0f}% pool):")
        if self._our_mirrors:
            for pair, m in self._our_mirrors.items():
                mid = self._get_mid_price(pair) or Decimal("0")
                pnl_str = ""
                entry_px = float(m.get("entry_price") or 0)
                lev = m.get("leverage", self.config.max_leverage)
                if float(mid) > 0 and entry_px > 0:
                    if m["side"] == "LONG":
                        pnl_pct = (float(mid) - entry_px) / entry_px * 100 * lev
                    else:
                        pnl_pct = (entry_px - float(mid)) / entry_px * 100 * lev
                    pnl_str = f"  PnL ~ {pnl_pct:+.2f}%"
                lines.append(
                    f"  {m['side']:<5} {m['amount']:>10} {pair:<12} "
                    f"from trader {m['trader'][:10]}...  {lev}x{pnl_str}"
                )
        else:
            lines.append("  (none)")

        lines.append("─" * 63)
        return "\n".join(lines)
