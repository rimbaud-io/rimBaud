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
shortn_trader.py
================
Bollinger bot for BTC / ETH / LTC / TAO.

- Auto-entry stays SHORT: 15m BBP high + 1h SMA-50 not up.
- 100% leftover follows current BIAS (LONG or SHORT). Never flips an open clip.
- At +90% ROE: close ("piss off").
- Do not short again until price has bounced at least +20% ROE from that exit.
- −20% ROE stop on open shorts.

Dashboard: LONG/SHORT/FLAT + 100% leftover follows current BIAS.
Subaccount is read from conf/bot_accounts.json (Confiig).
"""

from __future__ import annotations

import json
import os
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
            row = (json.load(f) or {}).get("hummingbot-shortn") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe("hummingbot-shortn", default))
    except Exception:
        return float(default)

try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default


def _ctrl(key, default):
    return _ctrl_live("hummingbot-shortn", key, default)

BOT_ID = "hummingbot-shortn"
BIAS_FILE = "/home/hummingbot/conf/shortn_bias.json"
STATUS_FILE = "/home/hummingbot/conf/shortn_status.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/shortn_trades.json"

SHORTN_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
DEFAULT_SUBACCOUNT = ""
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"


def _subaccount() -> str:
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        for bot in data.get("bots") or []:
            if bot.get("id") == "hummingbot-shortn":
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


def _atomic_json_write(path: str, payload: dict):
    raw = json.dumps(payload, indent=2, default=_json_default)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class ShortnTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(SHORTN_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    tp_roe_pct: float = Field(90.0)
    sl_roe_pct: float = Field(20.0)
    recover_roe_pct: float = Field(20.0)

    candles_connector: str = Field("hyperliquid_perpetual")
    interval: str = Field("15m")
    bb_length: int = Field(20)
    bb_std: float = Field(2.0)
    bb_short_threshold: float = Field(0.75)
    trend_interval: str = Field("1h")
    trend_sma_length: int = Field(50)
    candle_records: int = Field(120)

    signal_interval: int = Field(30)
    status_interval: int = Field(30)

    def shortn_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or SHORTN_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in SHORTN_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(SHORTN_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.shortn_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class ShortnTrader(StrategyV2Base):
    """Bollinger shorts, 90% ROE close, wait 20% ROE bounce, short again."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: ShortnTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.shortn_coins()
        self._hl_max_leverage: Dict[str, int] = {}

        self._bias: Dict[str, str] = {c: "FLAT" for c in self._coins}
        self._last_bias_read = 0.0

        self._bbp: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._sma: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._above_trend: Dict[str, Optional[bool]] = {c: None for c in self._coins}
        self._last_signal_check: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._position_side: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._entry_price: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._amount: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._entry_time: Dict[str, Optional[float]] = {c: None for c in self._coins}
        # After a 90% TP: wait until price bounces recover_roe from this exit px
        self._wait_exit_px: Dict[str, Optional[float]] = {c: None for c in self._coins}
        # Bounce wait stays on the past exit's leverage, not the desk tier.
        self._wait_lev: Dict[str, float] = {c: float(self._leverage_cap(c)) for c in self._coins}
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
        self._load_cooldown_from_status()

        for coin in self._coins:
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=_pair_for(coin),
                interval=self.config.interval,
                max_records=self.config.candle_records,
            ))
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=_pair_for(coin),
                interval=self.config.trend_interval,
                max_records=self.config.trend_sma_length + 10,
            ))

        sub = _subaccount() or "UNSET"
        self.logger().info(
            f"Shorty init | sub={sub[:12]}… coins={','.join(self._coins)} "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"BBP>={config.bb_short_threshold} TP={config.tp_roe_pct}% "
            f"wait bounce {config.recover_roe_pct}% ROE  SL={config.sl_roe_pct}%"
        )

    def _load_cooldown_from_status(self):
        try:
            with open(STATUS_FILE, "r") as f:
                data = json.load(f) or {}
            cd = data.get("cooldown") or {}
            for coin in self._coins:
                row = cd.get(coin) or {}
                px = _as_float(row.get("exit_px"))
                if px and px > 0:
                    self._wait_exit_px[coin] = px
                    self._wait_lev[coin] = float(row.get("lev") or self._leverage_cap(coin))
        except Exception:
            pass

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
            self._update_signal(coin)
            if self._position_side[coin] in ("LONG", "SHORT"):
                self._check_exits(coin)
            else:
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
                self.logger().info(
                    f"Shorty ready | {self.config.connector_name} sub={_subaccount() or 'UNSET'} bias={self._bias}"
                )
                self._restore_positions_from_exchange(force=True)
        except Exception as e:
            self.logger().warning(f"Not ready yet: {e}")

    def _fetch_hl_max_leverage(self):
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
        except Exception as e:
            self.logger().warning(f"[leverage] Could not fetch HL max leverage: {e}")

    def _leverage_cap(self, coin: str) -> int:
        """Exchange max. Shorty recovery ROE does not follow the desk tier."""
        by_coin = self.config.leverage_by_coin or {}
        cfg = int(by_coin.get(coin) or self.config.leverage or 40)
        hl = int(self._hl_max_leverage.get(coin) or 0)
        use_max = bool(getattr(self.config, "use_exchange_max_leverage", True))
        try:
            from leverage_tier import sized
            return sized(cfg, hl, use_max, "MAX")
        except Exception:
            if use_max and hl > 0:
                return hl
            if hl > 0:
                return max(1, min(cfg, hl))
            return max(1, cfg)

    def _leverage_tier(self) -> str:
        try:
            from leverage_tier import tier_name
            return tier_name(BOT_ID)
        except Exception:
            return "MAX"

    def _leverage_for(self, coin: str) -> int:
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
                trading_pair=_pair_for(coin), leverage=lev,
            )
            self._leverage_set[coin] = True
            self._leverage_used[coin] = lev
            self.logger().info(f"leverage {coin} {self._leverage_tier()} {lev}x")
        except Exception as e:
            self.logger().warning(f"set_leverage {coin} failed (retry): {e}")

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
                self._close_position(coin, "dashboard force close", arm_wait=False)
            else:
                self.logger().info(f"Force close {coin} requested but flat")
        self._sql_done(cmd)

    def _run_force_open(self, cmd):
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        coin = str(data.get("coin") or "").upper()
        if coin not in self._coins:
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
            self._sql_done(cmd, "invalid pct")
            return
        if self._position_side[coin] is not None:
            self.logger().info(f"Force open {coin} skipped — already {self._position_side[coin]}")
            self._sql_done(cmd, "already open")
            return
        side = str(data.get("side") or self._bias.get(coin) or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(f"Force open {coin} skipped — set LONG or SHORT first")
            self._sql_done(cmd, "need long/short")
            return
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        self._wait_exit_px[coin] = None
        self.logger().info(f"Dashboard FORCE OPEN {side} {coin} at {pct*100:.0f}% leftover")
        self._open_now(coin, Decimal(str(pct)), reason=f"dashboard {pct*100:.0f}%", side=side)
        self._sql_done(cmd)

    def _get_mid(self, coin: str) -> Optional[Decimal]:
        try:
            p = self.connectors[self.config.connector_name].get_mid_price(_pair_for(coin))
            return Decimal(str(p)) if p else None
        except Exception:
            return None

    def _get_free_usd(self) -> Decimal:
        raw = Decimal("0")
        conn = self.connectors[self.config.connector_name]
        for asset in ("USD", "USDC", "USDT"):
            try:
                bal = conn.get_available_balance(asset)
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

    def _get_equity_usd(self) -> Decimal:
        conn = self.connectors[self.config.connector_name]
        for asset in ("USD", "USDC", "USDT"):
            try:
                bal = conn.get_balance(asset)
                if bal and Decimal(str(bal)) > 0:
                    return Decimal(str(bal))
            except Exception:
                continue
        return self._get_free_usd()

    def _get_spot_usdc(self) -> Decimal:
        addr = _subaccount()
        if not addr:
            return Decimal("0")
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({"type": "spotClearinghouseState", "user": addr}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
            for bal in (data or {}).get("balances") or []:
                if str(bal.get("coin") or "").upper() == "USDC":
                    return Decimal(str(bal.get("total") or "0"))
        except Exception:
            pass
        return Decimal("0")

    def _qty_step(self, coin: str) -> Decimal:
        if coin == "BTC":
            return Decimal("0.00001")
        if coin == "ETH":
            return Decimal("0.0001")
        if coin == "LTC":
            return Decimal("0.01")
        return Decimal("0.0001")

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
                max_records=max(self.config.bb_length + 5, 40),
            )
            if df is None or len(df) < self.config.bb_length + 2:
                return
            closes = [float(x) for x in df["close"].astype(float).values]
            price = closes[-1]
            nbb = int(self.config.bb_length)
            window = closes[-nbb:]
            sma = sum(window) / nbb
            var = sum((x - sma) ** 2 for x in window) / nbb
            std = var ** 0.5
            upper = sma + float(self.config.bb_std) * std
            lower = sma - float(self.config.bb_std) * std
            self._bbp[coin] = (price - lower) / (upper - lower) if upper != lower else 0.5

            df1h = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.trend_interval,
                max_records=self.config.trend_sma_length + 5,
            )
            if df1h is not None and len(df1h) >= self.config.trend_sma_length:
                h_closes = [float(x) for x in df1h["close"].astype(float).values[-self.config.trend_sma_length:]]
                self._sma[coin] = sum(h_closes) / float(self.config.trend_sma_length)
                self._above_trend[coin] = bool(price > self._sma[coin])
            else:
                self._above_trend[coin] = None
        except Exception as e:
            self.logger().warning(f"Signal error {coin}: {e}")

    def _short_signal(self, coin: str) -> bool:
        bbp = self._bbp[coin]
        if bbp is None:
            return False
        if bbp < float(_ctrl("bb_short_threshold", self.config.bb_short_threshold)):
            return False
        if self._above_trend[coin] is True:
            return False
        return True

    def _recovery_roe(self, coin: str, mid: Optional[Decimal] = None) -> Optional[float]:
        exit_px = self._wait_exit_px.get(coin)
        if not exit_px:
            return None
        mid = mid or self._get_mid(coin)
        if not mid or mid <= 0:
            return None
        lev = float(self._wait_lev.get(coin) or self._leverage_cap(coin))
        return (float(mid) - float(exit_px)) / float(exit_px) * lev * 100.0

    def _try_enter(self, coin: str):
        if self._bias.get(coin) != "SHORT":
            return
        if self._position_side[coin] is not None:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        if self.current_timestamp - self._last_global_entry < 15:
            return
        mid = self._get_mid(coin)
        rec = self._recovery_roe(coin, mid)
        need = float(_ctrl("recover_roe_pct", self.config.recover_roe_pct))
        if rec is not None and rec < need:
            return
        if rec is not None and rec >= need:
            self.logger().info(
                f"{coin} bounce {rec:.1f}% ROE ≥ {need:.0f}% — recovery done, shorts allowed"
            )
            self._wait_exit_px[coin] = None
        if not self._short_signal(coin):
            return
        pct = Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0)))
        self._open_now(coin, pct, reason="BB short", side="SHORT")

    def _open_now(self, coin: str, margin_pct: Decimal, reason: str, side: str = "SHORT"):
        side = str(side or "SHORT").upper()
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
                self.logger().info(f"Skip {coin} entry — below min notional / free")
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
        pair = _pair_for(coin)
        self._last_entry_attempt[coin] = self.current_timestamp
        self._last_global_entry = self.current_timestamp
        bbp = self._bbp[coin]
        bbp_s = f"{bbp:.3f}" if bbp is not None else "n/a"
        trend = self._above_trend[coin]
        self.logger().info(
            f"SHORTY ENTRY {side} {pair} | {reason} | {float(pct)*100:.0f}% leftover "
            f"BBP={bbp_s} trend={'up' if trend else 'down' if trend is False else '?'} "
            f"amount={amount} mid=${float(mid):.4f} lev={lev}x"
        )
        try:
            if side == "LONG":
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            else:
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            self._position_side[coin] = side
            self._entry_price[coin] = mid
            self._amount[coin] = amount
            self._entry_time[coin] = self.current_timestamp
            self._leverage_used[coin] = lev
            self._wait_exit_px[coin] = None
            self._log_trade({
                "action": "OPEN", "coin": coin, "side": side,
                "amount": _as_float(amount), "price": _as_float(mid),
                "leverage": int(lev), "reason": str(reason),
                "pnl_usd": None, "roe_pct": None,
            })
            self._write_status()
        except Exception as e:
            self.logger().error(f"Entry order failed {coin}: {e}")

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
            for coin in self._coins:
                if self._position_side[coin] is not None and not found[coin]:
                    et = self._entry_time[coin]
                    if et and (self.current_timestamp - et) < 20:
                        continue
                    self.logger().info(f"{coin} flat on exchange — clearing shortn state")
                    self._position_side[coin] = None
                    self._entry_price[coin] = None
                    self._amount[coin] = None
                    self._entry_time[coin] = None
        except Exception as e:
            self.logger().warning(f"Restore position failed: {e}")

    def _calc_roe_pct(self, coin: str, mid: Decimal) -> Optional[float]:
        entry_px = self._entry_price[coin]
        side = self._position_side[coin]
        if not entry_px or entry_px <= 0 or not mid or not side:
            return None
        entry = float(entry_px)
        px = float(mid)
        lev = float(self._leverage_used[coin] or self._leverage_for(coin))
        if side == "LONG":
            return (px - entry) / entry * lev * 100.0
        return (entry - px) / entry * lev * 100.0

    def _check_exits(self, coin: str):
        if self._is_close_pending(coin):
            return
        mid = self._get_mid(coin)
        if not mid or not self._entry_price[coin]:
            return
        et = self._entry_time.get(coin)
        age = (self.current_timestamp - et) if et else 999
        roe = self._calc_roe_pct(coin, mid)
        if roe is None:
            return
        if roe >= float(_ctrl("tp_roe_pct", self.config.tp_roe_pct)):
            self._close_position(coin, f"TP ROE {roe:.2f}% piss off", arm_wait=True)
            return
        sl = float(_live_sl_roe(self.config.sl_roe_pct))
        if age >= 5 and roe <= -sl:
            self._close_position(coin, f"SL ROE {roe:.2f}%", arm_wait=False)
            return

    def _close_position(self, coin: str, reason: str, arm_wait: bool = False):
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
        try:
            if side == "LONG":
                close_px = mid * Decimal("0.99") if mid else Decimal("0")
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, close_px, PositionAction.CLOSE)
            else:
                close_px = mid * Decimal("1.01") if mid else Decimal("0")
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, close_px, PositionAction.CLOSE)
            roe = self._calc_roe_pct(coin, mid) if mid else None
            self.logger().info(
                f"CLOSED {side} {amount} {pair} | {reason} | "
                f"{('ROE~'+format(roe,'.2f')+'%') if roe is not None else 'ROE=n/a'}"
            )
            entry = _as_float(self._entry_price[coin])
            exit_px = _as_float(mid)
            amt = _as_float(amount)
            pnl = None
            if entry and exit_px and amt:
                pnl = amt * (entry - exit_px) if side == "SHORT" else amt * (exit_px - entry)
            self._log_trade({
                "action": "CLOSE", "coin": coin, "side": side, "amount": amt,
                "price": exit_px, "entry_price": entry,
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
                "reason": str(reason),
                "pnl_usd": round(pnl, 4) if pnl is not None else None,
                "roe_pct": round(roe, 3) if roe is not None else None,
            })
            if arm_wait and exit_px:
                self._wait_exit_px[coin] = exit_px
                self._wait_lev[coin] = float(self._leverage_used[coin] or self._leverage_cap(coin))
                self.logger().info(
                    f"{coin} wait bounce ≥ {_ctrl('recover_roe_pct', self.config.recover_roe_pct)}% ROE from ${exit_px:.4f} "
                    f"before next short"
                )
        except Exception as e:
            self.logger().error(f"Close failed {coin}: {e}")
            return
        self._close_pending_until[coin] = self.current_timestamp + 45
        self._position_side[coin] = None
        self._entry_price[coin] = None
        self._amount[coin] = None
        self._entry_time[coin] = None
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        lines = [
            f"Shorty | sub={(_subaccount() or 'UNSET')[:12]}… | "
            f"perp=${float(self._get_equity_usd()):.2f} free=${float(free):.2f} "
            f"spot=${float(self._get_spot_usdc()):.2f}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            rec = self._recovery_roe(coin, mid)
            wait = (
                f"WAIT bounce {rec:.1f}%/{float(_ctrl('recover_roe_pct', self.config.recover_roe_pct)):.0f}%"
                if rec is not None else "ready"
            )
            bbp = self._bbp[coin]
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} BBP={bbp if bbp is None else f'{bbp:.3f}'} "
                f"trend={'up' if self._above_trend[coin] else 'down' if self._above_trend[coin] is False else '?'} "
                f"| pos={self._position_side[coin] or 'FLAT'} mid={mid} ROE="
                f"{f'{roe:.2f}%' if roe is not None else 'n/a'} {wait}"
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
                try:
                    p = float(t.get("pnl_usd"))
                except (TypeError, ValueError):
                    continue
                realized += p
                if p > 0:
                    wins += 1
                elif p < 0:
                    losses += 1
        return {
            "realized_pnl_usd": round(realized, 4),
            "n_wins": wins, "n_losses": losses,
            "n_opens": opens, "n_closes": closes,
        }

    def _log_trade(self, event: dict):
        data = self._read_trade_log()
        trades = list(data.get("trades") or [])
        rec = dict(event or {})
        rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        rec["id"] = f"{int(time.time()*1000)}-{rec.get('coin','')}-{rec.get('action','')}"
        trades.append(rec)
        trades = trades[-200:]
        summary = self._summarize_trades(trades)
        try:
            _atomic_json_write(TRADE_LOG_FILE, {"trades": trades, **summary})
        except Exception as e:
            self.logger().warning(f"trade log write failed: {e}")

    def _open_upnl_usd(self) -> float:
        total = 0.0
        for coin in self._coins:
            if self._position_side[coin] != "SHORT":
                continue
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid = _as_float(self._get_mid(coin))
            if not entry or not amt or not mid:
                continue
            total += amt * (entry - mid)
        return round(total, 4)

    def _write_status(self):
        try:
            from risk_park import maybe_send
            maybe_send(BOT_ID, self.connectors.get(self.config.connector_name))
        except Exception:
            pass
        positions = {}
        cooldown = {}
        for coin in self._coins:
            mid = self._get_mid(coin)
            side = self._position_side[coin]
            roe = self._calc_roe_pct(coin, mid) if mid and side else None
            rec = self._recovery_roe(coin, mid)
            if self._wait_exit_px.get(coin):
                cooldown[coin] = {
                    "exit_px": self._wait_exit_px[coin],
                    "lev": self._wait_lev.get(coin),
                    "bounce_roe": rec,
                    "need_roe": float(_ctrl("recover_roe_pct", self.config.recover_roe_pct)),
                }
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": _as_float(self._entry_price[coin]),
                "amount": _as_float(self._amount[coin]),
                "mid_price": _as_float(mid),
                "roe_pct": round(roe, 3) if roe is not None else None,
                "bbp": self._bbp.get(coin),
                "trend_up": self._above_trend.get(coin),
                "sma": self._sma.get(coin),
                "waiting_bounce": bool(self._wait_exit_px.get(coin)),
                "bounce_roe": round(rec, 2) if rec is not None else None,
                "need_bounce_roe": float(_ctrl("recover_roe_pct", self.config.recover_roe_pct)),
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
            }
        log = self._read_trade_log()
        summary = self._summarize_trades(list(log.get("trades") or []))
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "ready": self._trading_active,
            "connector": self.config.connector_name,
            "subaccount": _subaccount() or None,
            "coins": list(self._coins),
            "pairs": [_pair_for(c) for c in self._coins],
            "leverage": {c: self._leverage_for(c) for c in self._coins},
            "tp_roe_pct": float(_ctrl("tp_roe_pct", self.config.tp_roe_pct)),
            "sl_roe_pct": float(_live_sl_roe(self.config.sl_roe_pct)),
            "recover_roe_pct": float(_ctrl("recover_roe_pct", self.config.recover_roe_pct)),
            "bb_short_threshold": float(_ctrl("bb_short_threshold", self.config.bb_short_threshold)),
            "margin_pct": float(_ctrl("margin_pct", self.config.margin_pct or 1.0)),
            "equity_usd": _as_float(self._get_equity_usd()) or 0.0,
            "free_usd": _as_float(self._get_free_usd()) or 0.0,
            "perp_usd": _as_float(self._get_equity_usd()) or 0.0,
            "spot_usd": _as_float(self._get_spot_usdc()) or 0.0,
            "open_upnl_usd": self._open_upnl_usd(),
            "realized_pnl_usd": summary.get("realized_pnl_usd", 0.0),
            "n_wins": summary.get("n_wins", 0),
            "n_losses": summary.get("n_losses", 0),
            "n_opens": summary.get("n_opens", 0),
            "n_closes": summary.get("n_closes", 0),
            "bias": dict(self._bias),
            "cooldown": cooldown,
            "positions": positions,
        }
        try:
            _atomic_json_write(STATUS_FILE, payload)
            try:
                from matrix_store import try_status_put
                try_status_put(BOT_ID, payload)
            except Exception:
                pass
        except Exception as e:
            self.logger().warning(f"status write failed: {e}")
