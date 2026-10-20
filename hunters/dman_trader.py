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
dman_trader.py
==============
Official Hummingbot DMan V3, adapted for Hyperliquid BTC / ETH / LTC.

- 15m Bollinger: LONG when BBP < 0 (below lower band), SHORT when BBP > 1.
- New entries use 100% leftover perp USD in one clip (all funds, after RISK park).
- Trailing stop + Home ROE stop-loss. No extra DCA clips on the open.
- Trailing stop (activation 1.5% / delta 0.5% of price) + Home ROE stop-loss.
- Dashboard LONG/SHORT/FLAT + 100/50/20% force open, CLOSE.

Does not trade until Confiig has a unique subaccount (not Copy/Swing/…).
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
    from bot_sl_override import effective_sl_roe, effective_time_limit_s
except ImportError:
    def effective_sl_roe(bot_id, default=20.0):
        return float(default)
    def effective_time_limit_s(bot_id, default=43200):
        return int(default)

SL_FILE = "/home/hummingbot/conf/bot_sl.json"
_HOLD_S = {12: 12 * 3600, 24: 24 * 3600, 68: 68 * 3600}


def _live_sl_roe(default=20.0) -> float:
    """Home SL from bot_sl.json — do not rely on import path."""
    try:
        with open(SL_FILE, "r") as f:
            row = (json.load(f) or {}).get("hummingbot-dman") or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe("hummingbot-dman", default))
    except Exception:
        return float(default)


def _live_hold_s(default=43200) -> int:
    """Home TIME hold from bot_sl.json (12/24/68h)."""
    try:
        with open(SL_FILE, "r") as f:
            row = (json.load(f) or {}).get("hummingbot-dman") or {}
        if row.get("time_limit_s") is not None:
            v = int(row.get("time_limit_s"))
            if v in _HOLD_S.values():
                return v
        if row.get("time_limit_h") is not None:
            h = int(row.get("time_limit_h"))
            if h in _HOLD_S:
                return _HOLD_S[h]
    except Exception:
        pass
    try:
        return int(effective_time_limit_s("hummingbot-dman", default))
    except Exception:
        return int(default)

BIAS_FILE = "/home/hummingbot/conf/dman_bias.json"
STATUS_FILE = "/home/hummingbot/conf/dman_status.json"
TUNE_FILE = "/home/hummingbot/conf/dman_tune.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/dman_trades.json"

DMAN_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
DEFAULT_SUBACCOUNT = ""
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"
SELF_ID = "hummingbot-dman"


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


class DmanTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(DMAN_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    candles_connector: str = Field("hyperliquid_perpetual")
    interval: str = Field("15m")
    bb_length: int = Field(100)
    bb_std: float = Field(2.0)
    bb_long_threshold: float = Field(0.0)
    bb_short_threshold: float = Field(1.0)
    candle_records: int = Field(130)

    dca_spreads: List[float] = Field(default_factory=lambda: [0.001, 0.018, 0.15, 0.25])
    dynamic_order_spread: bool = Field(True)
    dynamic_target: bool = Field(False)
    stop_loss: float = Field(0.03)
    trail_activation: float = Field(0.015)
    trail_delta: float = Field(0.005)
    time_limit: int = Field(43200)
    cooldown_time: int = Field(300)

    signal_interval: int = Field(30)
    status_interval: int = Field(30)

    def dman_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or DMAN_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in DMAN_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(DMAN_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.dman_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class DmanTrader(StrategyV2Base):
    """DMan V3 mean-reversion + DCA ladder on Hyperliquid."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: DmanTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.dman_coins()
        self._hl_max_leverage: Dict[str, int] = {}

        self._bias: Dict[str, str] = {c: "FLAT" for c in self._coins}
        self._last_bias_read = 0.0
        self._tune: Dict[str, float] = {}
        self._last_tune_read = 0.0

        self._bbp: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._bbb: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._signal: Dict[str, int] = {c: 0 for c in self._coins}
        self._last_signal_check: Dict[str, float] = {c: 0.0 for c in self._coins}

        self._position_side: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._entry_price: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._amount: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._entry_time: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._leverage_used: Dict[str, int] = {c: 1 for c in self._coins}
        self._leverage_set: Dict[str, bool] = {c: False for c in self._coins}
        self._last_entry_attempt: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._close_pending_until: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_closed: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._dca: Dict[str, Optional[dict]] = {c: None for c in self._coins}

        self._trading_active = False
        self._wallet_ok_cached = False
        self._wallet_warned = False
        self._last_status_log = 0.0
        self._last_global_entry = 0.0
        self._fetch_hl_max_leverage()
        self._leverage_used = {c: self._leverage_for(c) for c in self._coins}
        self._lev_tier_seen = self._leverage_tier()
        self._load_dca_from_status()

        for coin in self._coins:
            self.market_data_provider.initialize_candles_feed(CandlesConfig(
                connector=self.config.candles_connector,
                trading_pair=_pair_for(coin),
                interval=self.config.interval,
                max_records=self.config.candle_records,
            ))

        sub = _subaccount() or "UNSET"
        self.logger().info(
            f"Dman V3 init | sub={sub[:12]}… coins={','.join(self._coins)} "
            f"BB {config.interval} n={config.bb_length} long<={config.bb_long_threshold} "
            f"short>={config.bb_short_threshold} DCA={config.dca_spreads} "
            f"SL={config.stop_loss*100:.1f}% trail={config.trail_activation*100:.1f}/"
            f"{config.trail_delta*100:.1f}%"
        )

    def _load_dca_from_status(self):
        try:
            with open(STATUS_FILE, "r") as f:
                data = json.load(f) or {}
            plans = data.get("dca") or {}
            for coin in self._coins:
                row = plans.get(coin)
                if isinstance(row, dict) and row.get("side") and row.get("levels"):
                    self._dca[coin] = row
        except Exception:
            pass

    def on_tick(self):
        self._read_bias()
        self._read_tune()
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
                    "Dman blocked — set a unique subaccount in Confiig "
                    "(must not be Copy / Swing / Helliot / Surfn / Shorty)"
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
                self._maybe_dca_add(coin)
                self._check_exits(coin)
            else:
                self._try_enter(coin)
        now = self.current_timestamp
        if now - self._last_status_log >= self.config.status_interval:
            self._last_status_log = now
            self.logger().info(self.format_status())
        self._write_status()

    def _wallet_allowed(self) -> bool:
        addr = (_subaccount() or "").strip().lower()
        ok = bool(addr.startswith("0x") and len(addr) == 42 and addr not in _occupied_wallets())
        self._wallet_ok_cached = ok
        return ok

    def _check_ready(self):
        try:
            connector = self.connectors[self.config.connector_name]
            if connector.ready:
                self._trading_active = True
                self.logger().info(
                    f"Dman ready | {self.config.connector_name} sub={_subaccount() or 'UNSET'} "
                    f"wallet_ok={self._wallet_allowed()} bias={self._bias}"
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

    def _leverage_tier(self) -> str:
        try:
            from leverage_tier import tier_name
            return tier_name(SELF_ID)
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
            sql = live_bias(SELF_ID, self._coins, VALID_BIAS)
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

    def _tune_f(self, key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(self._tune[key]) if key in self._tune else float(default)
        except (TypeError, ValueError, KeyError):
            v = float(default)
        return max(lo, min(hi, v))

    def _bb_long_th(self) -> float:
        return self._tune_f("bb_long_threshold", self.config.bb_long_threshold, -0.2, 1.0)

    def _bb_short_th(self) -> float:
        return self._tune_f("bb_short_threshold", self.config.bb_short_threshold, 0.0, 1.2)

    def _trail_act(self) -> float:
        return self._tune_f("trail_activation", self.config.trail_activation, 0.001, 0.20)

    def _trail_dlt(self) -> float:
        return self._tune_f("trail_delta", self.config.trail_delta, 0.001, 0.10)

    def _read_tune(self):
        now = time.time()
        if now - self._last_tune_read < 1.0:
            return
        self._last_tune_read = now
        parsed = {}
        try:
            if os.path.exists(TUNE_FILE):
                with open(TUNE_FILE, "r") as f:
                    data = json.load(f) or {}
                if isinstance(data, dict):
                    for k in ("bb_long_threshold", "bb_short_threshold", "trail_activation", "trail_delta"):
                        if data.get(k) is None:
                            continue
                        try:
                            parsed[k] = float(data[k])
                        except (TypeError, ValueError):
                            pass
            from matrix_store import ensure, kv_map
            ensure()
            sql = kv_map("hummingbot-dman")
            for k in ("bb_long_threshold", "bb_short_threshold", "trail_activation", "trail_delta"):
                if sql.get(k) is None:
                    continue
                try:
                    parsed[k] = float(sql[k])
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            self.logger().warning(f"Tune read error: {e}")
            return
        if parsed != self._tune:
            self._tune = parsed
            self.logger().info(
                f"DMan tune | long BBP<{self._bb_long_th():.2f} short BBP>{self._bb_short_th():.2f} "
                f"trail {self._trail_act()*100:.2f}/{self._trail_dlt()*100:.2f}%"
            )

    def _sql_cmd(self, kinds):
        try:
            from matrix_store import ensure, command_next
            ensure()
            return command_next(SELF_ID, kinds=kinds)
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
        side = str(data.get("side") or self._bias.get(coin) or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(f"Force open {coin} skipped — set LONG or SHORT")
            self._sql_done(cmd, "need long/short")
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
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        self.logger().info(f"Dashboard FORCE OPEN {side} {coin} at {pct*100:.0f}% leftover (single clip)")
        self._start_ladder(
            coin, side, Decimal(str(pct)),
            reason=f"dashboard {pct*100:.0f}%",
            full_now=True,
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
            return deduct(SELF_ID, raw)
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

    def _spread_mult(self, coin: str) -> float:
        if not self.config.dynamic_order_spread:
            return 1.0
        bbb = self._bbb.get(coin)
        if bbb is None:
            return 1.0
        # Official: BBB / 200. BBB is % bandwidth (e.g. 8.0 for 8%).
        return max(float(bbb) / 200.0, 0.01)

    def _target_mult(self, coin: str) -> float:
        if self.config.dynamic_target:
            return self._spread_mult(coin)
        return 1.0

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
            nbb = int(self.config.bb_length)
            if df is None or len(df) < nbb + 2:
                return
            closes = [float(x) for x in df["close"].astype(float).values]
            price = closes[-1]
            window = closes[-nbb:]
            sma = sum(window) / nbb
            var = sum((x - sma) ** 2 for x in window) / nbb
            std = var ** 0.5
            upper = sma + float(self.config.bb_std) * std
            lower = sma - float(self.config.bb_std) * std
            width = upper - lower
            self._bbp[coin] = (price - lower) / width if width else 0.5
            self._bbb[coin] = (width / price * 100.0) if price else None
            bbp = self._bbp[coin]
            sig = 0
            if bbp is not None and bbp < self._bb_long_th():
                sig = 1
            elif bbp is not None and bbp > self._bb_short_th():
                sig = -1
            self._signal[coin] = sig
        except Exception as e:
            self.logger().warning(f"Signal error {coin}: {e}")

    def _try_enter(self, coin: str):
        bias = self._bias.get(coin)
        if bias not in ("LONG", "SHORT"):
            return
        if self._position_side[coin] is not None:
            return
        if self.current_timestamp - self._last_closed.get(coin, 0) < float(self.config.cooldown_time or 0):
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        if self.current_timestamp - self._last_global_entry < 15:
            return
        sig = self._signal.get(coin) or 0
        if bias == "LONG" and sig != 1:
            return
        if bias == "SHORT" and sig != -1:
            return
        pct = Decimal("1")
        self._start_ladder(coin, bias, pct, reason="DMan BB all leftover", full_now=True)

    def _build_levels(self, coin: str, origin: Decimal, total_margin: Decimal, side: str) -> Optional[list]:
        spreads = [float(s) for s in (self.config.dca_spreads or [0.001])]
        if not spreads:
            return None
        lev = Decimal(str(self._leverage_for(coin)))
        min_n = Decimal(str(self.config.min_notional_usd))
        n = len(spreads)
        while n > 0:
            each = (total_margin / Decimal(n)) * lev
            if each >= min_n:
                break
            n -= 1
        if n <= 0:
            return None
        spreads = spreads[:n]
        each_margin = (total_margin / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if each_margin < self.config.min_margin_usd:
            return None
        mult = self._spread_mult(coin)
        levels = []
        for i, spr in enumerate(spreads):
            dist = float(spr) * mult
            if side == "LONG":
                px = float(origin) * (1.0 - dist)
            else:
                px = float(origin) * (1.0 + dist)
            levels.append({
                "i": i,
                "spread": spr,
                "dist": dist,
                "target_px": px,
                "margin": float(each_margin),
                "filled": False,
                "fill_px": None,
                "amount": None,
            })
        return levels

    def _start_ladder(self, coin: str, side: str, margin_pct: Decimal, reason: str, full_now: bool = False):
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
        if not full_now:
            pct = Decimal("1")
            full_now = True
        margin = (free * pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self.logger().info(
                f"Skip {coin} entry — margin ${float(margin):.2f} < min ${float(self.config.min_margin_usd)}"
            )
            self._last_entry_attempt[coin] = self.current_timestamp
            return
        if full_now:
            levels = [{
                "i": 0,
                "spread": 0.0,
                "dist": 0.0,
                "target_px": float(mid),
                "margin": float(margin),
                "filled": False,
                "fill_px": None,
                "amount": None,
            }]
        else:
            levels = self._build_levels(coin, mid, margin, side)
        if not levels:
            self.logger().info(f"Skip {coin} entry — not enough for a DCA clip / min notional")
            self._last_entry_attempt[coin] = self.current_timestamp
            return
        self._dca[coin] = {
            "side": side,
            "origin_px": float(mid),
            "mult": self._spread_mult(coin),
            "target_mult": self._target_mult(coin),
            "levels": levels,
            "full_now": bool(full_now),
            "started": self.current_timestamp,
            "trail_active": False,
            "trail_extreme": None,
        }
        self._last_entry_attempt[coin] = self.current_timestamp
        self._last_global_entry = self.current_timestamp
        self.logger().info(
            f"DMAN LADDER {side} {coin} | {reason} | {float(pct)*100:.0f}% leftover "
            f"levels={len(levels)} BBP={self._bbp.get(coin)} BBB={self._bbb.get(coin)} "
            f"mult={self._spread_mult(coin):.4f}"
        )
        self._fill_level(coin, 0, mid, reason=reason)

    def _fill_level(self, coin: str, idx: int, mid: Decimal, reason: str) -> bool:
        plan = self._dca.get(coin) or {}
        levels = list(plan.get("levels") or [])
        if idx < 0 or idx >= len(levels):
            return False
        lvl = levels[idx]
        if lvl.get("filled"):
            return False
        side = plan.get("side")
        if side not in ("LONG", "SHORT"):
            return False
        if not self._wallet_allowed():
            return False
        lev = self._leverage_for(coin)
        margin = Decimal(str(lvl.get("margin") or 0))
        free = self._get_free_usd()
        if margin > free:
            margin = free.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self.logger().info(f"Skip {coin} DCA[{idx}] — margin ${float(margin):.2f}")
            return False
        notional = margin * Decimal(str(lev))
        if notional < Decimal(str(self.config.min_notional_usd)):
            self.logger().info(f"Skip {coin} DCA[{idx}] — notional ${float(notional):.2f}")
            return False
        amount = self._quantize_amount(coin, notional / mid)
        if amount <= 0:
            return False
        pair = _pair_for(coin)
        action = "OPEN" if idx == 0 else "ADD"
        try:
            if side == "LONG":
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            else:
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
        except Exception as e:
            self.logger().error(f"{action} {coin} DCA[{idx}] failed: {e}")
            return False
        prev_amt = self._amount[coin] or Decimal("0")
        prev_px = self._entry_price[coin] or mid
        new_amt = prev_amt + amount
        if new_amt > 0:
            avg = (prev_amt * prev_px + amount * mid) / new_amt
        else:
            avg = mid
        self._position_side[coin] = side
        self._entry_price[coin] = avg
        self._amount[coin] = new_amt
        if self._entry_time[coin] is None:
            self._entry_time[coin] = self.current_timestamp
        self._leverage_used[coin] = lev
        lvl["filled"] = True
        lvl["fill_px"] = float(mid)
        lvl["amount"] = float(amount)
        levels[idx] = lvl
        plan["levels"] = levels
        self._dca[coin] = plan
        filled_n = sum(1 for x in levels if x.get("filled"))
        self.logger().info(
            f"DMAN {action} {side} {pair} lvl {idx+1}/{len(levels)} | {reason} "
            f"clip={amount} mid=${float(mid):.4f} avg=${float(avg):.4f} lev={lev}x"
        )
        self._log_trade({
            "action": action, "coin": coin, "side": side,
            "amount": _as_float(amount), "price": _as_float(mid),
            "leverage": int(lev), "reason": f"{reason} dca {idx+1}/{len(levels)}",
            "pnl_usd": None, "roe_pct": None, "dca_filled": filled_n,
        })
        self._write_status()
        return True

    def _maybe_dca_add(self, coin: str):
        plan = self._dca.get(coin)
        if not plan:
            return
        side = plan.get("side")
        if side != self._position_side[coin]:
            return
        if self.current_timestamp - self._last_entry_attempt[coin] < 15:
            return
        mid = self._get_mid(coin)
        if not mid:
            return
        px = float(mid)
        for i, lvl in enumerate(plan.get("levels") or []):
            if lvl.get("filled"):
                continue
            target = float(lvl.get("target_px") or 0)
            if target <= 0:
                continue
            hit = (side == "LONG" and px <= target) or (side == "SHORT" and px >= target)
            if not hit:
                return
            self._last_entry_attempt[coin] = self.current_timestamp
            self._fill_level(coin, i, mid, reason="DCA add")
            return

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
                    self.logger().info(f"{coin} flat on exchange — clearing dman state")
                    self._clear_pos(coin)
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
        if side == "SHORT":
            return (entry - px) / entry * lev * 100.0
        return (px - entry) / entry * lev * 100.0

    def _check_exits(self, coin: str):
        if self._is_close_pending(coin):
            return
        mid = self._get_mid(coin)
        if not mid or not self._entry_price[coin] or not self._position_side[coin]:
            return
        et = self._entry_time.get(coin)
        age = (self.current_timestamp - et) if et else 999
        side = self._position_side[coin]
        entry = float(self._entry_price[coin])
        px = float(mid)
        tmult = float((self._dca.get(coin) or {}).get("target_mult") or self._target_mult(coin))
        act = self._trail_act() * tmult
        delta = self._trail_dlt() * tmult
        if age >= 5:
            roe = self._calc_roe_pct(coin, mid)
            sl = float(_live_sl_roe(20.0))
            if roe is not None and roe <= -sl:
                self._close_position(coin, f"SL ROE {roe:.2f}%")
                return
        plan = self._dca.get(coin) or {}
        trail_on = bool(plan.get("trail_active"))
        extreme = _as_float(plan.get("trail_extreme"))
        if not trail_on:
            if side == "LONG" and px >= entry * (1.0 + act):
                trail_on = True
                extreme = px
            elif side == "SHORT" and px <= entry * (1.0 - act):
                trail_on = True
                extreme = px
        if trail_on and extreme:
            if side == "LONG":
                extreme = max(extreme, px)
                if px <= extreme * (1.0 - delta):
                    self._close_position(coin, f"TRAIL from {extreme:.4f}")
                    return
            else:
                extreme = min(extreme, px)
                if px >= extreme * (1.0 + delta):
                    self._close_position(coin, f"TRAIL from {extreme:.4f}")
                    return
            plan["trail_active"] = True
            plan["trail_extreme"] = extreme
            self._dca[coin] = plan
        limit = int(_live_hold_s(self.config.time_limit or 43200))
        started = float(plan.get("started") or et or 0)
        if limit > 0 and started and (self.current_timestamp - started) >= limit:
            self._close_position(coin, f"TIME {limit}s")
            return

    def _clear_pos(self, coin: str):
        self._position_side[coin] = None
        self._entry_price[coin] = None
        self._amount[coin] = None
        self._entry_time[coin] = None
        self._dca[coin] = None

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
            if side == "LONG":
                px = mid * Decimal("0.99") if mid else Decimal("0")
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, px, PositionAction.CLOSE)
            else:
                px = mid * Decimal("1.01") if mid else Decimal("0")
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, px, PositionAction.CLOSE)
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
                if side == "SHORT":
                    pnl = amt * (entry - exit_px)
                else:
                    pnl = amt * (exit_px - entry)
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
        self._last_closed[coin] = self.current_timestamp
        self._clear_pos(coin)
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        lines = [
            f"Dman V3 | sub={(_subaccount() or 'UNSET')[:12]}… | "
            f"ok={self._wallet_ok_cached} perp=${float(self._get_equity_usd()):.2f} "
            f"free=${float(free):.2f} spot=${float(self._get_spot_usdc()):.2f}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            bbp = self._bbp[coin]
            sig = self._signal.get(coin) or 0
            plan = self._dca.get(coin) or {}
            levels = plan.get("levels") or []
            filled = sum(1 for x in levels if x.get("filled"))
            trail = "TRAIL" if plan.get("trail_active") else ""
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} BBP={bbp if bbp is None else f'{bbp:.3f}'} "
                f"sig={sig} dca={filled}/{len(levels) or 0} {trail} "
                f"| pos={self._position_side[coin] or 'FLAT'} mid={mid} ROE="
                f"{f'{roe:.2f}%' if roe is not None else 'n/a'}"
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
            side = self._position_side[coin]
            entry = _as_float(self._entry_price[coin])
            amt = _as_float(self._amount[coin])
            mid = _as_float(self._get_mid(coin))
            if not side or not entry or not amt or not mid:
                continue
            if side == "SHORT":
                total += amt * (entry - mid)
            else:
                total += amt * (mid - entry)
        return round(total, 4)

    def _write_status(self):
        try:
            from risk_park import maybe_send
            maybe_send(SELF_ID, self.connectors.get(self.config.connector_name))
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
                try:
                    from matrix_store import try_status_put
                    try_status_put(SELF_ID, payload)
                except Exception:
                    pass
            except Exception as e:
                self.logger().warning(f"status write failed: {e}")
            return
        positions = {}
        dca_out = {}
        for coin in self._coins:
            mid = self._get_mid(coin)
            side = self._position_side[coin]
            roe = self._calc_roe_pct(coin, mid) if mid and side else None
            plan = self._dca.get(coin) or {}
            levels = plan.get("levels") or []
            filled = sum(1 for x in levels if x.get("filled"))
            next_px = None
            for lvl in levels:
                if not lvl.get("filled"):
                    next_px = lvl.get("target_px")
                    break
            if plan:
                dca_out[coin] = plan
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": _as_float(self._entry_price[coin]),
                "amount": _as_float(self._amount[coin]),
                "mid_price": _as_float(mid),
                "roe_pct": round(roe, 3) if roe is not None else None,
                "bbp": self._bbp.get(coin),
                "bbb": self._bbb.get(coin),
                "signal": self._signal.get(coin) or 0,
                "dca_filled": filled,
                "dca_total": len(levels),
                "dca_next_px": next_px,
                "trail_active": bool(plan.get("trail_active")),
                "trail_extreme": plan.get("trail_extreme"),
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
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
            "stop_loss": float(self.config.stop_loss),
            "trail_activation": self._trail_act(),
            "trail_delta": self._trail_dlt(),
            "bb_long_threshold": self._bb_long_th(),
            "bb_short_threshold": self._bb_short_th(),
            "tune": dict(self._tune),
            "sl_roe_pct": float(_live_sl_roe(20.0)),
            "time_limit_s": int(_live_hold_s(self.config.time_limit or 43200)),
            "time_limit_h": round(int(_live_hold_s(self.config.time_limit or 43200)) / 3600.0, 1),
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
            "dca": dca_out,
            "positions": positions,
        }
        try:
            _atomic_json_write(STATUS_FILE, payload)
        except Exception as e:
            self.logger().warning(f"status write failed: {e}")
        try:
            from matrix_store import try_status_put
            try_status_put(SELF_ID, payload)
        except Exception:
            pass
