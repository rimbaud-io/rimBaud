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
onlyup_trader.py
================
Supertrend rider ("Only Up") for BTC, LTC, TAO.

- BTC-USD, LTC-USD, TAO-USD. Auto-entry stays LONG-only.
- 100% leftover follows current BIAS (LONG or SHORT). Never flips an open clip.
- 1h SMA-50 must be up.
- 15m Supertrend must be long, and price not extended (>1.2% from the line)
  so we do not chase.
- At ~90% ROE: sell 50%, keep a runner, trail under Supertrend / EMA-21.
- Supertrend flip down closes the runner.
- Home SL ROE (bot_sl.json) on new risk before first scale-out. Yaml default 20% if unset.

Dashboard: LONG/SHORT/FLAT + 100% leftover follows current BIAS.
Moon works on LONG (sell when price is up) and SHORT (cover when price is down).
Does not trade until Confiig has a unique subaccount.
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
            row = (json.load(f) or {}).get("hummingbot-onlyup") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe("hummingbot-onlyup", default))
    except Exception:
        return float(default)

try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default


def _ctrl(key, default):
    return _ctrl_live("hummingbot-onlyup", key, default)

BOT_ID = "hummingbot-onlyup"
BIAS_FILE = "/home/hummingbot/conf/onlyup_bias.json"
STATUS_FILE = "/home/hummingbot/conf/onlyup_status.json"
MOON_FILE = "/home/hummingbot/conf/onlyup_moon.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/onlyup_trades.json"
MOON_COIN_PCT = 50.0

