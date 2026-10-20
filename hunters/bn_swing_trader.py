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
bn_swing_trader.py
==================
Hyperliquid perpetual swing bot for BTC, ETH, and LTC.

- Dashboard sets per-coin bias: LONG | SHORT | FLAT  (conf/bn_swing_bias.json)
- Dashboard 100%/50%/20% buttons open a market position immediately
- Bollinger + 1h trend filter can also enter (only in bias direction)
- Hard ROE rules:
    take-profit  ≥ +tp_roe_pct   (default 93%)
    stop-loss    ≤ -sl_roe_pct   (default 20%)
- One position per coin; force-close via conf/force_close_bn_swing.txt
- Trades on HL subaccount 
  (separate from copy trader and Helliot).

ROE ≈ (price_move_pct) × leverage.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, PositionAction
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
if "/home/hummingbot/scripts" not in sys.path:
    sys.path.insert(0, "/home/hummingbot/scripts")

try:
    from bot_sl_override import effective_sl_roe
except ImportError:
    def effective_sl_roe(bot_id, default=20.0):
        return float(default)

SL_FILE = "/home/hummingbot/conf/bot_sl.json"


def _live_sl_roe(default=20.0) -> float:
    """Home SL from bot_sl.json — do not rely on import path."""
    try:
        with open(SL_FILE, "r") as f:
            row = (json.load(f) or {}).get("hummingbot-bn-swing") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe("hummingbot-bn-swing", default))
    except Exception:
        return float(default)

try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default


def _ctrl(key, default):
    return _ctrl_live("hummingbot-bn-swing", key, default)

BOT_ID = "hummingbot-bn-swing"
BIAS_FILE = "/home/hummingbot/conf/bn_swing_bias.json"
STATUS_FILE = "/home/hummingbot/conf/bn_swing_status.json"
RESERVED_COINS_FILE = "/home/hummingbot/conf/hl_reserved_coins.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/bn_swing_trades.json"

SWING_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
DEFAULT_SUBACCOUNT = ""
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"


def _subaccount() -> str:
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        for bot in data.get("bots") or []:
            if bot.get("id") == "hummingbot-bn-swing":
                addr = str(bot.get("address") or "").strip()
                if addr.lower().startswith("0x") and len(addr) == 42:
                    return addr
    except Exception:
        pass
    return DEFAULT_SUBACCOUNT


def _pair_for(coin: str) -> str:
    return f"{str(coin).upper()}-USD"


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def _as_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _as_bool(v):
    if v is None:
        return None
    return bool(v)


