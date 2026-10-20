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
helliot_trader.py
=================
Elliott-structure perpetual trader on a Hyperliquid *subaccount*.

Runs in container hummingbot-helliot with use_vault=true so orders hit
subaccount  — parallel to HL Swing
on the main account, same coins (BTC / ETH / LTC).

Auto entry (when bias is LONG/SHORT):
  - 15m swing pivots → last 3-wave (L-H-L or H-L-H)
  - LONG: last impulse up, price in wave-2/4 fib retrace 38.2–78.6%
  - SHORT: last impulse down, same retrace zone
  - Target: wave-3 1.618 extension of wave 1 from the retrace extreme

Dashboard 100/50/20% buttons open immediately (skip wave wait).
Hard ROE exits: +93% TP / −20% SL plus structure target / invalidation.
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
            row = (json.load(f) or {}).get("hummingbot-helliot") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe("hummingbot-helliot", default))
    except Exception:
        return float(default)

try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default


def _ctrl(key, default):
    return _ctrl_live("hummingbot-helliot", key, default)

BOT_ID = "hummingbot-helliot"
BIAS_FILE = "/home/hummingbot/conf/helliot_bias.json"
STATUS_FILE = "/home/hummingbot/conf/helliot_status.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/helliot_trades.json"

HELLIOT_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
DEFAULT_SUBACCOUNT = ""
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"


def _subaccount() -> str:
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        for bot in data.get("bots") or []:
            if bot.get("id") == "hummingbot-helliot":
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


class HelliotTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(HELLIOT_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    tp_roe_pct: float = Field(93.0)
    sl_roe_pct: float = Field(20.0)

    candles_connector: str = Field("hyperliquid_perpetual")
    interval: str = Field("15m")
    pivot_left: int = Field(3)
    wave2_min: float = Field(0.382)
    wave2_max: float = Field(0.786)
    wave3_ext: float = Field(1.618)
    candle_records: int = Field(180)

    signal_interval: int = Field(30)
    status_interval: int = Field(30)

    def helliot_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or HELLIOT_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in HELLIOT_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(HELLIOT_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.helliot_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class HelliotTrader(StrategyV2Base):
    """Elliott pivot structure + dashboard bias on HL subaccount."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: HelliotTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.helliot_coins()

        self._bias: Dict[str, str] = {c: "FLAT" for c in self._coins}
        self._last_bias_read = 0.0

        self._wave: Dict[str, dict] = {c: {} for c in self._coins}
        self._last_signal_check: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._position_side: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._entry_price: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._amount: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._entry_time: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._target_px: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._invalid_px: Dict[str, Optional[float]] = {c: None for c in self._coins}
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
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=_pair_for(coin),
                interval=self.config.interval,
                max_records=self.config.candle_records,
            ))

        self.logger().info(
            f"Helliot init | subaccount={_subaccount()[:10]}… coins={','.join(self._coins)} "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"W2={config.wave2_min}-{config.wave2_max} W3ext={config.wave3_ext} "
            f"TP_ROE={config.tp_roe_pct}% SL_ROE={config.sl_roe_pct}%"
        )

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
            self._update_wave(coin)
            if self._position_side[coin] is not None:
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
                    f"Helliot ready | {self.config.connector_name} sub={_subaccount()} bias={self._bias}"
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
            self.logger().info(f"Force open {coin} skipped — set LONG/SHORT first")
            self._sql_done(cmd, "need long/short")
            return
        if self._position_side[coin] is not None:
            self.logger().info(f"Force open {coin} skipped — already {self._position_side[coin]}")
            self._sql_done(cmd, "already open")
            return
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        w = self._wave.get(coin) or {}
        tgt, inv = w.get("target"), w.get("invalid")
        if side == "LONG" and w.get("dir") == "UP" and tgt and float(tgt) > 0:
            self._target_px[coin] = tgt
            self._invalid_px[coin] = inv
        elif side == "SHORT" and w.get("dir") == "DOWN" and tgt and float(tgt) > 0:
            self._target_px[coin] = tgt
            self._invalid_px[coin] = inv
        else:
            self._target_px[coin] = None
            self._invalid_px[coin] = None
        self.logger().info(f"Dashboard FORCE OPEN {side} {coin} at {pct*100:.0f}% leftover")
        self._open_now(coin, side, Decimal(str(pct)), reason=f"dashboard {pct*100:.0f}%")
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
        """USDC sitting in the subaccount *spot* wallet (not perp margin)."""
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

    # ── Elliott pivots ───────────────────────────────────────────────────────

    def _extract_pivots(self, highs, lows, left: int) -> List[dict]:
        n = len(highs)
        raw = []
        for i in range(left, n - left):
            is_h = all(highs[i] > highs[i - j] and highs[i] >= highs[i + j] for j in range(1, left + 1))
            is_l = all(lows[i] < lows[i - j] and lows[i] <= lows[i + j] for j in range(1, left + 1))
            if is_h:
                raw.append({"i": i, "kind": "H", "price": float(highs[i])})
            elif is_l:
                raw.append({"i": i, "kind": "L", "price": float(lows[i])})
        if not raw:
            return []
        merged = [raw[0]]
        for p in raw[1:]:
            last = merged[-1]
            if p["kind"] == last["kind"]:
                if p["kind"] == "H" and p["price"] >= last["price"]:
                    merged[-1] = p
                elif p["kind"] == "L" and p["price"] <= last["price"]:
                    merged[-1] = p
            else:
                merged.append(p)
        return merged

    def _update_wave(self, coin: str):
        now = self.current_timestamp
        if now - self._last_signal_check[coin] < self.config.signal_interval:
            return
        self._last_signal_check[coin] = now
        pair = _pair_for(coin)
        info = {
            "label": "n/a", "dir": None, "retrace": None,
            "target": None, "invalid": None, "long_ok": False, "short_ok": False,
        }
        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=pair,
                interval=self.config.interval,
                max_records=self.config.candle_records,
            )
            if df is None or len(df) < 40:
                self._wave[coin] = info
                return
            highs = list(df["high"].astype(float).values)
            lows = list(df["low"].astype(float).values)
            closes = list(df["close"].astype(float).values)
            price = float(closes[-1])
            pivots = self._extract_pivots(highs, lows, int(self.config.pivot_left))
            if len(pivots) < 3:
                info["label"] = "need pivots"
                self._wave[coin] = info
                return
            a, b, c = pivots[-3], pivots[-2], pivots[-1]
            wmin = float(_ctrl("wave2_min", self.config.wave2_min))
            wmax = float(_ctrl("wave2_max", self.config.wave2_max))
            ext = float(_ctrl("wave3_ext", self.config.wave3_ext))

            if a["kind"] == "L" and b["kind"] == "H" and c["kind"] == "L":
                w1 = b["price"] - a["price"]
                if w1 > 0:
                    retr = (b["price"] - c["price"]) / w1
                    info["dir"] = "UP"
                    info["retrace"] = round(retr, 3)
                    info["invalid"] = c["price"]
                    info["target"] = c["price"] + ext * w1
                    info["wave_a"] = a["price"]
                    info["wave_b"] = b["price"]
                    info["wave_c"] = c["price"]
                    info["zone_lo"] = b["price"] - wmax * w1
                    info["zone_hi"] = b["price"] - wmin * w1
                    in_zone = wmin <= retr <= wmax
                    bounced = price > c["price"] and price < b["price"]
                    if in_zone and bounced:
                        info["label"] = "W2 long zone → W3"
                        info["long_ok"] = True
                    elif retr < wmin:
                        info["label"] = "impulse up (shallow W2)"
                    elif retr > wmax:
                        info["label"] = "W2 too deep / possible ABC"
                    else:
                        info["label"] = "W2 waiting bounce"
            elif a["kind"] == "H" and b["kind"] == "L" and c["kind"] == "H":
                w1 = a["price"] - b["price"]
                if w1 > 0:
                    retr = (c["price"] - b["price"]) / w1
                    info["dir"] = "DOWN"
                    info["retrace"] = round(retr, 3)
                    info["invalid"] = c["price"]
                    info["target"] = c["price"] - ext * w1
                    info["wave_a"] = a["price"]
                    info["wave_b"] = b["price"]
                    info["wave_c"] = c["price"]
                    info["zone_lo"] = b["price"] + wmin * w1
                    info["zone_hi"] = b["price"] + wmax * w1
                    in_zone = wmin <= retr <= wmax
                    faded = price < c["price"] and price > b["price"]
                    if in_zone and faded:
                        info["label"] = "W2 short zone → W3"
                        info["short_ok"] = True
                    elif retr < wmin:
                        info["label"] = "impulse down (shallow W2)"
                    elif retr > wmax:
                        info["label"] = "W2 too deep / possible ABC"
                    else:
                        info["label"] = "W2 waiting fade"
            else:
                info["label"] = f"{a['kind']}-{b['kind']}-{c['kind']}"
            self._wave[coin] = info
        except Exception as e:
            self.logger().warning(f"Wave error {coin}: {e}")
            self._wave[coin] = info

    def _try_enter(self, coin: str):
        if self._bias.get(coin) == "FLAT":
            return
        if self._position_side[coin] is not None:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        if self.current_timestamp - self._last_global_entry < 15:
            return
        w = self._wave.get(coin) or {}
        want_long = self._bias[coin] == "LONG" and w.get("long_ok")
        want_short = self._bias[coin] == "SHORT" and w.get("short_ok")
        if not want_long and not want_short:
            return
        side = "LONG" if want_long else "SHORT"
        self._target_px[coin] = w.get("target")
        self._invalid_px[coin] = w.get("invalid")
        pct = Decimal(str(_ctrl("margin_pct", self.config.margin_pct or 1.0)))
        self._open_now(coin, side, pct, reason=str(w.get("label") or "elliott W2"))

    def _open_now(self, coin: str, side: str, margin_pct: Decimal, reason: str):
        if side not in ("LONG", "SHORT") or self._position_side[coin] is not None:
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
        w = self._wave.get(coin) or {}
        self.logger().info(
            f"HELLIOT ENTRY {side} {pair} | {reason} | {float(pct)*100:.0f}% leftover "
            f"wave={w.get('label')} retr={w.get('retrace')} tgt={w.get('target')} "
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
                    self.logger().info(f"{coin} flat on exchange — clearing helliot state")
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

    def _check_exits(self, coin: str):
        if self._is_close_pending(coin):
            return
        mid = self._get_mid(coin)
        if not mid or not self._entry_price[coin]:
            return
        side = self._position_side[coin]
        # Do not wave-exit on the same tick / first seconds after a manual open
        et = self._entry_time.get(coin)
        age = (self.current_timestamp - et) if et else 999
        px = float(mid)
        entry = float(self._entry_price[coin])
        tgt = self._target_px.get(coin)
        inv = self._invalid_px.get(coin)
        if age >= 20:
            if side == "LONG" and tgt and float(tgt) > entry and px >= float(tgt):
                self._close_position(coin, f"wave target {tgt:.4f}")
                return
            if side == "SHORT" and tgt and float(tgt) < entry and px <= float(tgt):
                self._close_position(coin, f"wave target {tgt:.4f}")
                return
            if side == "LONG" and inv and float(inv) < entry and px <= float(inv):
                self._close_position(coin, f"wave invalidation {inv:.4f}")
                return
            if side == "SHORT" and inv and float(inv) > entry and px >= float(inv):
                self._close_position(coin, f"wave invalidation {inv:.4f}")
                return
        roe = self._calc_roe_pct(coin, mid)
        if roe is None:
            return
        if roe >= float(_ctrl("tp_roe_pct", self.config.tp_roe_pct)):
            self._close_position(coin, f"TP ROE {roe:.2f}%")
            return
        sl = float(_live_sl_roe(self.config.sl_roe_pct))
        if age >= 5 and roe <= -sl:
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
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, close_px, PositionAction.CLOSE)
            else:
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, close_px, PositionAction.CLOSE)
            roe = self._calc_roe_pct(coin, mid) if mid else None
            roe_s = f"ROE~{roe:.2f}%" if roe is not None else "ROE=n/a"
            self.logger().info(f"CLOSED {side} {amount} {pair} | reason={reason} | {roe_s}")
            entry = _as_float(self._entry_price[coin])
            exit_px = _as_float(mid)
            amt = _as_float(amount)
            pnl = None
            if entry and exit_px and amt:
                pnl = amt * (exit_px - entry) if side == "LONG" else amt * (entry - exit_px)
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
        self._position_side[coin] = None
        self._entry_price[coin] = None
        self._amount[coin] = None
        self._entry_time[coin] = None
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        lines = [
            f"Helliot | sub={_subaccount()[:10]}… | "
            f"lev={' '.join(f'{c}={self._leverage_for(c)}x' for c in self._coins)} "
            f"perp=${float(self._get_equity_usd()):.2f} free=${float(free):.2f} "
            f"spot=${float(self._get_spot_usdc()):.2f}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            w = self._wave.get(coin) or {}
            roe_s = f"{roe:.2f}%" if roe is not None else "n/a"
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} wave={w.get('label')} retr={w.get('retrace')} "
                f"tgt={w.get('target')} | pos={self._position_side[coin] or 'FLAT'} "
                f"entry={self._entry_price[coin]} mid={mid} ROE={roe_s}"
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
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid = _as_float(self._get_mid(coin))
            if not side or not entry or not amt or not mid:
                continue
            total += amt * (mid - entry) if side == "LONG" else amt * (entry - mid)
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
            w = self._wave.get(coin) or {}
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": _as_float(self._entry_price[coin]),
                "amount": _as_float(self._amount[coin]),
                "mid_price": _as_float(mid),
                "roe_pct": round(roe, 3) if roe is not None else None,
                "wave": w.get("label"),
                "wave_dir": w.get("dir"),
                "retrace": w.get("retrace"),
                "target": w.get("target"),
                "invalid": w.get("invalid"),
                "wave_a": w.get("wave_a"),
                "wave_b": w.get("wave_b"),
                "wave_c": w.get("wave_c"),
                "zone_lo": w.get("zone_lo"),
                "zone_hi": w.get("zone_hi"),
                "long_ok": bool(w.get("long_ok")),
                "short_ok": bool(w.get("short_ok")),
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
            "tp_roe_pct": float(_ctrl("tp_roe_pct", self.config.tp_roe_pct)),
            "wave2_min": float(_ctrl("wave2_min", self.config.wave2_min)),
            "wave2_max": float(_ctrl("wave2_max", self.config.wave2_max)),
            "wave3_ext": float(_ctrl("wave3_ext", self.config.wave3_ext)),
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
            try:
                from matrix_store import try_status_put
                try_status_put(BOT_ID, payload)
            except Exception:
                pass
        except Exception as e:
            self.logger().warning(f"status write failed: {e}")
