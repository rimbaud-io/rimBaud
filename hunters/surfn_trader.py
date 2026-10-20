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
surfn_trader.py
===============
Hummingbot directional_trading.supertrend_v1 on the Surfn HL subaccount
 (id hummingbot-surfn).

Official SuperTrend V1 signal (length 20, multiplier 4, 3m, 1% distance):
- LONG  when SUPERTd == 1  and |close-ST|/close < percentage_threshold
- SHORT when SUPERTd == -1 and |close-ST|/close < percentage_threshold
BIAS still gates entries. An open clip is never flipped by an ST reverse.
Exit: Home SL / Close / 100% / scale-out. Same matrix rails as before.
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
            row = (json.load(f) or {}).get("hummingbot-surfn") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe("hummingbot-surfn", default))
    except Exception:
        return float(default)

try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default


def _ctrl(key, default):
    return _ctrl_live("hummingbot-surfn", key, default)

BOT_ID = "hummingbot-surfn"
BIAS_FILE = "/home/hummingbot/conf/surfn_bias.json"
STATUS_FILE = "/home/hummingbot/conf/surfn_status.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/surfn_trades.json"
MOON_COIN_PCT = 50.0

SURFN_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
DEFAULT_SUBACCOUNT = ""
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"


def _subaccount() -> str:
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        for bot in data.get("bots") or []:
            if bot.get("id") == "hummingbot-surfn":
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


def _ema(values, length: int) -> Optional[float]:
    if not values or len(values) < length:
        return None
    k = 2.0 / (length + 1)
    ema = sum(values[:length]) / float(length)
    for v in values[length:]:
        ema = float(v) * k + ema * (1.0 - k)
    return float(ema)


def _supertrend(highs, lows, closes, length: int = 20, mult: float = 4.0):
    """Official SuperTrend V1: (direction, line, dist_pct). direction 1=up, -1=down."""
    n = len(closes)
    if n < length + 3:
        return None, None, None
    trs = []
    for i in range(1, n):
        h, l, pc = float(highs[i]), float(lows[i]), float(closes[i - 1])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:length]) / float(length)
    direction = 1
    st = (float(highs[length]) + float(lows[length])) / 2.0
    for i in range(length, n):
        hl2 = (float(highs[i]) + float(lows[i])) / 2.0
        if i > length:
            atr = (atr * (length - 1) + trs[i - 1]) / float(length)
        upper = hl2 + mult * atr
        lower = hl2 - mult * atr
        prev = st
        close = float(closes[i])
        if direction == 1:
            st = max(lower, prev) if close > prev else upper
        else:
            st = min(upper, prev) if close < prev else lower
        if close > st:
            direction = 1
        elif close < st:
            direction = -1
    close = float(closes[-1])
    dist = abs(close - st) / close if close else None
    return direction, st, dist


class SurfnTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(SURFN_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    scale_roe_pct: float = Field(90.0)
    scale_out_pct: float = Field(0.50)
    sl_roe_pct: float = Field(20.0)

    candles_connector: str = Field("hyperliquid_perpetual")
    interval: str = Field("3m")
    st_length: int = Field(20)
    st_mult: float = Field(4.0)
    percentage_threshold: float = Field(0.01)
    candle_records: int = Field(80)

    signal_interval: int = Field(30)
    status_interval: int = Field(30)

    def surfn_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or SURFN_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in SURFN_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(SURFN_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.surfn_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class SurfnTrader(StrategyV2Base):
    """directional_trading.supertrend_v1 on the Surfn wallet. Never flips an open clip."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: SurfnTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.surfn_coins()

        self._bias: Dict[str, str] = {c: "FLAT" for c in self._coins}
        self._last_bias_read = 0.0
        self._moon: Dict[str, dict] = {c: {} for c in self._coins}
        self._last_moon_read = 0.0

        self._signal: Dict[str, dict] = {c: {} for c in self._coins}
        self._last_signal_check: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._position_side: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._entry_price: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._amount: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._entry_time: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._scaled_out: Dict[str, bool] = {c: False for c in self._coins}
        self._n_adds: Dict[str, int] = {c: 0 for c in self._coins}
        self._trail_px: Dict[str, Optional[float]] = {c: None for c in self._coins}

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

        recs = max(int(self.config.candle_records), int(self.config.st_length) + 20)
        for coin in self._coins:
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=_pair_for(coin),
                interval=self.config.interval,
                max_records=recs,
            ))

        self.logger().info(
            f"SuperTrend V1 init | subaccount={_subaccount()[:10]}… coins={','.join(self._coins)} "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"ST len={config.st_length} mult={config.st_mult} {config.interval} "
            f"thr={config.percentage_threshold} "
            f"scale={config.scale_out_pct*100:.0f}%@{config.scale_roe_pct}% ROE "
            f"SL={config.sl_roe_pct}% (new risk only) · ST reverse does not flatten"
        )

    def on_tick(self):
        self._read_bias()
        self._read_moon()
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
                self._try_add(coin)
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
                    f"SuperTrend V1 ready | {self.config.connector_name} sub={_subaccount()} bias={self._bias}"
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
            self.logger().info(f"[leverage] HL max: {self._hl_max_leverage or 'n/a'}")
        except Exception as e:
            self.logger().warning(f"[leverage] Could not fetch HL max leverage: {e}")

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

    def _moon_on(self, coin: str) -> bool:
        row = self._moon.get(coin) or {}
        return bool(row.get("on"))

    def _moon_target_px(self, coin: str):
        row = self._moon.get(coin) or {}
        try:
            tgt = float(row.get("target_px") or 0)
        except (TypeError, ValueError):
            tgt = 0.0
        return tgt if tgt > 0 else None

    def _persist_moon(self):
        payload = {}
        for c in self._coins:
            payload[c] = dict(self._moon.get(c) or {})
        try:
            from matrix_store import ensure, kv_set
            ensure()
            kv_set(BOT_ID, "moon", payload, "hunter")
        except Exception:
            pass

    def _fill_moon_anchor(self, coin: str):
        row = dict(self._moon.get(coin) or {})
        if not row.get("on"):
            return
        try:
            pct = float(row.get("coin_pct") or MOON_COIN_PCT)
        except (TypeError, ValueError):
            pct = MOON_COIN_PCT
        pct = max(1.0, min(500.0, pct))
        try:
            anchor = float(row.get("anchor_px") or 0)
        except (TypeError, ValueError):
            anchor = 0.0
        if anchor <= 0:
            mid = self._get_mid(coin)
            entry = self._entry_price.get(coin)
            if mid:
                anchor = float(mid)
            elif entry:
                anchor = float(entry)
        if anchor <= 0:
            return
        target = anchor * (1.0 + pct / 100.0)
        changed = (
            abs(float(row.get("anchor_px") or 0) - anchor) > 1e-9
            or abs(float(row.get("target_px") or 0) - target) > 1e-9
            or abs(float(row.get("coin_pct") or 0) - pct) > 1e-9
        )
        row["anchor_px"] = anchor
        row["target_px"] = target
        row["coin_pct"] = pct
        row["on"] = True
        self._moon[coin] = row
        if changed:
            self._persist_moon()
            self.logger().info(
                f"[moon] {coin} hold — sell only when coin +{pct:.0f}% "
                f"from ${anchor:.4f} → ${target:.4f}"
            )

    def _clear_moon(self, coin: str, reason: str = ""):
        row = dict(self._moon.get(coin) or {})
        if not row.get("on"):
            return
        row["on"] = False
        self._moon[coin] = row
        self._persist_moon()
        extra = f" ({reason})" if reason else ""
        self.logger().info(f"[moon] {coin} off{extra}")

    def _read_moon(self):
        now = time.time()
        if now - self._last_moon_read < 1.0:
            for c in self._coins:
                if self._moon_on(c):
                    self._fill_moon_anchor(c)
            return
        self._last_moon_read = now
        data = {}
        try:
            from matrix_store import ensure, kv_get
            ensure()
            sql_moon = kv_get(BOT_ID, "moon")
            if isinstance(sql_moon, dict):
                data = sql_moon
        except Exception:
            data = {}
        if not isinstance(data, dict):
            return
        for c in self._coins:
            raw = data.get(c)
            row = raw if isinstance(raw, dict) else {}
            on = bool(row.get("on"))
            prev = bool((self._moon.get(c) or {}).get("on"))
            self._moon[c] = dict(row)
            self._moon[c]["on"] = on
            if on and not prev:
                self.logger().info(f"[moon] {c} armed from dashboard")
            if on:
                self._fill_moon_anchor(c)

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
        connector = self.connectors.get(self.config.connector_name)
        if connector is None or not getattr(connector, "ready", False):
            return
        payload = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        try:
            from matrix_store import close_coin_list
            targets = close_coin_list(payload, self._coins)
        except Exception:
            targets = list(self._coins)
        self._restore_positions_from_exchange(force=True)
        self._seed_positions_from_status()
        exchange_pos = getattr(connector, "account_positions", None)
        if not any(self._position_side[c] for c in targets) and not exchange_pos:
            self.logger().info("Force close waiting — exchange positions not loaded yet")
            return
        for coin in targets:
            if self._position_side[coin] is not None:
                self.logger().info(f"Dashboard FORCE CLOSE {coin}")
                self._close_position(coin, "dashboard force close")
            else:
                self.logger().info(f"Force close {coin} requested but flat")
        self._sql_done(cmd)

    def _seed_positions_from_status(self):
        """If restore has not filled local state yet, use last status snapshot."""
        data = {}
        try:
            from matrix_store import ensure, status_get
            ensure()
            data = status_get(BOT_ID) or {}
        except Exception:
            data = {}
        if not data:
            try:
                if os.path.exists(STATUS_FILE):
                    with open(STATUS_FILE, "r") as f:
                        data = json.load(f) or {}
            except Exception:
                return
        pos = data.get("positions") if isinstance(data, dict) else None
        if not isinstance(pos, dict):
            return
        for coin in self._coins:
            if self._position_side[coin] is not None:
                continue
            row = pos.get(coin) if isinstance(pos.get(coin), dict) else {}
            side = str(row.get("position_side") or "").upper()
            amt = row.get("amount")
            entry = row.get("entry_price")
            if side != "LONG" or not amt:
                continue
            try:
                self._position_side[coin] = "LONG"
                self._amount[coin] = Decimal(str(amt))
                self._entry_price[coin] = Decimal(str(entry or 0)) or None
                if self._entry_time[coin] is None:
                    self._entry_time[coin] = self.current_timestamp
                self.logger().info(
                    f"Force close seed {coin} from status {self._amount[coin]} @ {self._entry_price[coin]}"
                )
            except Exception:
                continue

    def _run_force_open(self, cmd):
        if not self._trading_active:
            return
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
        side = str(data.get("side") or self._bias.get(coin) or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(f"Force open {coin} skipped — set LONG or SHORT first")
            self._sql_done(cmd, "need long/short")
            return
        open_side = self._position_side[coin]
        if open_side is not None and open_side != side:
            self.logger().info(f"Force open {coin} skipped — already {open_side}, will not flip")
            self._sql_done(cmd, "already open opposite")
            return
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        adding = open_side == side
        self.logger().info(
            f"Dashboard FORCE {'ADD' if adding else 'OPEN'} {side} {coin} at {pct*100:.0f}% leftover"
        )
        self._open_now(
            coin,
            Decimal(str(pct)),
            reason=f"dashboard {pct*100:.0f}%{' add' if adding else ''}",
            is_add=adding,
            side=side,
        )
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
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({
                    "type": "spotClearinghouseState",
                    "user": _subaccount(),
                }).encode(),
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
        length = int(_ctrl("st_length", self.config.st_length) or 20)
        mult = float(_ctrl("st_mult", self.config.st_mult) or 4.0)
        thr = float(_ctrl("percentage_threshold", self.config.percentage_threshold) or 0.01)
        if thr > 1:
            thr = thr / 100.0
        info = {
            "label": "n/a",
            "trend_up": False,
            "st_dir": None,
            "st_line": None,
            "st_dist": None,
            "long_ok": False,
            "short_ok": False,
        }
        try:
            recs = max(int(self.config.candle_records), length + 20)
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.interval,
                max_records=recs,
            )
            if df is None or len(df) < length + 3:
                info["label"] = f"need {self.config.interval} candles"
                self._signal[coin] = info
                return
            closes = [float(x) for x in df["close"].astype(float).values]
            lows = [float(x) for x in df["low"].astype(float).values]
            highs = [float(x) for x in df["high"].astype(float).values]
            direction, line, dist = _supertrend(highs, lows, closes, length=length, mult=mult)
            info["st_dir"] = direction
            info["st_line"] = round(line, 6) if line is not None else None
            info["st_dist"] = round(dist, 5) if dist is not None else None
            info["trend_up"] = direction == 1
            near = dist is not None and dist < thr
            if direction == 1 and near:
                info["long_ok"] = True
                info["label"] = f"ST V1 LONG dist={dist*100:.2f}%"
            elif direction == -1 and near:
                info["short_ok"] = True
                info["label"] = f"ST V1 SHORT dist={dist*100:.2f}%"
            elif direction == 1:
                info["label"] = f"ST up, far ({(dist or 0)*100:.2f}%)"
            elif direction == -1:
                info["label"] = f"ST down, far ({(dist or 0)*100:.2f}%)"
            else:
                info["label"] = "ST n/a"
            self._signal[coin] = info
        except Exception as e:
            self.logger().warning(f"Signal error {coin}: {e}")
            self._signal[coin] = info

    def _try_enter(self, coin: str):
        if self._position_side[coin] is not None:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        if self.current_timestamp - self._last_global_entry < 15:
            return
        bias = self._bias.get(coin)
        sig = self._signal.get(coin) or {}
        pct = Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0)))
        if bias == "LONG" and sig.get("long_ok"):
            self._open_now(coin, pct, reason=str(sig.get("label") or "supertrend_v1 long"), side="LONG")
        elif bias == "SHORT" and sig.get("short_ok"):
            self._open_now(coin, pct, reason=str(sig.get("label") or "supertrend_v1 short"), side="SHORT")

    def _try_add(self, coin: str):
        """After scale-out, add leftover if SuperTrend still agrees. Never flips side."""
        if not self._scaled_out.get(coin):
            return
        side = self._position_side[coin]
        if side not in ("LONG", "SHORT"):
            return
        if self._bias.get(coin) != side:
            return
        if self._n_adds.get(coin, 0) >= 2:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 45:
            return
        sig = self._signal.get(coin) or {}
        if side == "LONG" and not sig.get("long_ok"):
            return
        if side == "SHORT" and not sig.get("short_ok"):
            return
        mid = self._get_mid(coin)
        if not mid or mid <= 0:
            return
        trail = self._trail_px.get(coin)
        if side == "LONG" and trail and float(mid) <= float(trail):
            return
        if side == "SHORT" and trail and float(mid) >= float(trail):
            return
        self._open_now(
            coin,
            Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0))),
            reason="runner add",
            is_add=True,
            side=side,
        )

    def _open_now(self, coin: str, margin_pct: Decimal, reason: str, is_add: bool = False, side: str = "LONG"):
        side = str(side or "LONG").upper()
        if side not in ("LONG", "SHORT"):
            return
        if self._position_side[coin] is not None and not is_add:
            return
        if is_add and self._position_side[coin] != side:
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
                f"Skip {coin} {'add' if is_add else 'entry'} — margin ${float(margin):.2f} "
                f"< min ${float(self.config.min_margin_usd)}"
            )
            self._last_entry_attempt[coin] = self.current_timestamp
            return
        lev = self._leverage_for(coin)
        notional = margin * Decimal(str(lev))
        min_n = Decimal(str(self.config.min_notional_usd))
        if notional < min_n:
            needed_margin = (min_n / Decimal(str(lev))).quantize(Decimal("0.01"))
            if needed_margin > free * Decimal("0.80"):
                self.logger().info(f"Skip {coin} {'add' if is_add else 'entry'} — below min notional / free")
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
        sig = self._signal.get(coin) or {}
        self.logger().info(
            f"SURFN {'ADD' if is_add else 'ENTRY'} {side} {pair} | {reason} | "
            f"{float(pct)*100:.0f}% leftover ema={sig.get('ema')} bbp={sig.get('bbp')} "
            f"amount={amount} mid=${float(mid):.4f} lev={lev}x"
        )
        try:
            if side == "SHORT":
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            else:
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            if is_add and self._amount[coin] and self._entry_price[coin]:
                old_amt = Decimal(str(self._amount[coin]))
                old_px = Decimal(str(self._entry_price[coin]))
                new_amt = old_amt + amount
                blend = (old_px * old_amt + mid * amount) / new_amt
                self._amount[coin] = new_amt
                self._entry_price[coin] = blend
                self._n_adds[coin] = int(self._n_adds.get(coin) or 0) + 1
            else:
                self._position_side[coin] = side
                self._entry_price[coin] = mid
                self._amount[coin] = amount
                self._entry_time[coin] = self.current_timestamp
                self._scaled_out[coin] = False
                self._n_adds[coin] = 0
                self._trail_px[coin] = None
            self._leverage_used[coin] = lev
            self._log_trade({
                "action": "ADD" if is_add else "OPEN",
                "coin": coin, "side": side,
                "amount": _as_float(amount), "price": _as_float(mid),
                "leverage": int(lev), "reason": str(reason),
                "pnl_usd": None, "roe_pct": None,
            })
            self._write_status()
        except Exception as e:
            self.logger().error(f"{'Add' if is_add else 'Entry'} order failed {coin}: {e}")

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
                    self.logger().info(f"{coin} flat on exchange — clearing surfn state")
                    self._clear_pos(coin)
        except Exception as e:
            self.logger().warning(f"Restore position failed: {e}")

    def _clear_pos(self, coin: str):
        self._position_side[coin] = None
        self._entry_price[coin] = None
        self._amount[coin] = None
        self._entry_time[coin] = None
        self._scaled_out[coin] = False
        self._n_adds[coin] = 0
        self._trail_px[coin] = None

    def _calc_roe_pct(self, coin: str, mid: Decimal) -> Optional[float]:
        entry_px = self._entry_price[coin]
        side = self._position_side[coin]
        if not entry_px or entry_px <= 0 or not mid or not side:
            return None
        entry = float(entry_px)
        px = float(mid)
        lev = float(self._leverage_used[coin] or self._leverage_for(coin))
        if side == "SHORT":
            return (entry - px) / entry * lev * 100.0
        return ((px - entry) / entry) * lev * 100.0

    def _update_trail(self, coin: str, mid: Decimal):
        sig = self._signal.get(coin) or {}
        entry = float(self._entry_price[coin] or 0)
        st = sig.get("st_line")
        be = entry * 1.0005 if entry else 0
        trail = be
        if st:
            trail = max(trail, float(st) * 0.999)
        prev = self._trail_px.get(coin)
        if prev:
            trail = max(float(prev), trail)
        self._trail_px[coin] = trail

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

        side = self._position_side[coin]
        if side == "SHORT":
            sl = float(_live_sl_roe(self.config.sl_roe_pct))
            if age >= 5 and roe <= -sl:
                self._close_position(coin, f"SL ROE {roe:.2f}%")
                return
            tp = float(_ctrl("tp_roe_pct", getattr(self.config, "tp_roe_pct", 20.0) or 20.0))
            if age >= 5 and roe >= tp:
                self._close_position(coin, f"TP ROE {roe:.2f}%")
            return

        if self._moon_on(coin):
            self._fill_moon_anchor(coin)
            target = self._moon_target_px(coin)
            if target and age >= 5 and float(mid) >= float(target):
                pct = float((self._moon.get(coin) or {}).get("coin_pct") or MOON_COIN_PCT)
                self._close_position(
                    coin,
                    f"MOON coin +{pct:.0f}% @ {float(mid):.4f} (target {target:.4f})",
                )
                self._clear_moon(coin, "target hit")
            return

        # New-risk stop: only before the first scale-out
        sl = float(_live_sl_roe(self.config.sl_roe_pct))
        if not self._scaled_out[coin] and age >= 5 and roe <= -sl:
            self._close_position(coin, f"SL ROE {roe:.2f}%")
            return

        # Clip 50% at ~90% ROE, keep the runner
        if not self._scaled_out[coin] and roe >= float(_ctrl("scale_roe_pct", self.config.scale_roe_pct)):
            if self._scale_out(coin, roe):
                return

        if self._scaled_out[coin]:
            self._update_trail(coin, mid)
            trail = self._trail_px.get(coin)
            if trail and float(mid) <= float(trail) and age >= 8:
                self._close_position(coin, f"runner trail {trail:.4f}")
                return

    def _scale_out(self, coin: str, roe: float) -> bool:
        amount = self._amount[coin]
        mid = self._get_mid(coin) or self._entry_price[coin]
        if not amount or not mid:
            return False
        frac = Decimal(str(_ctrl("scale_out_pct", self.config.scale_out_pct or 0.5)))
        sell_amt = self._quantize_amount(coin, amount * frac)
        remain = amount - sell_amt
        min_n = Decimal(str(self.config.min_notional_usd))
        if sell_amt <= 0 or remain * mid < min_n:
            self.logger().info(f"{coin} skip scale-out — leftover notional would be < ${float(min_n)}")
            return False
        pair = _pair_for(coin)
        close_px = mid * Decimal("0.99")
        try:
            self.sell(self.config.connector_name, pair, sell_amt, OrderType.MARKET, close_px, PositionAction.CLOSE)
            entry = _as_float(self._entry_price[coin])
            exit_px = _as_float(mid)
            pnl = None
            if entry and exit_px:
                pnl = float(sell_amt) * (exit_px - entry)
            self.logger().info(
                f"SCALE-OUT LONG {sell_amt}/{amount} {pair} @ {float(mid):.4f} "
                f"ROE={roe:.2f}% keep {remain} as runner"
            )
            self._log_trade({
                "action": "SCALE", "coin": coin, "side": "LONG",
                "amount": _as_float(sell_amt), "price": exit_px, "entry_price": entry,
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
                "reason": f"scale {float(frac)*100:.0f}% @ {roe:.1f}% ROE",
                "pnl_usd": round(pnl, 4) if pnl is not None else None,
                "roe_pct": round(roe, 3),
            })
            self._amount[coin] = remain
            self._scaled_out[coin] = True
            if entry:
                self._trail_px[coin] = float(entry) * 1.0005
            self._close_pending_until[coin] = self.current_timestamp + 20
            self._write_status()
            return True
        except Exception as e:
            self.logger().error(f"Scale-out failed {coin}: {e}")
            return False

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
        try:
            if side == "SHORT":
                close_px = mid * Decimal("1.01") if mid else Decimal("0")
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, close_px, PositionAction.CLOSE)
            else:
                close_px = mid * Decimal("0.99") if mid else Decimal("0")
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, close_px, PositionAction.CLOSE)
            roe = self._calc_roe_pct(coin, mid) if mid else None
            roe_s = f"ROE~{roe:.2f}%" if roe is not None else "ROE=n/a"
            self.logger().info(f"CLOSED {side} {amount} {pair} | reason={reason} | {roe_s}")
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
        except Exception as e:
            self.logger().error(f"Close failed {coin}: {e}")
            return
        self._close_pending_until[coin] = self.current_timestamp + 45
        self._clear_pos(coin)
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        lines = [
            f"SuperTrend V1 | sub={_subaccount()[:10]}… | "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"perp=${float(self._get_equity_usd()):.2f} free=${float(free):.2f} "
            f"spot=${float(self._get_spot_usdc()):.2f}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            s = self._signal.get(coin) or {}
            roe_s = f"{roe:.2f}%" if roe is not None else "n/a"
            mode = "runner" if self._scaled_out.get(coin) else (self._position_side[coin] or "FLAT")
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} sig={s.get('label')} "
                f"st={s.get('st_dir')} dist={s.get('st_dist')} "
                f"| pos={mode} entry={self._entry_price[coin]} mid={mid} ROE={roe_s} "
                f"trail={self._trail_px.get(coin)}"
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
            if action in ("OPEN", "ADD"):
                opens += 1
            elif action in ("CLOSE", "SCALE"):
                if action == "CLOSE":
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
        try:
            _atomic_json_write(TRADE_LOG_FILE, {"trades": trades, **summary})
        except Exception as e:
            self.logger().warning(f"trade log write failed: {e}")

    def _open_upnl_usd(self) -> float:
        total = 0.0
        for coin in self._coins:
            if self._position_side[coin] != "LONG":
                continue
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid = _as_float(self._get_mid(coin))
            if not entry or not amt or not mid:
                continue
            total += amt * (mid - entry)
        return round(total, 4)

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
            s = self._signal.get(coin) or {}
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": _as_float(self._entry_price[coin]),
                "amount": _as_float(self._amount[coin]),
                "mid_price": _as_float(mid),
                "roe_pct": round(roe, 3) if roe is not None else None,
                "scaled_out": bool(self._scaled_out.get(coin)),
                "n_adds": int(self._n_adds.get(coin) or 0),
                "moon": self._moon_on(coin),
                "moon_pct": float((self._moon.get(coin) or {}).get("coin_pct") or MOON_COIN_PCT) if self._moon_on(coin) else None,
                "moon_anchor_px": _as_float((self._moon.get(coin) or {}).get("anchor_px")) if self._moon_on(coin) else None,
                "moon_target_px": self._moon_target_px(coin),
                "trail_px": self._trail_px.get(coin),
                "signal": s.get("label"),
                "trend_up": bool(s.get("trend_up")),
                "st_dir": s.get("st_dir"),
                "st_line": s.get("st_line"),
                "st_dist": s.get("st_dist"),
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
            }
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
            "scale_roe_pct": float(_ctrl("scale_roe_pct", self.config.scale_roe_pct)),
            "scale_out_pct": float(_ctrl("scale_out_pct", self.config.scale_out_pct)),
            "st_length": int(_ctrl("st_length", self.config.st_length)),
            "st_mult": float(_ctrl("st_mult", self.config.st_mult)),
            "percentage_threshold": float(_ctrl("percentage_threshold", self.config.percentage_threshold)),
            "controller": "directional_trading.supertrend_v1",
            "margin_pct": float(_ctrl("margin_pct", self.config.margin_pct or 1.0)),
            "sl_roe_pct": float(_live_sl_roe(self.config.sl_roe_pct)),
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
            "positions": positions,
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
