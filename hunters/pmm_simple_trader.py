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
pmm_simple_trader.py
====================
Official pmm_simple family on the HL Swing container (hummingbot-bn-swing).

LONG + flat  → rest one buy limit with 100% leftover perp USD (after RISK park).
LONG + in    → hold; exit on Home SL / take-profit / trail.
OPEN 100%    → market-open leftover perp USD on the current BIAS (LONG or SHORT).
SHORT + flat → idle until 100%. Never flattens an already-open clip.

Does not trade until Confiig has a unique subaccount (not used by any other hunter).
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
from hummingbot.core.utils.async_utils import safe_ensure_future
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

try:
    from ctrl_override import live as _ctrl_live
except ImportError:
    def _ctrl_live(bot_id, key, default):
        return default

SL_FILE = "/home/hummingbot/conf/bot_sl.json"
BIAS_FILE = "/home/hummingbot/conf/bn_swing_bias.json"
STATUS_FILE = "/home/hummingbot/conf/bn_swing_status.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/bn_swing_trades.json"
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"

MAKER_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
SELF_ID = "hummingbot-bn-swing"
DEFAULT_SUBACCOUNT = ""


def _live_sl_roe(default=20.0) -> float:
    try:
        with open(SL_FILE, "r") as f:
            row = (json.load(f) or {}).get(SELF_ID) or {}
        v = float(row.get("sl_roe_pct"))
        if 1.0 <= v <= 90.0:
            return v
    except Exception:
        pass
    try:
        return float(effective_sl_roe(SELF_ID, default))
    except Exception:
        return float(default)


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


class PMMSimpleTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(MAKER_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(0.60)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    buy_spreads: List[float] = Field(default_factory=lambda: [0.002, 0.004])
    sell_spreads: List[float] = Field(default_factory=lambda: [0.002, 0.004])
    buy_amounts_pct: List[float] = Field(default_factory=lambda: [1.0, 1.0])
    executor_refresh_time: int = Field(20)
    take_profit: float = Field(0.024)
    stop_loss: float = Field(0.03)
    trail_activation: float = Field(0.014)
    trail_delta: float = Field(22)
    rebuy_pct: float = Field(0.0018)
    time_limit: int = Field(0)
    cooldown_time: int = Field(15)
    up_only: bool = Field(True)

    status_interval: int = Field(30)

    def maker_coins(self) -> List[str]:
        coins = []
        for c in (self.coins or MAKER_COINS):
            coin = str(c).upper().replace("-USD", "").replace("-USDT", "")
            if coin in MAKER_COINS and coin not in coins:
                coins.append(coin)
        return coins or list(MAKER_COINS)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {_pair_for(c) for c in self.maker_coins()}
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class PMMSimpleTrader(StrategyV2Base):
    """PMM Simple hunter on Hyperliquid. 100% follows current BIAS; never flips an open clip."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: PMMSimpleTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._coins = config.maker_coins()
        self._hl_max_leverage: Dict[str, int] = {}

        self._bias: Dict[str, str] = {c: "FLAT" for c in self._coins}
        self._last_bias_read = 0.0

        self._position_side: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._entry_price: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._amount: Dict[str, Optional[Decimal]] = {c: None for c in self._coins}
        self._entry_time: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._leverage_used: Dict[str, int] = {c: 1 for c in self._coins}
        self._leverage_set: Dict[str, bool] = {c: False for c in self._coins}
        self._close_pending_until: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_closed: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_refresh: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_entry_attempt: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._pending_market_open: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._pending_market_side: Dict[str, str] = {c: "LONG" for c in self._coins}
        self._trail_active: Dict[str, bool] = {c: False for c in self._coins}
        self._trail_extreme: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._wait_armed: Dict[str, bool] = {c: False for c in self._coins}
        self._pending_reenter: Dict[str, Optional[str]] = {c: None for c in self._coins}
        self._last_exit_px: Dict[str, Optional[float]] = {c: None for c in self._coins}
        self._last_exit_side: Dict[str, Optional[str]] = {c: None for c in self._coins}

        self._trading_active = False
        self._wallet_ok_cached = False
        self._last_status_log = 0.0
        self._quote_skip = ""
        self._wired_coin = None
        self._hl_free = Decimal("0")
        self._hl_equity = Decimal("0")
        self._last_hl_bal = 0.0
        self._cancelled_orphans = False
        self._fetch_hl_max_leverage()
        self._leverage_used = {c: self._leverage_for(c) for c in self._coins}
        self._lev_tier_seen = self._leverage_tier()

        sub = _subaccount() or "UNSET"
        self.logger().info(
            f"PMM Simple init | sub={sub[:12]}… coins={','.join(self._coins)} "
            f"buy_spreads={list(config.buy_spreads or [])} "
            f"TP={config.take_profit*100:.1f}% trail={config.trail_activation*100:.1f}/"
            f"{config.trail_delta*100:.1f}% NEXT IN={config.rebuy_pct*100:.1f}% "
            f"time_limit={int(config.time_limit or 0)}s"
        )

    def on_tick(self):
        self._read_bias()
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
                    "PMM Simple blocked — set a unique subaccount in Confiig "
                    "(must not be used by any other hunter)"
                )
            self._cancel_all_our_orders()
            self._write_status()
            return
        self._restore_positions_from_exchange()
        self._handle_sql_commands()
        self._cancel_exchange_orphans()
        self._refresh_hl_balances()
        active = self._active_coins()
        tier_now = self._leverage_tier()
        if tier_now != self._lev_tier_seen:
            self._lev_tier_seen = tier_now
            for c in self._coins:
                self._leverage_set[c] = False
        for coin in self._coins:
            if coin not in active:
                self._cancel_coin_orders(coin)
            if not self._leverage_set[coin]:
                self._set_leverage(coin)
        for coin in active:
            if self._is_close_pending(coin):
                continue
            if self._position_side[coin] in ("LONG", "SHORT"):
                self._check_exits(coin)
            self._maybe_trade(coin)
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
                    f"PMM Simple ready | {self.config.connector_name} sub={_subaccount() or 'UNSET'} "
                    f"wallet_ok={self._wallet_allowed()} bias={self._bias}"
                )
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

    def _ctrl_f(self, key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(_ctrl_live(SELF_ID, key, default))
        except (TypeError, ValueError):
            v = float(default)
        return max(lo, min(hi, v))

    def _trail_act(self) -> float:
        return self._ctrl_f("trail_activation", self.config.trail_activation, 0.004, 0.05)

    def _trail_dlt(self) -> float:
        """Price retrace. Trail % of ARM (8–40) or legacy price fraction."""
        raw = self._ctrl_f("trail_delta", self.config.trail_delta, 0.001, 40.0)
        act = self._trail_act()
        if raw >= 8.0:
            d = act * (raw / 100.0)
        elif raw > 0.05:
            d = act * raw
        else:
            d = raw
        return max(0.001, min(0.05, d))

    def _tp_frac(self) -> float:
        return self._ctrl_f("take_profit", self.config.take_profit, 0.008, 0.08)

    def _rebuy_frac(self) -> float:
        return self._ctrl_f("rebuy_pct", self.config.rebuy_pct, 0.0004, 0.01)

    def _margin_frac(self) -> Decimal:
        v = self._ctrl_f("margin_pct", float(self.config.margin_pct), 0.20, 1.0)
        if v > 1.0:
            v = v / 100.0
        return Decimal(str(max(0.20, min(1.0, v))))

    def _refresh_s(self) -> float:
        return self._ctrl_f("executor_refresh_time", float(self.config.executor_refresh_time or 20), 8, 90)

    def _ctrl_bool(self, key: str) -> bool:
        try:
            v = _ctrl_live(SELF_ID, key, False)
        except Exception:
            v = False
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        return str(v).strip().lower() in ("1", "true", "on", "yes")

    def _open_on_bias(self) -> bool:
        return self._ctrl_bool("open_on_bias")

    def _go_in_after_close(self) -> bool:
        return self._ctrl_bool("go_in_after_close")

    def _rebuy_px(self) -> Optional[float]:
        """Absolute NEXT IN from chart drag (frozen price)."""
        try:
            v = float(_ctrl_live(SELF_ID, "rebuy_px", 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v if v > 0 else None

    def _wait_target(self, coin: str) -> Optional[float]:
        px = self._rebuy_px()
        if px:
            return float(px)
        frac = self._rebuy_frac()
        exit_px = self._last_exit_px.get(coin)
        side = self._last_exit_side.get(coin)
        if not self._wait_armed.get(coin) or not exit_px or frac <= 0:
            return None
        if side == "SHORT":
            return float(exit_px) * (1.0 + frac)
        return float(exit_px) * (1.0 - frac)

    def _entry_wait_ok(self, coin: str) -> bool:
        """Wait until mid reaches NEXT IN, then open on BIAS.
        Works flat (chart-dragged rebuy_px) and after a WIN close (% of exit)."""
        target = self._wait_target(coin)
        if not target:
            return True
        mid = self._get_mid(coin)
        if not mid:
            return True
        bias = str(self._bias.get(coin) or "LONG").upper()
        side = self._last_exit_side.get(coin) if self._wait_armed.get(coin) else bias
        if side == "SHORT":
            hit = float(mid) >= float(target)
        else:
            hit = float(mid) <= float(target)
        if hit:
            self._wait_armed[coin] = False
            self.logger().info(
                f"{coin} NEXT IN hit ${target:.4f} mid=${float(mid):.4f} — open on BIAS"
            )
            return True
        why = "after WIN" if self._wait_armed.get(coin) else "flat wait"
        self._quote_skip = (
            f"{coin} NEXT IN wait ${target:.2f} (mid ${float(mid):.2f}) — {why}"
        )
        return False

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
        self._cancel_all_our_orders()
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
            self._sql_done(cmd, "wallet blocked")
            return
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
                f"Force open {coin} skipped — set LONG or SHORT first (got {side or 'FLAT'})"
            )
            self._sql_done(cmd, "need long/short")
            return
        self._restore_positions_from_exchange(force=True)
        if self._position_side[coin] is not None:
            self.logger().info(f"Force open {coin} skipped — already {self._position_side[coin]}")
            self._sql_done(cmd, "already open")
            return
        if not self._leverage_set[coin]:
            self._set_leverage(coin)
        self._pending_market_open[coin] = float(pct)
        self._pending_market_side[coin] = side
        self._cancel_coin_orders(coin)
        self.logger().info(f"Dashboard FORCE OPEN {side} {coin} at {pct*100:.0f}% leftover")
        self._enter_long_market(coin, Decimal(str(pct)), reason=f"dashboard {pct*100:.0f}%", side=side)
        if self._position_side[coin] == side:
            self._pending_market_open[coin] = 0.0
        self._sql_done(cmd)

    def _get_mid(self, coin: str) -> Optional[Decimal]:
        try:
            p = self.connectors[self.config.connector_name].get_mid_price(_pair_for(coin))
            return Decimal(str(p)) if p else None
        except Exception:
            return None

    def _active_coins(self) -> List[str]:
        coin = None
        try:
            with open("/home/hummingbot/conf/wire_runtime.json", "r") as f:
                data = json.load(f) or {}
            raw = str(((data.get("bots") or {}).get(SELF_ID) or {}).get("coin") or "").upper()
            raw = raw.replace("-USD", "").replace("-USDT", "")
            if raw in self._coins:
                coin = raw
        except Exception:
            coin = None
        self._wired_coin = coin
        if coin:
            return [coin]
        return [c for c in self._coins if self._bias.get(c) in ("LONG", "SHORT")]

    def _cancel_exchange_orphans(self):
        if self._cancelled_orphans or not self._wallet_allowed():
            return
        self._cancelled_orphans = True
        try:
            conn = self.connectors[self.config.connector_name]
            if hasattr(conn, "cancel_all"):
                safe_ensure_future(conn.cancel_all(20.0))
                self.logger().info("Cancel leftover exchange orders (BTC/ETH orphans from last session)")
        except Exception as e:
            self.logger().warning(f"cancel_all failed: {e}")

    def _refresh_hl_balances(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_hl_bal < 8:
            return
        self._last_hl_bal = now
        addr = _subaccount()
        if not addr:
            return
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({"type": "clearinghouseState", "user": addr}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode()) or {}
            ms = data.get("marginSummary") if isinstance(data.get("marginSummary"), dict) else {}
            eq = Decimal(str(ms.get("accountValue") or data.get("accountValue") or "0"))
            free = Decimal(str(data.get("withdrawable") or "0"))
            self._hl_equity = eq
            self._hl_free = free
        except Exception:
            pass

    def _connector_free(self) -> Decimal:
        conn = self.connectors[self.config.connector_name]
        for asset in ("USD", "USDC", "USDT"):
            try:
                bal = conn.get_available_balance(asset)
                if bal and Decimal(str(bal)) > 0:
                    return Decimal(str(bal))
            except Exception:
                continue
        return Decimal("0")

    def _get_free_usd(self) -> Decimal:
        conn_free = self._connector_free()
        if self._hl_free > conn_free:
            raw = self._hl_free
        elif conn_free > 0:
            raw = conn_free
        else:
            raw = self._hl_free
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
                    return max(Decimal(str(bal)), self._hl_equity)
            except Exception:
                continue
        if self._hl_equity > 0:
            return self._hl_equity
        return self._get_free_usd()

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

    def _quantize_price(self, coin: str, price: Decimal) -> Decimal:
        pair = _pair_for(coin)
        try:
            conn = self.connectors[self.config.connector_name]
            if hasattr(conn, "quantize_order_price"):
                q = conn.quantize_order_price(pair, price)
                if q and Decimal(str(q)) > 0:
                    return Decimal(str(q))
        except Exception:
            pass
        return price.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def _spreads(self) -> List[float]:
        raw = self.config.buy_spreads or [0.002, 0.004]
        out = []
        for s in raw:
            try:
                v = float(s)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out.append(v)
        return out or [0.002]

    def _amount_pcts(self) -> List[Decimal]:
        spreads = self._spreads()
        raw = list(self.config.buy_amounts_pct or [])
        if len(raw) != len(spreads):
            raw = [1.0] * len(spreads)
        vals = []
        for x in raw:
            try:
                vals.append(Decimal(str(x)))
            except Exception:
                vals.append(Decimal("1"))
        total = sum(vals) or Decimal("1")
        return [v / total for v in vals]

    def _active_orders_for(self, coin: str):
        pair = _pair_for(coin)
        out = []
        try:
            for order in self.get_active_orders(self.config.connector_name) or []:
                tp = str(getattr(order, "trading_pair", "") or "")
                if tp == pair:
                    out.append(order)
        except Exception:
            pass
        return out

    def _cancel_coin_orders(self, coin: str):
        pair = _pair_for(coin)
        for order in self._active_orders_for(coin):
            oid = getattr(order, "client_order_id", None)
            if not oid:
                continue
            try:
                self.cancel(self.config.connector_name, pair, oid)
            except Exception as e:
                self.logger().warning(f"cancel {coin} {oid} failed: {e}")

    def _cancel_all_our_orders(self):
        for coin in self._coins:
            self._cancel_coin_orders(coin)

    def _maybe_trade(self, coin: str):
        if not self._wallet_allowed():
            return
        pos = self._position_side[coin]
        if pos in ("LONG", "SHORT"):
            self._pending_market_open[coin] = 0.0
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
            self._quote_skip = ""
            return
        pending = float(self._pending_market_open.get(coin) or 0)
        if pending > 0:
            self._wait_armed[coin] = False
            side = str(self._pending_market_side.get(coin) or "LONG").upper()
            if side not in ("LONG", "SHORT"):
                side = "LONG"
            self._cancel_coin_orders(coin)
            self._enter_long_market(
                coin, Decimal(str(pending)),
                reason=f"dashboard {pending*100:.0f}% retry",
                side=side,
            )
            if self._position_side[coin] == side:
                self._pending_market_open[coin] = 0.0
            else:
                verb = "buy" if side == "LONG" else "sell"
                self._quote_skip = f"{coin} waiting to market-{verb} {pending*100:.0f}% leftover"
            return
        reenter = self._pending_reenter.get(coin)
        if reenter:
            self._pending_reenter[coin] = None
            self._wait_armed[coin] = False
            if reenter in ("LONG", "SHORT"):
                self._enter_long_market(
                    coin, Decimal("1"),
                    reason=f"go-in-after-close {reenter}",
                    side=reenter,
                )
            return
        bias = self._bias.get(coin) or "FLAT"
        if bias == "FLAT":
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
            self._quote_skip = f"{coin} FLAT — idle (no auto close)"
            return
        if self._open_on_bias():
            self._wait_armed[coin] = False
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
                self._quote_skip = f"{coin} Open on BIAS — cancel rest then market {bias}"
                return
            self._enter_long_market(
                coin, self._margin_frac(),
                reason=f"open-on-bias {bias}",
                side=bias,
            )
            return
        if bias != "LONG":
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
            self._quote_skip = f"{coin} {bias} — idle (check Open on BIAS to market-fill)"
            return
        if not self._entry_wait_ok(coin):
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
            return
        self._maintain_buy_quotes(coin)

    def _enter_long_market(self, coin: str, margin_pct: Decimal, reason: str, side: str = "LONG"):
        """Cancel rest bids and market-open this fraction of leftover perp USD on BIAS."""
        side = str(side or "LONG").upper()
        if side not in ("LONG", "SHORT"):
            return
        if self._position_side[coin] is not None:
            return
        if self._active_orders_for(coin):
            self._cancel_coin_orders(coin)
            self._quote_skip = f"{coin} canceling rest bids before market long"
            return
        mid = self._get_mid(coin)
        if not mid or mid <= 0:
            self.logger().warning(f"Skip {coin} market long — no mid")
            self._quote_skip = f"{coin} no mid — wait"
            return
        self._refresh_hl_balances(force=True)
        free = self._get_free_usd()
        equity = self._get_equity_usd()
        # Rest bids lock withdrawable until the cancel hits. Equity is the leftover
        # when we are flat and just cancelled our own quotes.
        if free < self.config.min_margin_usd and equity > free and self._position_side[coin] is None:
            free = equity
        pct = margin_pct if margin_pct and margin_pct > 0 else Decimal("1")
        if pct > 1:
            pct = Decimal("1")
        margin = (free * pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self._quote_skip = (
                f"perp free ${float(free):.2f} < min ${float(self.config.min_margin_usd):.0f} "
                "— send USD to this sub on Hyperliquid perps"
            )
            self.logger().info(f"Skip {coin} market long | {self._quote_skip}")
            return
        lev = Decimal(str(self._leverage_for(coin)))
        min_n = Decimal(str(self.config.min_notional_usd))
        notional = margin * lev
        if notional < min_n:
            needed = (min_n / lev).quantize(Decimal("0.01"))
            if needed > free * Decimal("0.80"):
                self._quote_skip = (
                    f"notional ${float(notional):.2f} < min ${float(min_n):.0f} "
                    f"(free ${float(free):.2f} × {int(lev)}x). Add perp USD."
                )
                self.logger().info(f"Skip {coin} market long | {self._quote_skip}")
                return
            notional = min_n
            margin = needed
        amount = self._quantize_amount(coin, notional / mid)
        if amount <= 0:
            return
        max_margin = free * pct
        if lev > 0 and amount * mid / lev > max_margin:
            amount = self._quantize_amount(coin, (max_margin * lev) / mid)
            if amount <= 0:
                return
        pair = _pair_for(coin)
        now = self.current_timestamp
        self._last_entry_attempt[coin] = now
        self._quote_skip = ""
        self.logger().info(
            f"PMM MARKET {side} {pair} | {reason} | leftover {float(pct)*100:.0f}% "
            f"amount={amount} mid=${float(mid):.4f} margin=${float(margin):.2f} lev={int(lev)}x"
        )
        try:
            if side == "SHORT":
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            else:
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            self._position_side[coin] = side
            self._entry_price[coin] = mid
            self._amount[coin] = amount
            self._entry_time[coin] = now
            self._log_trade({
                "action": "OPEN", "coin": coin, "side": side, "amount": float(amount),
                "price": float(mid), "leverage": int(lev), "reason": str(reason),
            })
            self._write_status()
        except Exception as e:
            self._position_side[coin] = None
            self._entry_price[coin] = None
            self._amount[coin] = None
            self._entry_time[coin] = None
            self.logger().warning(f"PMM market {side} {coin} failed: {e}")
            self._quote_skip = f"market {side} failed: {e}"

    def _maintain_buy_quotes(self, coin: str):
        now = self.current_timestamp
        refresh = self._refresh_s()
        existing = self._active_orders_for(coin)
        if existing and now - float(self._last_refresh.get(coin) or 0) < refresh:
            self._quote_skip = f"{coin} {len(existing)} pmm bid(s) resting"
            return
        if now - float(self._last_closed.get(coin) or 0) < float(self.config.cooldown_time or 0):
            self._quote_skip = f"{coin} cooldown after close"
            return
        if now - float(self._last_entry_attempt.get(coin) or 0) < 8:
            return
        mid = self._get_mid(coin)
        if not mid or mid <= 0:
            self._quote_skip = f"{coin} no mid — wait"
            return
        self._refresh_hl_balances(force=True)
        free = self._get_free_usd()
        pct = self._margin_frac()
        margin = (free * pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self._quote_skip = (
                f"perp free ${float(free):.2f} < min ${float(self.config.min_margin_usd):.0f} "
                "— send USD to this sub on Hyperliquid perps (spot USDC does not trade)"
            )
            if now - self._last_status_log >= 25:
                self._last_status_log = now
                self.logger().info(f"Skip {coin} pmm bids | {self._quote_skip}")
            return
        lev = Decimal(str(self._leverage_for(coin)))
        min_n = Decimal(str(self.config.min_notional_usd))
        notional = margin * lev
        if notional < min_n:
            needed = (min_n / lev).quantize(Decimal("0.01"))
            if needed > free * Decimal("0.80"):
                self._quote_skip = (
                    f"notional ${float(notional):.2f} < min ${float(min_n):.0f} "
                    f"(free ${float(free):.2f} × {int(lev)}x). Add perp USD."
                )
                return
            notional = min_n
            margin = needed
        spreads = self._spreads()[:1] or [0.002]
        pair = _pair_for(coin)
        if existing:
            self._cancel_coin_orders(coin)
        self._last_entry_attempt[coin] = now
        self._last_refresh[coin] = now
        placed = 0
        for i, spread in enumerate(spreads):
            frac = Decimal("1")
            px = self._quantize_price(coin, mid * (Decimal("1") - Decimal(str(spread))))
            if not px or px <= 0:
                continue
            amount = self._quantize_amount(coin, (notional * frac) / px)
            if amount <= 0:
                continue
            try:
                self.buy(
                    self.config.connector_name, pair, amount,
                    OrderType.LIMIT, px, PositionAction.OPEN,
                )
                placed += 1
                self.logger().info(
                    f"PMM BID {pair} lv{i} spread={float(spread)*100:.2f}% "
                    f"amt={amount} px=${float(px):.4f} mid=${float(mid):.4f}"
                )
            except Exception as e:
                self.logger().warning(f"PMM bid {coin} lv{i} failed: {e}")
        if placed:
            self._quote_skip = f"{coin} {placed} pmm bid(s) at buy_spreads"
            if not existing:
                self.logger().info(
                    f"PMM Simple quoting LONG {pair} | {placed} bid(s) "
                    f"margin=${float(margin):.2f} leftover {float(pct)*100:.0f}% lev={int(lev)}x"
                )
        else:
            self._quote_skip = f"{coin} no pmm bids placed"

    def _is_close_pending(self, coin: str) -> bool:
        until = float(self._close_pending_until.get(coin) or 0)
        return until > 0 and self.current_timestamp < until

    def _restore_positions_from_exchange(self, force: bool = False):
        if not self._wallet_allowed():
            return
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
                        if side == "LONG" and prev is None:
                            self._log_trade({
                                "action": "OPEN", "coin": coin, "side": "LONG",
                                "amount": float(abs(amt)),
                                "price": float(self._entry_price[coin] or 0),
                                "reason": "pmm_simple bid fill",
                            })
            for coin in self._coins:
                if self._position_side[coin] is not None and not found[coin]:
                    et = self._entry_time[coin]
                    if et and (self.current_timestamp - et) < 20:
                        continue
                    self.logger().info(f"{coin} flat on exchange — clearing maker state")
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
        if age >= 5:
            roe = self._calc_roe_pct(coin, mid)
            sl = float(_live_sl_roe(20.0))
            if roe is not None and roe <= -sl:
                self._close_position(coin, f"SL ROE {roe:.2f}%")
                return
            tp = self._tp_frac()
            if side == "LONG" and px >= entry * (1.0 + tp):
                self._close_position(coin, f"TP +{tp*100:.2f}%")
                return
            if side == "SHORT" and px <= entry * (1.0 - tp):
                self._close_position(coin, f"TP +{tp*100:.2f}%")
                return
        act = self._trail_act()
        delta = self._trail_dlt()
        trail_on = self._trail_active[coin]
        extreme = self._trail_extreme[coin]
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
            self._trail_active[coin] = True
            self._trail_extreme[coin] = extreme
        limit = int(self.config.time_limit or 0)
        if limit > 0 and et and (self.current_timestamp - et) >= limit:
            self._close_position(coin, f"TIME {limit}s")
            return

    def _clear_pos(self, coin: str):
        self._position_side[coin] = None
        self._entry_price[coin] = None
        self._amount[coin] = None
        self._entry_time[coin] = None
        self._trail_active[coin] = False
        self._trail_extreme[coin] = None

    def _close_position(self, coin: str, reason: str):
        if not self._wallet_allowed():
            self.logger().warning(f"Close blocked {coin} ({reason}) — unique subaccount not set")
            return
        self._cancel_coin_orders(coin)
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
            win_close = str(reason).startswith("TP") or str(reason).startswith("TRAIL")
            if win_close and exit_px:
                self._wait_armed[coin] = True
                self._last_exit_px[coin] = float(exit_px)
                self._last_exit_side[coin] = side
            else:
                self._wait_armed[coin] = False
        except Exception as e:
            self.logger().error(f"Close failed {coin}: {e}")
            return
        self._close_pending_until[coin] = self.current_timestamp + 45
        self._last_closed[coin] = self.current_timestamp
        self._clear_pos(coin)
        user_close = "dashboard force close" in str(reason)
        if not user_close and self._go_in_after_close():
            bnow = str(self._bias.get(coin) or "FLAT").upper()
            if bnow in ("LONG", "SHORT"):
                self._wait_armed[coin] = False
                self._pending_reenter[coin] = bnow
                self._close_pending_until[coin] = self.current_timestamp + 4
                self.logger().info(
                    f"{coin} go-in-after-close armed {bnow} 100% leftover (skip NEXT IN)"
                )
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        nq = 0
        try:
            nq = len(self.get_active_orders(self.config.connector_name) or [])
        except Exception:
            pass
        lines = [
            f"PMM Simple | sub={(_subaccount() or 'UNSET')[:12]}… | "
            f"ok={self._wallet_ok_cached} perp=${float(self._get_equity_usd()):.2f} "
            f"free=${float(free):.2f} quotes={nq}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            qn = len(self._active_orders_for(coin))
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} quotes={qn} "
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
        try:
            self._active_coins()
        except Exception:
            pass
        nq = 0
        try:
            nq = len(self.get_active_orders(self.config.connector_name) or [])
        except Exception:
            pass
        if not self._wallet_ok_cached:
            payload = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "ready": self._trading_active,
                "wallet_ok": False,
                "controller": "market_making.pmm_simple",
                "connector": self.config.connector_name,
                "subaccount": _subaccount() or None,
                "coins": list(self._coins),
                "pairs": [_pair_for(c) for c in self._coins],
                "equity_usd": 0.0,
                "free_usd": 0.0,
                "open_upnl_usd": 0.0,
                "n_quotes": 0,
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
        for coin in self._coins:
            mid = self._get_mid(coin)
            side = self._position_side[coin]
            roe = self._calc_roe_pct(coin, mid) if mid and side else None
            positions[coin] = {
                "pair": _pair_for(coin),
                "bias": self._bias.get(coin),
                "position_side": side,
                "entry_price": _as_float(self._entry_price[coin]),
                "amount": _as_float(self._amount[coin]),
                "mid_price": _as_float(mid),
                "roe_pct": round(roe, 3) if roe is not None else None,
                "n_quotes": len(self._active_orders_for(coin)),
                "trail_active": bool(self._trail_active.get(coin)),
                "trail_extreme": self._trail_extreme.get(coin),
                "wait_armed": bool(self._wait_armed.get(coin)),
                "wait_px": self._wait_target(coin),
                "leverage": int(self._leverage_used[coin] or self._leverage_for(coin)),
            }
        log = self._read_trade_log()
        summary = self._summarize_trades(list(log.get("trades") or []))
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "ready": self._trading_active,
            "wallet_ok": self._wallet_ok_cached,
            "controller": "market_making.pmm_simple",
            "connector": self.config.connector_name,
            "subaccount": _subaccount() or None,
            "coins": list(self._coins),
            "pairs": [_pair_for(c) for c in self._coins],
            "leverage": {c: self._leverage_for(c) for c in self._coins},
            "buy_spreads": self._spreads(),
            "sell_spreads": list(self.config.sell_spreads or []),
            "take_profit": self._tp_frac(),
            "trail_activation": self._trail_act(),
            "trail_delta": self._trail_dlt(),
            "rebuy_pct": self._rebuy_frac(),
            "open_on_bias": self._open_on_bias(),
            "go_in_after_close": self._go_in_after_close(),
            "executor_refresh_time": int(self.config.executor_refresh_time),
            "sl_roe_pct": float(_live_sl_roe(20.0)),
            "time_limit_s": int(self.config.time_limit or 0),
            "equity_usd": _as_float(self._get_equity_usd()) or 0.0,
            "free_usd": _as_float(self._get_free_usd()) or 0.0,
            "perp_usd": _as_float(self._get_equity_usd()) or 0.0,
            "open_upnl_usd": self._open_upnl_usd(),
            "n_quotes": nq,
            "wired_coin": self._wired_coin,
            "quote_skip": self._quote_skip or "",
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
            try_status_put(SELF_ID, payload)
        except Exception:
            pass