ONLYUP_COINS = ("BTC", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
DEFAULT_SUBACCOUNT = ""
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"
SELF_ID = "hummingbot-onlyup"


def _subaccount() -> str:
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        for bot in data.get("bots") or []:
            if bot.get("id") == SELF_ID:
                addr = str(bot.get("address") or "").strip()
                if addr.lower().startswith("0x") and len(addr) == 42:
                    return addr
    except Exception:
        pass
    return DEFAULT_SUBACCOUNT


def _occupied_wallets() -> set:
    occupied = set()
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        for bot in data.get("bots") or []:
            if bot.get("id") == SELF_ID:
                continue
            addr = str(bot.get("address") or "").strip().lower()
            if addr.startswith("0x") and len(addr) == 42:
                occupied.add(addr)
    except Exception:
        pass
    return occupied


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


def _supertrend(highs, lows, closes, length: int = 10, mult: float = 3.0):
    """Return (direction, line, dist_pct) for the last bar. direction 1=up, -1=down."""
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


class OnlyupTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(ONLYUP_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    scale_roe_pct: float = Field(90.0)
    scale_out_pct: float = Field(0.50)
    sl_roe_pct: float = Field(20.0)

    candles_connector: str = Field("hyperliquid_perpetual")
    interval: str = Field("15m")
    ema_length: int = Field(21)
    bb_length: int = Field(20)
    bb_std: float = Field(2.0)
    bbp_min: float = Field(0.35)
    bbp_max: float = Field(0.52)
    trend_interval: str = Field("1h")
    trend_sma_length: int = Field(50)
    candle_records: int = Field(120)
    st_length: int = Field(10)
    st_mult: float = Field(3.0)
    st_max_dist: float = Field(0.012)

    signal_interval: int = Field(30)
    status_interval: int = Field(30)

    def onlyup_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or ONLYUP_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in ONLYUP_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(ONLYUP_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.onlyup_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class OnlyupTrader(StrategyV2Base):
    """BTC + LTC Supertrend long rider. Never shorts."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: OnlyupTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.onlyup_coins()
        self._wallet_ok_cached = False

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
        self._last_closed: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._trading_active = False
        self._last_status_log = 0.0
        self._last_global_entry = 0.0
        self._fetch_hl_max_leverage()
        self._leverage_used = {c: self._leverage_for(c) for c in self._coins}
        self._lev_tier_seen = self._leverage_tier()

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
                max_records=self.config.trend_sma_length + 20,
            ))

        self.logger().info(
            f"Only Up init | sub={(_subaccount() or 'UNSET')[:12]}… coins={','.join(self._coins)} "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"ST{config.st_length}x{config.st_mult} dist<{config.st_max_dist*100:.1f}% "
            f"1hSMA{config.trend_sma_length} scale={config.scale_out_pct*100:.0f}%@"
            f"{config.scale_roe_pct}% ROE SL={config.sl_roe_pct}%"
        )

    def _wallet_allowed(self) -> bool:
        addr = (_subaccount() or "").strip().lower()
        ok = bool(addr.startswith("0x") and len(addr) == 42 and addr not in _occupied_wallets())
        self._wallet_ok_cached = ok
        return ok

    def on_tick(self):
        self._read_bias()
        self._read_moon()
        if not self._trading_active:
            self._check_ready()
            self._wallet_allowed()
            self._write_status()
            return
        if not self._wallet_allowed():
            now = self.current_timestamp
            if now - self._last_status_log >= self.config.status_interval:
                self._last_status_log = now
                self.logger().warning(
                    "Only Up blocked — set a unique subaccount in Confiig"
                )
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
                if self._position_side[coin] == "LONG":
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
                    f"Only Up ready | {self.config.connector_name} "
                    f"sub={_subaccount() or 'UNSET'} wallet_ok={self._wallet_allowed()} bias={self._bias}"
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
        fallback = 10 if coin == "LTC" else 40
        cfg = int(by_coin.get(coin) or fallback)
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

    def _moon_pos_side(self, coin: str) -> str:
        s = str(self._position_side.get(coin) or "").upper()
        if s in ("LONG", "SHORT"):
            return s
        b = str(self._bias.get(coin) or "").upper()
        return b if b in ("LONG", "SHORT") else "LONG"

    def _moon_target_px(self, coin: str) -> Optional[float]:
        row = self._moon.get(coin) or {}
        if not row.get("on"):
            return None
        try:
            manual = float(row.get("close_px") or 0)
        except (TypeError, ValueError):
            manual = 0.0
        if manual > 0:
            return manual
        try:
            px = float(row.get("target_px") or 0)
        except (TypeError, ValueError):
            px = 0.0
        return px if px > 0 else None

    def _persist_moon(self):
        payload = {}
        for c in self._coins:
            row = dict(self._moon.get(c) or {})
            if row:
                payload[c] = row
        payload["updated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        try:
            _atomic_json_write(MOON_FILE, payload)
        except Exception as e:
            self.logger().warning(f"moon write failed: {e}")
        try:
            from matrix_store import ensure, kv_set
            ensure()
            coins = {k: v for k, v in payload.items() if k in self._coins and isinstance(v, dict)}
            kv_set("hummingbot-onlyup", "moon", coins, "hunter")
        except Exception:
            pass

    def _fill_moon_anchor(self, coin: str):
        row = dict(self._moon.get(coin) or {})
        if not row.get("on"):
            return
        try:
            manual = float(row.get("close_px") or 0)
        except (TypeError, ValueError):
            manual = 0.0
        side = self._moon_pos_side(coin)
        verb = "cover" if side == "SHORT" else "sell"
        if manual > 0:
            if abs(float(row.get("target_px") or 0) - manual) > 1e-9:
                row["target_px"] = manual
                row["on"] = True
                self._moon[coin] = row
                self._persist_moon()
                self.logger().info(
                    f"[moon] {coin} {side} hold — {verb} at ${manual:.4f} (manual close $)"
                )
            else:
                self._moon[coin] = row
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
        if side == "SHORT":
            target = anchor * (1.0 - pct / 100.0)
            if target <= 0:
                target = anchor * 0.01
        else:
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
            move = f"-{pct:.0f}%" if side == "SHORT" else f"+{pct:.0f}%"
            self.logger().info(
                f"[moon] {coin} {side} hold — {verb} only when coin {move} "
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
            if os.path.exists(MOON_FILE):
                with open(MOON_FILE, "r") as f:
                    data = json.load(f) or {}
        except Exception as e:
            self.logger().warning(f"moon read error: {e}")
            data = {}
        try:
            from matrix_store import ensure, kv_get
            ensure()
            sql_moon = kv_get("hummingbot-onlyup", "moon")
            if isinstance(sql_moon, dict):
                data = dict(data or {})
                data.update(sql_moon)
        except Exception:
            pass
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
        if not self._wallet_allowed():
            self.logger().warning("Force close blocked — unique subaccount not set")
            return
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
        if not self._wallet_allowed():
            self.logger().warning("Force open blocked — unique subaccount not set")
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
        already = self._position_side[coin]
        if already:
            self.logger().info(f"Force open {coin} skipped — already {already}")
            self._sql_done(cmd, "already open")
            return
        side = str(data.get("side") or self._bias.get(coin) or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(f"Force open {coin} skipped — set LONG or SHORT first")
            self._sql_done(cmd, "need long/short")
            return
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        self.logger().info(
            f"Dashboard FORCE OPEN {side} {coin} at {pct*100:.0f}% leftover"
        )
        self._open_now(
            coin,
            Decimal(str(pct)),
            reason=f"dashboard {pct*100:.0f}% leftover",
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
        info = {
            "label": "n/a",
            "trend_up": False,
            "ema": None,
            "bbp": None,
            "sma": None,
            "hour_low": None,
            "long_ok": False,
        }
        try:
            df15 = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.interval,
                max_records=self.config.candle_records,
            )
            df1h = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.trend_interval,
                max_records=self.config.trend_sma_length + 10,
            )
            if df15 is None or len(df15) < max(self.config.ema_length + 5, self.config.bb_length + 2):
                info["label"] = "need 15m candles"
                self._signal[coin] = info
                return
            closes = [float(x) for x in df15["close"].astype(float).values]
            lows = [float(x) for x in df15["low"].astype(float).values]
            highs = [float(x) for x in df15["high"].astype(float).values]
            price = closes[-1]
            ema = _ema(closes, int(self.config.ema_length))
            info["ema"] = round(ema, 6) if ema else None

            nbb = int(self.config.bb_length)
            window = closes[-nbb:]
            sma_bb = sum(window) / nbb
            var = sum((x - sma_bb) ** 2 for x in window) / nbb
            std = var ** 0.5
            upper = sma_bb + float(self.config.bb_std) * std
            lower = sma_bb - float(self.config.bb_std) * std
            bbp = (price - lower) / (upper - lower) if upper != lower else 0.5
            info["bbp"] = round(bbp, 3)

            trend_up = False
            hour_low = None
            if df1h is not None and len(df1h) >= int(self.config.trend_sma_length):
                h_closes = [float(x) for x in df1h["close"].astype(float).values]
                h_lows = [float(x) for x in df1h["low"].astype(float).values]
                sma = sum(h_closes[-int(self.config.trend_sma_length):]) / float(self.config.trend_sma_length)
                info["sma"] = round(sma, 6)
                trend_up = price > sma
                hour_low = min(h_lows[-8:]) if len(h_lows) >= 8 else min(h_lows)
                info["hour_low"] = hour_low
            info["trend_up"] = bool(trend_up)

            st_dir, st_line, st_dist = _supertrend(
                highs, lows, closes,
                int(self.config.st_length), float(self.config.st_mult),
            )
            info["st_dir"] = st_dir
            info["st_line"] = round(st_line, 2) if st_line else None
            info["st_dist"] = round(st_dist * 100.0, 3) if st_dist is not None else None
            near_st = st_dist is not None and st_dist <= float(_ctrl("st_max_dist", self.config.st_max_dist))

            if not trend_up:
                info["label"] = "1h trend down — no longs"
            elif st_dir != 1:
                info["label"] = "Supertrend down — no longs"
            elif not near_st:
                info["label"] = "uptrend but extended — wait pullback"
            else:
                info["label"] = "Supertrend long + near line"
                info["long_ok"] = True
            self._signal[coin] = info
        except Exception as e:
            self.logger().warning(f"Signal error {coin}: {e}")
            self._signal[coin] = info

    def _try_enter(self, coin: str):
        if self._bias.get(coin) != "LONG":
            return
        if self._position_side[coin] is not None:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        if self.current_timestamp - self._last_global_entry < 15:
            return
        sig = self._signal.get(coin) or {}
        if not sig.get("long_ok"):
            return
        pct = Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0)))
        self._open_now(coin, pct, reason=str(sig.get("label") or "supertrend long"), side="LONG")

    def _try_add(self, coin: str):
        """After scale-out, put freed margin back in on the next shallow dip."""
        if not self._scaled_out.get(coin):
            return
        if self._bias.get(coin) != "LONG":
            return
        if self._n_adds.get(coin, 0) >= 2:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 45:
            return
        sig = self._signal.get(coin) or {}
        if not sig.get("long_ok") or not sig.get("trend_up"):
            return
        mid = self._get_mid(coin)
        if not mid or mid <= 0:
            return
        # Don't add into a falling runner
        trail = self._trail_px.get(coin)
        if trail and float(mid) <= float(trail):
            return
        self._open_now(coin, Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0))), reason="runner add", is_add=True, side="LONG")

    def _open_now(self, coin: str, margin_pct: Decimal, reason: str, is_add: bool = False, side: str = "LONG"):
        side = str(side or "LONG").upper()
        if side not in ("LONG", "SHORT"):
            return
        if self._position_side[coin] is not None and not is_add:
            return
        if is_add and (side != "LONG" or self._position_side[coin] != "LONG"):
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
            f"ONLYUP {'ADD' if is_add else 'ENTRY'} {side} {pair} | {reason} | "
            f"{float(pct)*100:.0f}% leftover ST={sig.get('st_dir')} dist={sig.get('st_dist')} "
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

    def _closed_after_open(self, coin: str) -> bool:
        """True if the latest logged action for coin is CLOSE (manual or bot)."""
        try:
            log = self._read_trade_log()
            last = None
            for t in list(log.get("trades") or []):
                if str(t.get("coin") or "").upper() != coin:
                    continue
                act = str(t.get("action") or "").upper()
                if act in ("OPEN", "CLOSE"):
                    last = act
            return last == "CLOSE"
        except Exception:
            return False

    def _restore_positions_from_exchange(self, force: bool = False):
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
                    if self.current_timestamp - self._last_closed.get(coin, 0) < 300:
                        continue
                    if self._closed_after_open(coin):
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
                    self.logger().info(f"{coin} flat on exchange — clearing onlyup state")
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
        ema = sig.get("ema")
        be = entry * 1.0005 if entry else 0
        trail = be
        if ema:
            trail = max(trail, float(ema) * 0.997)
        st_line = sig.get("st_line")
        if st_line:
            trail = max(trail, float(st_line) * 0.999)
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
        # Moon bag: ignore ROE SL / clip / Supertrend.
        # LONG: sell when mid >= target (price up). SHORT: cover when mid <= target (price down).
        # A SHORT target above the market is ignored so Close $120 cannot flatten a $72 short.
        if self._moon_on(coin):
            self._fill_moon_anchor(coin)
            target = self._moon_target_px(coin)
            if target and age >= 5:
                mid_f = float(mid)
                tgt = float(target)
                if side == "SHORT":
                    hit = tgt > 0 and tgt <= mid_f * 1.001 and mid_f <= tgt
                else:
                    hit = tgt > 0 and mid_f >= tgt
                if hit:
                    row = self._moon.get(coin) or {}
                    try:
                        manual = float(row.get("close_px") or 0)
                    except (TypeError, ValueError):
                        manual = 0.0
                    verb = "cover" if side == "SHORT" else "sell"
                    move = float(row.get("coin_pct") or MOON_COIN_PCT)
                    sign = "-" if side == "SHORT" else "+"
                    why = (
                        f"manual {verb} ${tgt:.4f} @ {mid_f:.4f}"
                        if manual > 0
                        else (
                            f"MOON {side} coin {sign}{move:.0f}% "
                            f"@ {mid_f:.4f} (target {tgt:.4f})"
                        )
                    )
                    self._close_position(coin, why)
                    self._clear_moon(coin, "target hit")
            return

        if side == "SHORT":
            sl = float(_live_sl_roe(self.config.sl_roe_pct))
            if age >= 5 and roe <= -sl:
                self._close_position(coin, f"SL ROE {roe:.2f}%")
                return
            tp = float(_ctrl("tp_roe_pct", getattr(self.config, "tp_roe_pct", 20.0) or 20.0))
            if age >= 5 and roe >= tp:
                self._close_position(coin, f"TP ROE {roe:.2f}%")
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
            sig = self._signal.get(coin) or {}
            if sig.get("st_dir") == -1 and age >= 8:
                self._close_position(coin, "Supertrend flip down")
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
        if not self._wallet_allowed():
            self.logger().warning(f"Close blocked {coin} ({reason}) — unique subaccount not set")
            return
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
        self._close_pending_until[coin] = self.current_timestamp + 120
        self._last_closed[coin] = self.current_timestamp
        self._clear_pos(coin)
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        lines = [
            f"Only Up | sub={(_subaccount() or 'UNSET')[:12]}… ok={self._wallet_ok_cached} | "
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
                f"ST={s.get('st_dir')} dist={s.get('st_dist')} trend={'up' if s.get('trend_up') else 'down'} "
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
            side = self._position_side[coin]
            if side not in ("LONG", "SHORT"):
                continue
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid = _as_float(self._get_mid(coin))
            if not entry or not amt or not mid:
                continue
            if side == "SHORT":
                total += amt * (entry - mid)
            else:
                total += amt * (mid - entry)
        return round(total, 4)

    def _write_status(self):
        try:
            from risk_park import maybe_send
            maybe_send(BOT_ID, self.connectors.get(self.config.connector_name))
        except Exception:
            pass
        if not self._wallet_ok_cached:
            payload = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "ready": self._trading_active,
                "wallet_ok": False,
                "connector": self.config.connector_name,
                "subaccount": _subaccount() or None,
                "coins": list(self._coins),
                "pairs": [_pair_for(c) for c in self._coins],
                "equity_usd": 0.0,
                "free_usd": 0.0,
                "perp_usd": 0.0,
                "spot_usd": 0.0,
                "open_upnl_usd": 0.0,
                "bias": dict(self._bias),
                "positions": {c: {
                    "pair": _pair_for(c), "bias": self._bias.get(c),
                    "position_side": None, "leverage": self._leverage_for(c),
                } for c in self._coins},
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
            return
        positions = {}
        for coin in self._coins:
            mid = self._get_mid(coin)
            side = self._position_side[coin]
            roe = self._calc_roe_pct(coin, mid) if mid and side else None
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid_f = _as_float(mid)
            pnl = None
            if side in ("LONG", "SHORT") and entry and amt and mid_f:
                pnl = round(amt * ((entry - mid_f) if side == "SHORT" else (mid_f - entry)), 4)
            s = self._signal.get(coin) or {}
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": entry,
                "amount": amt,
                "mid_price": mid_f,
                "mark_px": mid_f,
                "unrealized_pnl": pnl,
                "pnl_usd": pnl,
                "roe_pct": round(roe, 3) if roe is not None else None,
                "scaled_out": bool(self._scaled_out.get(coin)),
                "n_adds": int(self._n_adds.get(coin) or 0),
                "trail_px": self._trail_px.get(coin),
                "signal": s.get("label"),
                "trend_up": bool(s.get("trend_up")),
                "ema": s.get("ema"),
                "bbp": s.get("bbp"),
                "sma": s.get("sma"),
                "st_dir": s.get("st_dir"),
                "st_line": s.get("st_line"),
                "st_dist": s.get("st_dist"),
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
                "moon": self._moon_on(coin),
                "moon_pct": float((self._moon.get(coin) or {}).get("coin_pct") or MOON_COIN_PCT) if self._moon_on(coin) else None,
                "moon_anchor_px": _as_float((self._moon.get(coin) or {}).get("anchor_px")) if self._moon_on(coin) else None,
                "moon_target_px": self._moon_target_px(coin),
            }
        log = self._read_trade_log()
        summary = self._summarize_trades(list(log.get("trades") or []))
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "ready": self._trading_active,
            "wallet_ok": self._wallet_ok_cached,
            "connector": self.config.connector_name,
            "subaccount": _subaccount() or None,
            "coins": list(self._coins),
            "pairs": [_pair_for(c) for c in self._coins],
            "leverage": {c: self._leverage_for(c) for c in self._coins},
            "scale_roe_pct": float(_ctrl("scale_roe_pct", self.config.scale_roe_pct)),
            "scale_out_pct": float(_ctrl("scale_out_pct", self.config.scale_out_pct)),
            "st_max_dist": float(_ctrl("st_max_dist", self.config.st_max_dist)),
            "margin_pct": float(_ctrl("margin_pct", self.config.margin_pct or 1.0)),
            "sl_roe_pct": float(_live_sl_roe(self.config.sl_roe_pct)),
            "moon": {c: dict(self._moon.get(c) or {}) for c in self._coins},
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
            from matrix_store import try_status_put, ensure, kv_set
            try_status_put(BOT_ID, payload)
            ensure()
            live = {
                "ts": payload.get("ts"),
                "equity_usd": payload.get("equity_usd"),
                "free_usd": payload.get("free_usd"),
                "open_upnl_usd": payload.get("open_upnl_usd"),
                "n_pos": sum(
                    1 for p in (payload.get("positions") or {}).values()
                    if isinstance(p, dict) and str(p.get("position_side") or "") in ("LONG", "SHORT")
                ),
                "positions": payload.get("positions"),
                "sl_roe_pct": payload.get("sl_roe_pct"),
                "bias": payload.get("bias"),
            }
            kv_set(BOT_ID, "live", live, "hunter")
        except Exception:
            pass