def _atomic_json_write(path: str, payload: dict):
    raw = json.dumps(payload, indent=2, default=_json_default)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class BNSwingTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))

    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(SWING_COINS))
    # Fallback if a coin is missing from leverage_by_coin / HL meta
    leverage: int = Field(40)
    # Per-coin caps (HL live max: BTC 40x, ETH 25x, LTC 10x). Used when not fetching meta.
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    # If true, use Hyperliquid's published max leverage for each coin.
    use_exchange_max_leverage: bool = Field(True)

    # Sizing: fraction of leftover free USD used as margin per new trade (1.0 = 100%)
    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    # Hard ROE exits (percent points, e.g. 93 = +93% ROE)
    tp_roe_pct: float = Field(93.0)
    sl_roe_pct: float = Field(20.0)

    candles_connector: str = Field("hyperliquid_perpetual")
    interval: str = Field("15m")
    bb_length: int = Field(20)
    bb_std: float = Field(2.0)
    bb_long_threshold: float = Field(0.25)
    bb_short_threshold: float = Field(0.75)

    trend_interval: str = Field("1h")
    trend_sma_length: int = Field(50)

    signal_interval: int = Field(30)
    status_interval: int = Field(30)

    def swing_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or SWING_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in SWING_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(SWING_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.swing_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class BNSwingTrader(StrategyV2Base):
    """Dashboard bias + BB swing entries + hard ROE TP/SL on HL BTC/ETH."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: BNSwingTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.swing_coins()

        self._bias: Dict[str, str] = {c: "FLAT" for c in self._coins}
        self._last_bias_read = 0.0

        self._bbp: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._bb_lower: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._bb_upper: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._sma: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._above_trend: Dict[str, Optional[bool]] = {c: None for c in self._coins}
        self._last_signal_check: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._position_side: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._entry_price: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._amount: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._entry_time: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._hl_max_leverage: Dict[str, int] = {}
        self._leverage_used: Dict[str, int] = {c: self._leverage_for(c) for c in self._coins}
        self._leverage_set: Dict[str, bool] = {c: False for c in self._coins}
        self._last_entry_attempt: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._close_pending_until: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._trading_active = False
        self._last_status_log = 0.0
        self._last_global_entry = 0.0
        self._fetch_hl_max_leverage()
        self._leverage_used = {c: self._leverage_for(c) for c in self._coins}
        self._lev_tier_seen = self._leverage_tier()

        for coin in self._coins:
            pair = _pair_for(coin)
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.interval,
                max_records=self.config.bb_length + 15,
            ))
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.trend_interval,
                max_records=self.config.trend_sma_length + 10,
            ))

        self._write_reserved_coins()
        lev_s = " ".join(f"{c}={self._leverage_for(c)}x" for c in self._coins)
        self.logger().info(
            f"HL Swing Trader init | sub={_subaccount()} coins={','.join(self._coins)} lev={lev_s} "
            f"(exchange max) TP_ROE={config.tp_roe_pct}% SL_ROE={config.sl_roe_pct}% "
            f"BB({config.bb_length},{config.bb_std}) thr={config.bb_long_threshold}/{config.bb_short_threshold}"
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def on_tick(self):
        self._read_bias()

        if not self._trading_active:
            self._check_ready()
            self._write_status()
            return

        self._restore_positions_from_exchange()
        self._handle_sql_commands()

        tier_now = self._leverage_tier()
        if tier_now != self._lev_tier_seen:
            self._lev_tier_seen = tier_now
            for c in self._coins:
                self._leverage_set[c] = False
        for coin in self._coins:
            if not self._leverage_set[coin]:
                self._set_leverage(coin)

        for coin in self._coins:
            if self._is_close_pending(coin):
                continue
            if self._position_side[coin] is not None:
                self._check_roe_exits(coin)
            else:
                self._update_signal(coin)
                self._try_enter(coin)

        now = self.current_timestamp
        if now - self._last_status_log >= self.config.status_interval:
            self._last_status_log = now
            self.logger().info(self.format_status())
        self._write_status()

    def _check_ready(self):
        try:
            connector = self.connectors[self.config.connector_name]
            if connector.ready:
                self._trading_active = True
                mids = ", ".join(
                    f"{c}={float(self._get_mid(c)) if self._get_mid(c) else 'n/a'}"
                    for c in self._coins
                )
                self.logger().info(
                    f"HL Swing ready | {self.config.connector_name} sub={_subaccount()} "
                    f"{mids} bias={self._bias}"
                )
                self._restore_positions_from_exchange(force=True)
        except Exception as e:
            self.logger().warning(f"Not ready yet: {e}")

    def _fetch_hl_max_leverage(self):
        """Read Hyperliquid's published max leverage for swing coins."""
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({"type": "meta"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            for asset in data.get("universe") or []:
                coin = str(asset.get("name") or "").upper()
                lev = asset.get("maxLeverage")
                if coin in self._coins and lev:
                    self._hl_max_leverage[coin] = int(lev)
            self.logger().info(
                f"[leverage] HL max: {self._hl_max_leverage or 'n/a'}"
            )
        except Exception as e:
            self.logger().warning(f"[leverage] Could not fetch HL max leverage: {e}")

    def _leverage_tier(self) -> str:
        try:
            from leverage_tier import tier_name
            return tier_name(BOT_ID)
        except Exception:
            return "MAX"

    def _leverage_for(self, coin: str) -> int:
        """Tiered leverage for this coin. MAX keeps the HL max, or the yml cap."""
        by_coin = self.config.leverage_by_coin or {}
        cfg = int(by_coin.get(coin) or self.config.leverage or 40)
        hl = int(self._hl_max_leverage.get(coin) or 0)
        use_max = bool(getattr(self.config, "use_exchange_max_leverage", True))
        try:
            from leverage_tier import sized
            return sized(cfg, hl, use_max, self._leverage_tier())
        except Exception:
            if use_max and hl > 0:
                return hl
            if hl > 0:
                return max(1, min(cfg, hl))
            return max(1, cfg)

    def _set_leverage(self, coin: str):
        # An open coin is not marked set. The tier applies once that coin is flat.
        if self._position_side.get(coin) is not None:
            return
        lev = self._leverage_for(coin)
        if self._leverage_set.get(coin) and int(self._leverage_used.get(coin) or 0) == lev:
            return
        try:
            self.connectors[self.config.connector_name].set_leverage(
                trading_pair=_pair_for(coin),
                leverage=lev,
            )
            self._leverage_set[coin] = True
            self._leverage_used[coin] = lev
            self.logger().info(f"leverage {coin} {self._leverage_tier()} {lev}x")
        except Exception as e:
            self.logger().warning(f"set_leverage {coin} failed (retry): {e}")

    # ── Bias / force close ───────────────────────────────────────────────────

    def _read_bias(self):
        now = time.time()
        if now - self._last_bias_read < 1.0:
            return
        self._last_bias_read = now
        try:
            from matrix_store import ensure, live_bias
            ensure()
            sql = live_bias(BOT_ID, self._coins, VALID_BIAS)
        except Exception:
            sql = None
        if sql is not None:
            for c in self._coins:
                if sql[c] != self._bias[c]:
                    self.logger().info(f"Bias {c} changed: {self._bias[c]} → {sql[c]}")
            self._bias = sql
            return
        try:
            if not os.path.exists(BIAS_FILE):
                return
            with open(BIAS_FILE, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            parsed = {c: "FLAT" for c in self._coins}
            for c in self._coins:
                v = str(data.get(c) or "").upper()
                if v in VALID_BIAS:
                    parsed[c] = v
            coins_map = data.get("coins")
            if isinstance(coins_map, dict):
                for c in self._coins:
                    v = str(coins_map.get(c) or "").upper()
                    if v in VALID_BIAS:
                        parsed[c] = v
            legacy_coin = str(data.get("coin") or "").upper()
            legacy_bias = str(data.get("bias") or "").upper()
            if legacy_coin in self._coins and legacy_bias in VALID_BIAS:
                parsed[legacy_coin] = legacy_bias
            for c in self._coins:
                if parsed[c] != self._bias[c]:
                    self.logger().info(f"Bias {c} changed: {self._bias[c]} → {parsed[c]}")
            self._bias = parsed
        except Exception as e:
            self.logger().warning(f"Bias read error: {e}")

    def _sql_cmd(self, kinds):
        try:
            from matrix_store import ensure, command_next
            ensure()
            return command_next(BOT_ID, kinds=kinds)
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

    def _handle_sql_commands(self):
        if not self._trading_active:
            return
        cmd = self._sql_cmd(("close", "open"))
        if not cmd:
            return
        kind = str(cmd.get("kind") or "")
        if kind == "close":
            self._run_force_close(cmd)
        elif kind == "open":
            self._run_force_open(cmd)

    def _run_force_close(self, cmd):
        payload = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        try:
            from matrix_store import close_coin_list
            targets = close_coin_list(payload, self._coins)
        except Exception:
            targets = list(self._coins)
        self._restore_positions_from_exchange(force=True)
        for coin in targets:
            if self._position_side[coin] is not None:
                self.logger().info(f"Dashboard FORCE CLOSE {coin}")
                self._close_position(coin, "dashboard force close")
            else:
                self.logger().info(f"Force close {coin} requested but flat")
        self._sql_done(cmd)

    def _run_force_open(self, cmd):
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        coin = str(data.get("coin") or "").upper()
        if coin not in self._coins:
            self.logger().warning(f"Force open ignored — invalid coin {coin!r}")
            self._sql_done(cmd, "invalid coin")
            return
        try:
            raw_pct = float(data.get("pct"))
        except (TypeError, ValueError):
            raw_pct = 0.0
        if raw_pct > 1:
            raw_pct = raw_pct / 100.0
        pct = None
        for allowed in (0.2, 0.5, 1.0):
            if abs(raw_pct - allowed) < 1e-6:
                pct = allowed
                break
        if pct is None:
            self.logger().warning(f"Force open ignored — pct must be 20/50/100, got {data.get('pct')}")
            self._sql_done(cmd, "invalid pct")
            return
        side = str(data.get("side") or self._bias.get(coin) or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(
                f"Force open {coin} skipped — set LONG or SHORT bias first (got {side or 'FLAT'})"
            )
            self._sql_done(cmd, "need long/short")
            return
        if self._position_side[coin] is not None:
            self.logger().info(f"Force open {coin} skipped — already {self._position_side[coin]}")
            self._sql_done(cmd, "already open")
            return
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        self.logger().info(
            f"Dashboard FORCE OPEN {side} {coin} at {pct*100:.0f}% leftover"
        )
        self._open_now(coin, side, Decimal(str(pct)), reason=f"dashboard {pct*100:.0f}%")
        self._sql_done(cmd)

    # ── Market helpers ───────────────────────────────────────────────────────

    def _get_mid(self, coin: str) -> Optional[Decimal]:
        try:
            p = self.connectors[self.config.connector_name].get_mid_price(_pair_for(coin))
            return Decimal(str(p)) if p else None
        except Exception:
            return None

    def _get_free_usd(self) -> Decimal:
        raw = Decimal("0")
        for asset in ("USD", "USDC", "USDT"):
            try:
                bal = self.connectors[self.config.connector_name].get_available_balance(asset)
                if bal and Decimal(str(bal)) > 0:
                    raw = Decimal(str(bal))
                    break
            except Exception:
                continue
        try:
            from risk_park import deduct
            return deduct(BOT_ID, raw)
        except Exception:
            return raw

    def _quantize_amount(self, coin: str, amount: Decimal) -> Decimal:
        pair = _pair_for(coin)
        try:
            conn = self.connectors[self.config.connector_name]
            if hasattr(conn, "quantize_order_amount"):
                q = conn.quantize_order_amount(pair, amount)
                if q and Decimal(str(q)) > 0:
                    return Decimal(str(q))
        except Exception:
            pass
        return amount.quantize(self._qty_step(coin), rounding=ROUND_DOWN)

    def _qty_step(self, coin: str) -> Decimal:
        if coin == "BTC":
            return Decimal("0.00001")
        if coin == "ETH":
            return Decimal("0.0001")
        if coin == "LTC":
            return Decimal("0.01")
        return Decimal("0.0001")

    # ── Signal (Bollinger swing) ─────────────────────────────────────────────

    def _update_signal(self, coin: str):
        now = self.current_timestamp
        if now - self._last_signal_check[coin] < self.config.signal_interval:
            return
        self._last_signal_check[coin] = now
        pair = _pair_for(coin)
        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.interval,
                max_records=self.config.bb_length + 10,
            )
            if df is None or len(df) < self.config.bb_length:
                return
            closes = list(df["close"].astype(float).values[-self.config.bb_length:])
            sma = sum(closes) / self.config.bb_length
            std = statistics.stdev(closes) if len(closes) > 1 else 0.0
            if std <= 0:
                return
            lower = sma - self.config.bb_std * std
            upper = sma + self.config.bb_std * std
            price = closes[-1]
            if upper <= lower:
                return
            self._bbp[coin] = float((price - lower) / (upper - lower))
            self._bb_lower[coin] = float(lower)
            self._bb_upper[coin] = float(upper)

            df1h = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.trend_interval,
                max_records=self.config.trend_sma_length + 5,
            )
            if df1h is not None and len(df1h) >= self.config.trend_sma_length:
                h_closes = list(df1h["close"].astype(float).values[-self.config.trend_sma_length:])
                self._sma[coin] = float(sum(h_closes) / self.config.trend_sma_length)
                self._above_trend[coin] = bool(float(price) > self._sma[coin])
            else:
                self._above_trend[coin] = None
        except Exception as e:
            self.logger().warning(f"Signal error {coin}: {e}")

    def _long_signal(self, coin: str) -> bool:
        bbp = self._bbp[coin]
        if bbp is None:
            return False
        if bbp > _ctrl("bb_long_threshold", self.config.bb_long_threshold):
            return False
        if self._above_trend[coin] is False:
            return False
        return True

    def _short_signal(self, coin: str) -> bool:
        bbp = self._bbp[coin]
        if bbp is None:
            return False
        if bbp < _ctrl("bb_short_threshold", self.config.bb_short_threshold):
            return False
        if self._above_trend[coin] is True:
            return False
        return True

    # ── Entries ──────────────────────────────────────────────────────────────

    def _try_enter(self, coin: str):
        if self._bias.get(coin) == "FLAT":
            return
        if self._position_side[coin] is not None:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        if self.current_timestamp - self._last_global_entry < 15:
            return

        want_long = self._bias[coin] == "LONG" and self._long_signal(coin)
        want_short = self._bias[coin] == "SHORT" and self._short_signal(coin)
        if not want_long and not want_short:
            return
        side = "LONG" if want_long else "SHORT"
        pct = Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0)))
        self._open_now(coin, side, pct, reason="BB signal")

    def _open_now(self, coin: str, side: str, margin_pct: Decimal, reason: str):
        """Market-open coin using this fraction of leftover free USD as margin."""
        if side not in ("LONG", "SHORT"):
            return
        if self._position_side[coin] is not None:
            return
        mid = self._get_mid(coin)
        if not mid or mid <= 0:
            self.logger().warning(f"Skip {coin} open — no mid price")
            return

        free = self._get_free_usd()
        pct = Decimal(str(margin_pct))
        if pct <= 0:
            return
        if pct > 1:
            pct = Decimal("1")
        margin = (free * pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self.logger().info(
                f"Skip {coin} entry — margin ${float(margin):.2f} < min ${float(self.config.min_margin_usd)}"
            )
            self._last_entry_attempt[coin] = self.current_timestamp
            return

        lev = self._leverage_for(coin)
        notional = margin * Decimal(str(lev))
        min_n = Decimal(str(self.config.min_notional_usd))
        if notional < min_n:
            needed_margin = (min_n / Decimal(str(lev))).quantize(Decimal("0.01"))
            if needed_margin > free * Decimal("0.80"):
                self.logger().info(
                    f"Skip {coin} entry — notional ${float(notional):.2f} < min ${float(min_n)} "
                    f"and free ${float(free):.2f} cannot cover ${float(needed_margin):.2f} margin"
                )
                self._last_entry_attempt[coin] = self.current_timestamp
                return
            notional = min_n
            margin = needed_margin

        amount = self._quantize_amount(coin, notional / mid)
        if amount <= 0:
            return
        max_margin = free * pct
        if lev > 0 and amount * mid / Decimal(str(lev)) > max_margin:
            amount = self._quantize_amount(coin, (max_margin * Decimal(str(lev))) / mid)
            if amount <= 0:
                return
        if amount * mid < min_n:
            step_up = (min_n / mid)
            amount = self._quantize_amount(coin, step_up)
            if amount * mid < min_n:
                amount = self._quantize_amount(coin, amount + self._qty_step(coin))
            if amount <= 0 or amount * mid < min_n * Decimal("0.9"):
                self.logger().info(f"Skip {coin} entry — quantized amount below min notional")
                self._last_entry_attempt[coin] = self.current_timestamp
                return

        pair = _pair_for(coin)
        self._last_entry_attempt[coin] = self.current_timestamp
        self._last_global_entry = self.current_timestamp
        bbp = self._bbp[coin]
        trend = self._above_trend[coin]
        bbp_s = f"{bbp:.3f}" if bbp is not None else "n/a"
        self.logger().info(
            f"SWING ENTRY {side} {pair} | {reason} | {float(pct)*100:.0f}% leftover "
            f"BBP={bbp_s} trend={'up' if trend else 'down' if trend is False else '?'} "
            f"amount={amount} mid=${float(mid):.4f} margin~${float(margin):.2f} "
            f"notional~${float(amount * mid):.2f} lev={lev}x"
        )
        try:
            if side == "LONG":
                self.buy(
                    self.config.connector_name,
                    pair,
                    amount,
                    OrderType.MARKET,
                    mid,
                    PositionAction.OPEN,
                )
            else:
                self.sell(
                    self.config.connector_name,
                    pair,
                    amount,
                    OrderType.MARKET,
                    mid,
                    PositionAction.OPEN,
                )
            self._position_side[coin] = side
            self._entry_price[coin] = mid
            self._amount[coin] = amount
            self._entry_time[coin] = self.current_timestamp
            self._leverage_used[coin] = lev
            self._log_trade({
                "action": "OPEN",
                "coin": coin,
                "side": side,
                "amount": _as_float(amount),
                "price": _as_float(mid),
                "leverage": int(lev),
                "reason": str(reason),
                "pnl_usd": None,
                "roe_pct": None,
            })
            self._write_status()
        except Exception as e:
            self.logger().error(f"Entry order failed {coin}: {e}")

    # ── Position restore / ROE exits ─────────────────────────────────────────

    def _is_close_pending(self, coin: str) -> bool:
        until = float(self._close_pending_until.get(coin) or 0)
        return until > 0 and self.current_timestamp < until

    def _restore_positions_from_exchange(self, force: bool = False):
        missing = [c for c in self._coins if self._position_side[c] is None]
        if not force and not missing:
            return
        try:
            connector = self.connectors[self.config.connector_name]
            positions = connector.account_positions or {}
            found = {c: False for c in self._coins}
            for key, pos in positions.items():
                key_s = str(key).upper()
                pair_s = str(getattr(pos, "trading_pair", "") or "").upper()
                for coin in self._coins:
                    if self._is_close_pending(coin):
                        continue
                    pair = _pair_for(coin)
                    if pair not in key_s and pair not in pair_s and coin not in key_s:
                        continue
                    amt = Decimal(str(getattr(pos, "amount", 0) or 0))
                    if abs(amt) < Decimal("1e-8"):
                        continue
                    entry = Decimal(str(getattr(pos, "entry_price", 0) or 0))
                    side = "LONG" if amt > 0 else "SHORT"
                    prev = self._position_side[coin]
                    self._position_side[coin] = side
                    self._entry_price[coin] = entry if entry > 0 else self._get_mid(coin)
                    self._amount[coin] = abs(amt)
                    for attr in ("leverage", "leverage_level"):
                        if hasattr(pos, attr) and getattr(pos, attr):
                            try:
                                self._leverage_used[coin] = int(getattr(pos, attr))
                                break
                            except (TypeError, ValueError):
                                pass
                    if self._entry_time[coin] is None:
                        self._entry_time[coin] = self.current_timestamp
                    found[coin] = True
                    if prev != side:
                        self.logger().info(
                            f"Restored {side} {self._amount[coin]} {pair} @ {self._entry_price[coin]}"
                        )
            # If we thought we were in a position but exchange is flat, clear local state
            for coin in self._coins:
                if self._position_side[coin] is not None and not found[coin]:
                    # recently sent an order — give it a moment
                    et = self._entry_time[coin]
                    if et and (self.current_timestamp - et) < 20:
                        continue
                    self.logger().info(f"{coin} flat on exchange — clearing local swing state")
                    self._position_side[coin] = None
                    self._entry_price[coin] = None
                    self._amount[coin] = None
                    self._entry_time[coin] = None
        except Exception as e:
            self.logger().warning(f"Restore position failed: {e}")

    def _calc_roe_pct(self, coin: str, mid: Decimal) -> Optional[float]:
        entry_px = self._entry_price[coin]
        if not entry_px or entry_px <= 0 or not mid:
            return None
        entry = float(entry_px)
        px = float(mid)
        lev = float(self._leverage_used[coin] or self._leverage_for(coin))
        if self._position_side[coin] == "LONG":
            price_move = (px - entry) / entry
        else:
            price_move = (entry - px) / entry
        return price_move * lev * 100.0

    def _check_roe_exits(self, coin: str):
        if self._is_close_pending(coin):
            return
        mid = self._get_mid(coin)
        if not mid or not self._entry_price[coin]:
            return
        roe = self._calc_roe_pct(coin, mid)
        if roe is None:
            return

        if roe >= float(_ctrl("tp_roe_pct", self.config.tp_roe_pct)):
            self.logger().info(
                f"TAKE PROFIT {coin} ROE={roe:.2f}% ≥ {_ctrl('tp_roe_pct', self.config.tp_roe_pct)}% | "
                f"{self._position_side[coin]} entry={self._entry_price[coin]} mark={mid}"
            )
            self._close_position(coin, f"TP ROE {roe:.2f}%")
            return

        sl = float(_live_sl_roe(self.config.sl_roe_pct))
        if roe <= -sl:
            self.logger().info(
                f"STOP LOSS {coin} ROE={roe:.2f}% ≤ -{sl}% | "
                f"{self._position_side[coin]} entry={self._entry_price[coin]} mark={mid}"
            )
            self._close_position(coin, f"SL ROE {roe:.2f}%")
            return

    def _close_position(self, coin: str, reason: str):
        mid = self._get_mid(coin) or self._entry_price[coin] or Decimal("0")
        amount = self._amount[coin]
        side = self._position_side[coin]
        if not amount or not side:
            self._restore_positions_from_exchange(force=True)
            amount = self._amount[coin]
            side = self._position_side[coin]
        if not amount or not side:
            self.logger().warning(f"Close requested {coin} ({reason}) but no position state")
            return
        pair = _pair_for(coin)
        close_px = mid
        if mid:
            close_px = mid * Decimal("0.99") if side == "LONG" else mid * Decimal("1.01")
        try:
            if side == "LONG":
                self.sell(
                    self.config.connector_name,
                    pair,
                    amount,
                    OrderType.MARKET,
                    close_px,
                    PositionAction.CLOSE,
                )
            else:
                self.buy(
                    self.config.connector_name,
                    pair,
                    amount,
                    OrderType.MARKET,
                    close_px,
                    PositionAction.CLOSE,
                )
            roe = self._calc_roe_pct(coin, mid) if mid else None
            roe_s = f"ROE~{roe:.2f}%" if roe is not None else "ROE=n/a"
            self.logger().info(f"CLOSED {side} {amount} {pair} | reason={reason} | {roe_s}")
            entry = _as_float(self._entry_price[coin])
            exit_px = _as_float(mid)
            amt = _as_float(amount)
            pnl = None
            if entry and exit_px and amt:
                if side == "LONG":
                    pnl = amt * (exit_px - entry)
                else:
                    pnl = amt * (entry - exit_px)
            self._log_trade({
                "action": "CLOSE",
                "coin": coin,
                "side": side,
                "amount": amt,
                "price": exit_px,
                "entry_price": entry,
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
                "reason": str(reason),
                "pnl_usd": round(pnl, 4) if pnl is not None else None,
                "roe_pct": round(roe, 3) if roe is not None else None,
            })
        except Exception as e:
            self.logger().error(f"Close failed {coin}: {e}")
            return
        # Block restore/re-TP until the exchange position is actually gone
        self._close_pending_until[coin] = self.current_timestamp + 45
        self._position_side[coin] = None
        self._entry_price[coin] = None
        self._amount[coin] = None
        self._entry_time[coin] = None
        self._write_status()

    # ── Status ───────────────────────────────────────────────────────────────

    def format_status(self) -> str:
        free = self._get_free_usd()
        lines = [
            f"HL Swing | coins={','.join(self._coins)} | "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"free=${float(free):.2f}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            bbp = self._bbp[coin]
            sma = self._sma[coin]
            trend = self._above_trend[coin]
            bbp_s = f"{bbp:.3f}" if bbp is not None else "n/a"
            sma_s = f"{sma:.4f}" if sma is not None else "n/a"
            trend_s = "UP" if trend else ("DOWN" if trend is False else "n/a")
            roe_s = f"{roe:.2f}%" if roe is not None else "n/a"
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} BBP={bbp_s} trend={trend_s} sma={sma_s} | "
                f"pos={self._position_side[coin] or 'FLAT'} lev={self._leverage_used[coin]}x "
                f"entry={self._entry_price[coin]} amt={self._amount[coin]} mid={mid} ROE={roe_s}"
            )
        lines.append(
            f"  TP={_ctrl('tp_roe_pct', self.config.tp_roe_pct)}% SL=-{_live_sl_roe(self.config.sl_roe_pct)}%"
        )
        return "\n".join(lines)

    def _read_trade_log(self) -> dict:
        empty = {"trades": [], "realized_pnl_usd": 0.0, "n_wins": 0, "n_losses": 0, "n_opens": 0, "n_closes": 0}
        try:
            with open(TRADE_LOG_FILE, "r") as f:
                data = json.load(f) or {}
            if isinstance(data, dict) and isinstance(data.get("trades"), list):
                return data
        except Exception:
            pass
        return empty

    def _summarize_trades(self, trades: list) -> dict:
        realized = 0.0
        wins = losses = opens = closes = 0
        for t in trades:
            if not isinstance(t, dict):
                continue
            action = str(t.get("action") or "").upper()
            if action == "OPEN":
                opens += 1
            elif action == "CLOSE":
                closes += 1
                pnl = t.get("pnl_usd")
                if pnl is None:
                    continue
                try:
                    p = float(pnl)
                except (TypeError, ValueError):
                    continue
                realized += p
                if p > 0:
                    wins += 1
                elif p < 0:
                    losses += 1
        return {
            "realized_pnl_usd": round(realized, 4),
            "n_wins": wins,
            "n_losses": losses,
            "n_opens": opens,
            "n_closes": closes,
        }

    def _log_trade(self, event: dict):
        data = self._read_trade_log()
        trades = list(data.get("trades") or [])
        rec = dict(event or {})
        # Ignore duplicate CLOSE/OPEN spam for the same coin within 30s
        if trades:
            last = trades[-1]
            same = (
                str(last.get("action")) == str(rec.get("action"))
                and str(last.get("coin")) == str(rec.get("coin"))
                and str(last.get("side")) == str(rec.get("side"))
            )
            if same:
                try:
                    last_ts = str(last.get("ts") or "")
                    last_epoch = time.mktime(time.strptime(last_ts.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S"))
                    if time.time() - last_epoch < 30:
                        return
                except Exception:
                    return
        rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        rec["id"] = f"{int(time.time()*1000)}-{rec.get('coin','')}-{rec.get('action','')}"
        trades.append(rec)
        trades = trades[-200:]
        summary = self._summarize_trades(trades)
        payload = {"trades": trades, **summary}
        try:
            _atomic_json_write(TRADE_LOG_FILE, payload)
        except Exception as e:
            self.logger().warning(f"trade log write failed: {e}")

    def _open_upnl_usd(self) -> float:
        total = 0.0
        for coin in self._coins:
            side = self._position_side[coin]
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid = _as_float(self._get_mid(coin))
            if not side or not entry or not amt or not mid:
                continue
            if side == "LONG":
                total += amt * (mid - entry)
            else:
                total += amt * (entry - mid)
        return round(total, 4)

    def _write_reserved_coins(self):
        # Swing uses its own subaccount — do not lock coins on the copy trader.
        return

    def _write_status(self):
        try:
            from risk_park import maybe_send
            maybe_send(BOT_ID, self.connectors.get(self.config.connector_name))
        except Exception:
            pass
        positions = {}
        for coin in self._coins:
            mid = self._get_mid(coin)
            side = self._position_side[coin]
            roe = self._calc_roe_pct(coin, mid) if mid and side else None
            bbp = _as_float(self._bbp[coin])
            lo = _as_float(self._bb_lower[coin])
            hi = _as_float(self._bb_upper[coin])
            thr = float(_ctrl("bb_long_threshold", self.config.bb_long_threshold))
            trend = _as_bool(self._above_trend[coin])
            long_ok = bbp is not None and bbp <= thr and trend is not False
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": _as_float(self._entry_price[coin]),
                "amount": _as_float(self._amount[coin]),
                "mid_price": _as_float(mid),
                "roe_pct": round(roe, 3) if roe is not None else None,
                "bbp": round(bbp, 4) if bbp is not None else None,
                "bb_lower": round(lo, 8) if lo is not None else None,
                "bb_upper": round(hi, 8) if hi is not None else None,
                "bb_long_px": round(lo + thr * (hi - lo), 8) if lo is not None and hi is not None and hi > lo else None,
                "trend_up": trend,
                "sma_1h": _as_float(self._sma[coin]),
                "long_ok": bool(long_ok),
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
            }
        # Compact top-level fields for older dashboard readers (BTC preferred)
        lead = positions.get("BTC") or positions.get(self._coins[0]) or {}
        log = self._read_trade_log()
        summary = self._summarize_trades(list(log.get("trades") or []))
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "ready": self._trading_active,
            "connector": self.config.connector_name,
            "subaccount": _subaccount(),
            "coins": list(self._coins),
            "pairs": [_pair_for(c) for c in self._coins],
            "leverage": {c: self._leverage_for(c) for c in self._coins},
            "tp_roe_pct": float(_ctrl("tp_roe_pct", self.config.tp_roe_pct)),
            "bb_long_threshold": float(_ctrl("bb_long_threshold", self.config.bb_long_threshold)),
            "bb_short_threshold": float(_ctrl("bb_short_threshold", self.config.bb_short_threshold)),
            "margin_pct": float(_ctrl("margin_pct", self.config.margin_pct or 1.0)),
            "sl_roe_pct": float(_live_sl_roe(self.config.sl_roe_pct)),
            "free_usd": _as_float(self._get_free_usd()) or 0.0,
            "free_usdt": _as_float(self._get_free_usd()) or 0.0,
            "open_upnl_usd": self._open_upnl_usd(),
            "realized_pnl_usd": summary.get("realized_pnl_usd", 0.0),
            "n_wins": summary.get("n_wins", 0),
            "n_losses": summary.get("n_losses", 0),
            "n_opens": summary.get("n_opens", 0),
            "n_closes": summary.get("n_closes", 0),
            "bias": dict(self._bias),
            "positions": positions,
            "pair": lead.get("pair"),
            "position_side": lead.get("position_side"),
            "entry_price": lead.get("entry_price"),
            "amount": lead.get("amount"),
            "mid_price": lead.get("mid_price"),
            "roe_pct": lead.get("roe_pct"),
            "bbp": lead.get("bbp"),
            "trend_up": lead.get("trend_up"),
        }
        try:
            _atomic_json_write(STATUS_FILE, payload)
        except Exception as e:
            self.logger().warning(f"status write failed: {e}")
        try:
            from matrix_store import try_status_put
            try_status_put(BOT_ID, payload)
        except Exception:
            pass
