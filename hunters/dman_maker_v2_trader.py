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
dman_maker_v2_trader.py
=======================
Hyperliquid hunter (unique subaccount).

LONG + flat  → market-buy leftover perp USD (does not wait for a dip).
SHORT + flat → market-sell leftover perp USD (same cycle as long).
LONG/SHORT + in → hold; exit on Home SL / take-profit / trail, then auto-reenter on the same BIAS.
OPEN 100%    → market-open leftover perp USD on the current BIAS (LONG or SHORT).
Never flattens an already-open clip.

Does not trade until Confiig has a unique subaccount (not used by any other hunter).
"""

from __future__ import annotations

import calendar
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
BIAS_FILE = "/home/hummingbot/conf/dman_maker_bias.json"
STATUS_FILE = "/home/hummingbot/conf/dman_maker_status.json"
TRADE_LOG_FILE = "/home/hummingbot/conf/dman_maker_trades.json"
ACCOUNTS_FILE = "/home/hummingbot/conf/bot_accounts.json"

MAKER_COINS = ("BTC", "ETH", "LTC", "TAO")
VALID_BIAS = ("LONG", "SHORT", "FLAT")
SELF_ID = "hummingbot-dman-maker"
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


class DmanMakerV2TraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    connector_name: str = Field("hyperliquid_perpetual")
    coins: List[str] = Field(default_factory=lambda: list(MAKER_COINS))
    leverage: int = Field(40)
    leverage_by_coin: Dict[str, int] = Field(default_factory=lambda: {"BTC": 40, "ETH": 25, "LTC": 10})
    use_exchange_max_leverage: bool = Field(True)

    margin_pct: float = Field(1.0)
    min_margin_usd: Decimal = Field(Decimal("1"))
    min_notional_usd: Decimal = Field(Decimal("10"))

    dca_spreads: List[float] = Field(default_factory=lambda: [0.002, 0.004, 0.008, 0.012])
    dca_amounts: List[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    executor_refresh_time: int = Field(30)
    take_profit: float = Field(0.02)
    stop_loss: float = Field(0.03)
    trail_activation: float = Field(0.015)
    trail_delta: float = Field(0.005)
    time_limit: int = Field(0)
    cooldown_time: int = Field(15)
    # After 2 consecutive closes with ROE ≥ this, wait wait_s OR a dip of dip_roe at used leverage.
    double_win_roe_pct: float = Field(20.0)
    double_win_wait_s: int = Field(1800)
    double_win_dip_roe_pct: float = Field(30.0)
    dontGoCrazy: bool = Field(True)
    afterLossNoRebuy: bool = Field(True)
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


class DmanMakerV2Trader(StrategyV2Base):
    """DMan Maker. 100% follows current BIAS; never flips an open clip."""

    def __init__(self, connectors: Dict[str, ConnectorBase], config: DmanMakerV2TraderConfig):
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
        self._hl_pos_leverage: Dict[str, int] = {}
        self._hl_pos_seen = False
        self._lev_tier_seen = "MAX"
        self._close_pending_until: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_closed: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_refresh: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_entry_attempt: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._pending_entry: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._last_long_arm: Dict[str, float] = {c: 0.0 for c in self._coins}
        self._trail_active: Dict[str, bool] = {c: False for c in self._coins}
        self._trail_extreme: Dict[str, Optional[float]] = {c: None for c in self._coins}

        self._trading_active = False
        self._wallet_ok_cached = False
        self._last_status_log = 0.0
        self._quote_skip = ""
        self._wired_coin = None
        self._hl_free = Decimal("0")
        self._hl_equity = Decimal("0")
        self._last_hl_bal = 0.0
        self._rest_mids: Dict[str, Decimal] = {}
        self._last_mids = 0.0
        self._cancelled_orphans = False
        self._hl_action_block_until = 0.0
        self._fetch_hl_max_leverage()
        self._leverage_used = {c: self._leverage_for(c) for c in self._coins}
        self._lev_tier_seen = self._leverage_tier()

        sub = _subaccount() or "UNSET"
        self.logger().info(
            f"DMan Maker UP-ONLY init | sub={sub[:12]}… coins={','.join(self._coins)} "
            f"TP={config.take_profit*100:.1f}% trail={config.trail_activation*100:.1f}/"
            f"{config.trail_delta*100:.1f}% time_limit={int(config.time_limit or 0)}s"
        )
        self.logger().info("Dashboard open finishes on the first attempt — the queue does not wait on the fill")

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
                    "DMan Maker blocked — set a unique subaccount in Confiig "
                    "(must not be Copy / Swing / Helliot / Surfn / Shorty / Dman / Only Up)"
                )
            self._cancel_all_our_orders()
            self._write_status()
            return
        # Read the dashboard command before the balance REST call. That call
        # can block the tick for several seconds and was delaying every click.
        self._handle_sql_commands()
        self._refresh_hl_balances()
        self._restore_positions_from_exchange()
        self._sync_risk_closes()
        close_busy = self._any_close_pending()
        blocked = self._hl_blocked()
        if not close_busy and not blocked:
            self._cancel_exchange_orphans()
        active = self._active_coins()
        tier_now = self._leverage_tier()
        if tier_now != self._lev_tier_seen:
            self._lev_tier_seen = tier_now
            for c in self._coins:
                self._leverage_set[c] = False
        for coin in self._coins:
            if coin not in active:
                if not close_busy and not blocked:
                    self._cancel_coin_orders(coin)
            if not self._leverage_set[coin] and not close_busy and not blocked:
                self._set_leverage(coin)
        for coin in active:
            if self._is_close_pending(coin) or blocked:
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

    def _fetch_rest_mids(self):
        now = time.time()
        if now - float(self._last_mids or 0) < 8.0:
            return
        self._last_mids = now
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({"type": "allMids"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode()) or {}
            if not isinstance(data, dict):
                return
            out = {}
            for k, v in data.items():
                coin = str(k or "").upper()
                if coin not in self._coins:
                    continue
                try:
                    px = Decimal(str(v))
                except Exception:
                    continue
                if px > 0:
                    out[coin] = px
            if out:
                self._rest_mids = out
        except Exception:
            pass

    def _check_ready(self):
        try:
            connector = self.connectors[self.config.connector_name]
            if connector.ready:
                self._trading_active = True
                self.logger().info(
                    f"DMan Maker ready | {self.config.connector_name} sub={_subaccount() or 'UNSET'} "
                    f"wallet_ok={self._wallet_allowed()} bias={self._bias}"
                )
                return
            self._fetch_rest_mids()
            if self._wallet_allowed() and any(self._rest_mids.get(c) for c in self._coins):
                self._trading_active = True
                self.logger().info(
                    f"DMan Maker REST-ready (book WS not up) | mids={list(self._rest_mids)} "
                    f"sub={_subaccount() or 'UNSET'} bias={self._bias}"
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
        raw = str(_ctrl_live(SELF_ID, "leverage_tier", "MAX") or "MAX").strip().upper()
        if raw not in ("MAX", "MID", "LOW"):
            return "MAX"
        return raw

    def _hl_cap(self, coin: str) -> int:
        hl = int(self._hl_max_leverage.get(coin) or 0)
        if hl > 0:
            return hl
        by_coin = self.config.leverage_by_coin or {}
        cfg = int(by_coin.get(coin) or self.config.leverage or 40)
        return max(1, cfg)

    def _leverage_for(self, coin: str) -> int:
        cap = self._hl_cap(coin)
        tier = self._leverage_tier()
        if tier == "LOW":
            return max(1, cap // 4)
        if tier == "MID":
            return max(1, cap // 2)
        by_coin = self.config.leverage_by_coin or {}
        cfg = int(by_coin.get(coin) or self.config.leverage or 40)
        hl = int(self._hl_max_leverage.get(coin) or 0)
        if getattr(self.config, "use_exchange_max_leverage", True) and hl > 0:
            return hl
        if hl > 0:
            return max(1, min(cfg, hl))
        return max(1, cfg)

    def _pos_leverage(self, coin: str) -> int:
        side = self._position_side.get(coin)
        live = int(self._hl_pos_leverage.get(coin) or 0)
        if side in ("LONG", "SHORT") and live > 0:
            return live
        used = int(self._leverage_used.get(coin) or 0)
        if used > 0:
            return used
        return int(self._leverage_for(coin))

    def _hl_blocked(self) -> bool:
        return time.time() < float(self._hl_action_block_until or 0)

    def _any_close_pending(self) -> bool:
        return any(self._is_close_pending(c) for c in self._coins)

    def _note_hl_action_fail(self, msg: str, coin: str = "") -> bool:
        text = str(msg or "")
        quota = "too many cumulative" in text.lower() or "cumulative requests" in text.lower()
        is_429 = "429" in text or "HTTP status is 429" in text
        if quota:
            until = time.time() + 12.0
            self._hl_action_block_until = max(float(self._hl_action_block_until or 0), until)
            self._quote_skip = f"{coin} HL address quota — 1 action / 10s".strip()
            return True
        if is_429:
            until = time.time() + 8.0
            self._hl_action_block_until = max(float(self._hl_action_block_until or 0), until)
            self._quote_skip = f"{coin} HL 429 — backing off".strip()
            return True
        return False

    def _set_leverage(self, coin: str):
        # An open coin is not marked set. The tier applies once that coin is flat.
        if not self._hl_pos_seen or self._position_side[coin] is not None:
            return
        if self._any_close_pending() or self._hl_blocked():
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
            self._note_hl_action_fail(str(e), coin)
            self.logger().warning(f"set_leverage {coin} failed (retry): {e}")

    def _ctrl_f(self, key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(_ctrl_live(SELF_ID, key, default))
        except (TypeError, ValueError):
            v = float(default)
        return max(lo, min(hi, v))

    def _trail_act(self) -> float:
        return self._ctrl_f("trail_activation", self.config.trail_activation, 0.001, 0.20)

    def _trail_dlt(self) -> float:
        return self._ctrl_f("trail_delta", self.config.trail_delta, 0.001, 0.10)

    def _tp_frac(self) -> float:
        return self._ctrl_f("take_profit", self.config.take_profit, 0.001, 0.50)

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
                    if sql[c] in ("LONG", "SHORT"):
                        self._last_long_arm[c] = time.time()
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
                    if parsed[c] in ("LONG", "SHORT"):
                        self._last_long_arm[c] = time.time()
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

    def _cmd_age_s(self, cmd) -> float:
        raw = str((cmd or {}).get("created_at") or "").replace(" UTC", "").strip()
        try:
            return max(0.0, time.time() - calendar.timegm(time.strptime(raw, "%Y-%m-%d %H:%M:%S")))
        except Exception:
            return 999.0

    def _handle_sql_commands(self):
        if not self._trading_active:
            return
        cmd = self._sql_cmd(("close", "open", "scale"))
        if not cmd:
            return
        kind = str(cmd.get("kind") or "")
        if kind == "close":
            self._run_force_close(cmd)
        elif kind == "open":
            self._run_force_open(cmd)
        elif kind == "scale":
            self._run_force_scale(cmd)

    def _run_force_close(self, cmd):
        if not self._wallet_allowed():
            self.logger().warning("Force close blocked — unique subaccount not set")
            self._sql_done(cmd, "no wallet")
            return
        payload = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        try:
            from matrix_store import close_coin_list
            targets = close_coin_list(payload, self._coins)
        except Exception:
            targets = list(self._coins)
        self._restore_positions_from_exchange(force=True)
        if not self._hl_blocked():
            self._cancel_all_our_orders()
        still = []
        for coin in targets:
            if self._position_side.get(coin) is None:
                continue
            if self._is_close_pending(coin) or self._hl_blocked():
                still.append(coin)
                continue
            self.logger().info(f"Dashboard FORCE CLOSE {coin}")
            self._close_position(coin, "dashboard force close")
            still.append(coin)
        if still:
            # A fill usually lands in a few seconds. Past that, leave the
            # command so the next click is not stuck behind this one.
            if self._cmd_age_s(cmd) < 12:
                return
            self._sql_done(cmd, "close sent")
            return
        self._sql_done(cmd)

    def _run_force_open(self, cmd):
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        coin = str(data.get("coin") or "").upper()
        if coin not in self._coins:
            self._sql_done(cmd, "invalid coin")
            return
        self._restore_positions_from_exchange(force=True)
        side = str(data.get("side") or self._bias.get(coin) or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(f"Force open {coin} skipped — set LONG or SHORT first")
            self._sql_done(cmd, "need long/short")
            return
        have = self._position_side.get(coin)
        if have and have != side:
            self.logger().info(
                f"Force open {coin} skipped — already {have}, will not flip to {side}"
            )
            self._sql_done(cmd, "already opposite")
            return
        if have == side:
            self.logger().info(f"100% leftover → scale existing {side} {coin}")
            self._run_force_scale(cmd)
            return
        if self._open_busy(except_coin=coin):
            self.logger().info(f"Force open {coin} skipped — another coin already using leftover")
            self._sql_done(cmd, "other clip")
            return
        if self._hl_blocked():
            self.logger().info(f"Force open {coin} wait — HL address slot busy")
            if self._cmd_age_s(cmd) < 8:
                return
            self._sql_done(cmd, "hl busy")
            return
        self._last_entry_attempt[coin] = 0
        self._last_closed[coin] = 0
        self.logger().info(f"Dashboard FORCE OPEN {side} {coin} 100% leftover")
        before = float(self._pending_entry.get(coin) or 0)
        self._enter_long(coin, side=side, force=True)
        after = float(self._pending_entry.get(coin) or 0)
        if after > before:
            self._sql_done(cmd, "submitted")
            return
        # An order is already in flight. Do not send another when the 25s
        # pending window expires — that stacked a market buy every 25s.
        if after > 0 and (self._pending_age(coin) or 0) < 25:
            self._sql_done(cmd, "already pending")
            return
        skip = self._quote_skip or ""
        if self._cmd_age_s(cmd) < 5 and ("canceling" in skip or "no mid" in skip):
            return
        free = self._tradable_free_usd()
        if free < self.config.min_margin_usd:
            self._sql_done(cmd, "no leftover")
            return
        self._sql_done(cmd, (skip or "not sent")[:80])

    def _tradable_free_usd(self) -> Decimal:
        """Withdrawable or equity−margin, after RISK park. Does not close."""
        self._refresh_hl_balances(force=True)
        wd = self._connector_free()
        if self._hl_free > wd:
            wd = self._hl_free
        eq = self._get_equity_usd()
        used = Decimal("0")
        for coin in self._coins:
            side = self._position_side.get(coin)
            amt = self._amount.get(coin)
            entry = self._entry_price.get(coin)
            if not side or not amt or not entry:
                continue
            lev = Decimal(str(self._leverage_for(coin) or 1))
            if lev <= 0:
                continue
            try:
                used += Decimal(str(amt)) * Decimal(str(entry)) / lev
            except Exception:
                continue
        est = eq - used
        if est < 0:
            est = Decimal("0")
        # Withdrawable is leftover on HL. Do not take max(wd, est) — after a
        # fill, local used-margin lags and est stays fat, which would 100% twice.
        if wd > 0 and est > 0:
            raw = wd if wd < est else est
        elif wd > 0:
            raw = wd
        else:
            raw = est
        try:
            from risk_park import deduct
            return deduct(SELF_ID, raw)
        except Exception:
            return raw

    def _run_force_scale(self, cmd):
        """Market-add ALL leftover perp USD to the open clip. Does not close."""
        if not self._wallet_allowed():
            self.logger().warning("Full scale blocked — unique subaccount not set")
            self._sql_done(cmd, "no wallet")
            return
        data = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
        coin = str(data.get("coin") or "").upper()
        if coin not in self._coins:
            self._sql_done(cmd, "invalid coin")
            return
        self._restore_positions_from_exchange(force=True)
        side = self._position_side.get(coin)
        want = str(data.get("side") or side or "").upper()
        if side not in ("LONG", "SHORT"):
            self.logger().info(f"Full scale {coin} skipped — need an open clip (have {side or 'FLAT'})")
            self._sql_done(cmd, "need open clip")
            return
        if want in ("LONG", "SHORT") and want != side:
            self.logger().info(f"Full scale {coin} skipped — clip is {side}, not {want}")
            self._sql_done(cmd, "already opposite")
            return
        free = self._tradable_free_usd()
        dust = Decimal("0.50")
        margin = (free - dust) if free > Decimal("1") else free
        margin = margin.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self.logger().info(
                f"Full scale {coin} done — tradable ${float(free):.2f} < min "
                f"${float(self.config.min_margin_usd):.0f}"
            )
            self._sql_done(cmd, "no leftover")
            return
        if self._pending_age(coin) and self._pending_age(coin) < 25:
            self._sql_done(cmd, "already pending")
            return
        if self._hl_blocked():
            self.logger().info(f"Full scale {coin} wait — HL address slot busy")
            if self._cmd_age_s(cmd) < 8:
                return
            self._sql_done(cmd, "hl busy")
            return
        self._cancel_coin_orders(coin, force=True)
        mid = self._get_mid(coin)
        if not mid or mid <= 0:
            if self._cmd_age_s(cmd) < 5:
                return
            self._sql_done(cmd, "no mid")
            return
        lev = Decimal(str(self._leverage_for(coin) or 1))
        notional = margin * lev
        min_n = Decimal(str(self.config.min_notional_usd))
        if notional < min_n:
            self.logger().info(
                f"Full scale {coin} skip — notional ${float(notional):.2f} < min ${float(min_n):.0f}"
            )
            self._sql_done(cmd, "too small")
            return
        amount = self._quantize_amount(coin, notional / mid)
        if amount <= 0:
            self._sql_done(cmd, "qty 0")
            return
        pair = _pair_for(coin)
        prev = Decimal(str(self._amount.get(coin) or 0))
        self.logger().info(
            f"FULL SCALE {side} {pair} | add leftover ${float(margin):.2f} "
            f"amount=+{amount} @ ${float(mid):.4f} lev={int(lev)}x "
            f"prev={float(prev):.6f} → {float(prev + amount):.6f}"
        )
        try:
            if side == "SHORT":
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            else:
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            self._pending_entry[coin] = self.current_timestamp
            self._quote_skip = f"{coin} 100% leftover submitted — wait for fill"
            self._sql_done(cmd, "submitted")
        except Exception as e:
            self._note_hl_action_fail(str(e), coin)
            self.logger().warning(f"Full scale {coin} failed: {e}")
            self._sql_done(cmd, "failed")

    def _get_mid(self, coin: str) -> Optional[Decimal]:
        try:
            p = self.connectors[self.config.connector_name].get_mid_price(_pair_for(coin))
            if p:
                return Decimal(str(p))
        except Exception:
            pass
        self._fetch_rest_mids()
        m = self._rest_mids.get(str(coin).upper())
        return m if m and m > 0 else None

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
        if self._hl_blocked() or self._any_close_pending():
            return
        n = 0
        try:
            n = len(self.get_active_orders(self.config.connector_name) or [])
        except Exception:
            n = 0
        if n <= 0:
            self._cancelled_orphans = True
            return
        self._cancelled_orphans = True
        try:
            conn = self.connectors[self.config.connector_name]
            if hasattr(conn, "cancel_all"):
                safe_ensure_future(conn.cancel_all(20.0))
                self.logger().info("Cancel leftover exchange orders (BTC/ETH orphans from last session)")
        except Exception as e:
            self._note_hl_action_fail(str(e))
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
            self._sync_positions_from_hl(data.get("assetPositions") or [])
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
        raw = self.config.dca_spreads or [0.01, 0.02, 0.04, 0.08]
        out = []
        for s in raw:
            try:
                v = float(s)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out.append(v)
        return out or [0.01]

    def _amount_pcts(self) -> List[Decimal]:
        spreads = self._spreads()
        raw = list(self.config.dca_amounts or [])
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

    def _cancel_coin_orders(self, coin: str, force: bool = False):
        orders = self._active_orders_for(coin)
        if not orders:
            return
        if not force and (self._hl_blocked() or self._any_close_pending()):
            return
        pair = _pair_for(coin)
        for order in orders:
            oid = getattr(order, "client_order_id", None)
            if not oid:
                continue
            try:
                self.cancel(self.config.connector_name, pair, oid)
            except Exception as e:
                self._note_hl_action_fail(str(e), coin)
                self.logger().warning(f"cancel {coin} {oid} failed: {e}")

    def _cancel_all_our_orders(self):
        for coin in self._coins:
            self._cancel_coin_orders(coin)

    def _maybe_trade(self, coin: str):
        if not self._wallet_allowed() or self._hl_blocked() or self._any_close_pending():
            return
        pos = self._position_side[coin]
        if pos in ("LONG", "SHORT"):
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
            self._quote_skip = ""
            return
        if self._pending_age(coin) and self._pending_age(coin) < 25:
            self._quote_skip = f"{coin} waiting for Hyperliquid fill"
            return
        if not self._wired_coin and self._open_busy(except_coin=coin):
            return
        bias = self._bias.get(coin) or "FLAT"
        if bias not in ("LONG", "SHORT"):
            if self._active_orders_for(coin):
                self._cancel_coin_orders(coin)
            self._quote_skip = f"{coin} {bias} — idle (set LONG or SHORT to auto-enter)"
            return
        self._enter_long(coin, side=bias)

    def _close_ts_epoch(self, raw) -> float:
        s = str(raw or "").replace(" UTC", "").strip()[:19]
        try:
            return float(calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S")))
        except Exception:
            return 0.0

    def _dont_go_crazy(self) -> bool:
        return bool(_ctrl_live(SELF_ID, "dontGoCrazy", getattr(self.config, "dontGoCrazy", True)))

    def _after_loss_no_rebuy(self) -> bool:
        return bool(_ctrl_live(SELF_ID, "afterLossNoRebuy", getattr(self.config, "afterLossNoRebuy", True)))

    def _is_sl_close_reason(self, reason: str) -> bool:
        r = str(reason or "").strip().upper()
        return r.startswith("SL") or r.startswith("EXCHANGE FLAT")

    def _sl_loss_hold(self, coin: str) -> Optional[str]:
        """After an SL (or exchange-flat) close: no new clip until LONG/SHORT is set again. Does not close."""
        if not self._after_loss_no_rebuy():
            return None
        last = None
        for t in reversed(list((self._read_trade_log() or {}).get("trades") or [])):
            if not isinstance(t, dict):
                continue
            if str(t.get("action") or "").upper() != "CLOSE":
                continue
            if str(t.get("coin") or "").upper() != coin:
                continue
            last = t
            break
        if not last or not self._is_sl_close_reason(last.get("reason") or ""):
            return None
        last_ts = self._close_ts_epoch(last.get("ts"))
        armed = float(self._last_long_arm.get(coin) or 0)
        if last_ts > 0 and armed > last_ts:
            return None
        why = str(last.get("reason") or "SL")
        return f"{coin} afterLossNoRebuy — last close was {why}; set LONG or SHORT again to re-enter"

    def _double_win_hold(self, coin: str, mid: Optional[Decimal]) -> Optional[str]:
        """After two +20% ROE wins: wait 30 min or a −30% ROE dip from last exit. Does not close."""
        if not self._dont_go_crazy():
            return None
        need = float(getattr(self.config, "double_win_roe_pct", 20.0) or 20.0)
        wait_s = float(getattr(self.config, "double_win_wait_s", 1800) or 1800)
        dip_roe = float(getattr(self.config, "double_win_dip_roe_pct", 30.0) or 30.0)
        closes = []
        for t in reversed(list((self._read_trade_log() or {}).get("trades") or [])):
            if not isinstance(t, dict):
                continue
            if str(t.get("action") or "").upper() != "CLOSE":
                continue
            if str(t.get("coin") or "").upper() != coin:
                continue
            closes.append(t)
            if len(closes) >= 2:
                break
        if len(closes) < 2:
            return None
        roes = []
        for t in closes:
            try:
                roes.append(float(t.get("roe_pct")))
            except (TypeError, ValueError):
                roes.append(None)
        if any(r is None or r < need for r in roes):
            return None
        last = closes[0]
        last_ts = self._close_ts_epoch(last.get("ts"))
        now = time.time()
        elapsed = now - last_ts if last_ts > 0 else 0.0
        if elapsed >= wait_s:
            return None
        exit_px = _as_float(last.get("price"))
        lev = float(last.get("leverage") or self._leverage_for(coin) or 10) or 10.0
        dip_frac = dip_roe / 100.0 / lev
        last_side = str(last.get("side") or "LONG").upper()
        trigger = None
        if exit_px and exit_px > 0:
            px = _as_float(mid)
            if last_side == "SHORT":
                trigger = exit_px * (1.0 + dip_frac)
                if px and px >= trigger:
                    return None
            else:
                trigger = exit_px * (1.0 - dip_frac)
                if px and px <= trigger:
                    return None
        left = max(0, int(wait_s - elapsed))
        trig_s = f"${trigger:.4f}" if trigger else "n/a"
        return (
            f"{coin} two +{need:g}% ROE wins — wait {left}s or dip to {trig_s} "
            f"(−{dip_roe:g}% ROE from last exit)"
        )

    def did_fail_order(self, order_failed_event):
        pair = str(getattr(order_failed_event, "trading_pair", "") or "").upper()
        coin = pair.replace("-USD", "").replace("-USDT", "")
        msg = str(
            getattr(order_failed_event, "error_message", None)
            or getattr(order_failed_event, "error_type", None)
            or order_failed_event
        )
        if coin not in self._coins:
            return
        self._pending_entry[coin] = 0.0
        self._last_entry_attempt[coin] = self.current_timestamp
        quota = self._note_hl_action_fail(msg, coin)
        if self._is_close_pending(coin) or self._any_close_pending():
            hold = self.current_timestamp + (12.0 if quota else 8.0)
            self._close_pending_until[coin] = max(float(self._close_pending_until.get(coin) or 0), hold)
            self.logger().warning(
                f"{coin} CLOSE failed — hold slot, retry after backoff: {msg[:160]}"
            )
            return
        if quota:
            self.logger().warning(f"{coin} market blocked by HL address quota — backoff, leftover stays")
        elif "429" in msg:
            self.logger().warning(f"{coin} market open 429 — backoff, leftover stays")
        else:
            self._quote_skip = f"{coin} market failed: {msg[:120]}"
            self.logger().warning(f"{coin} market open failed: {msg[:160]}")

    def _pending_age(self, coin: str) -> float:
        ts = float(self._pending_entry.get(coin) or 0)
        if ts <= 0:
            return 0.0
        return max(0.0, self.current_timestamp - ts)

    def _open_busy(self, except_coin: Optional[str] = None) -> bool:
        """One leftover clip at a time — another coin in or waiting for fill."""
        now = self.current_timestamp
        for c in self._coins:
            if except_coin and c == except_coin:
                continue
            if self._position_side.get(c) in ("LONG", "SHORT"):
                return True
            p = float(self._pending_entry.get(c) or 0)
            if p and (now - p) < 25:
                return True
        return False

    def _sync_positions_from_hl(self, asset_positions) -> None:
        """Trust a successful clearinghouseState. Does not send closes."""
        found = {c: None for c in self._coins}
        if not isinstance(asset_positions, list):
            return
        self._hl_pos_seen = True
        for ap in asset_positions:
            if not isinstance(ap, dict):
                continue
            p = ap.get("position") if isinstance(ap.get("position"), dict) else ap
            coin = str(p.get("coin") or "").upper()
            if coin not in self._coins:
                continue
            try:
                szi = Decimal(str(p.get("szi") or 0))
            except Exception:
                continue
            if abs(szi) < Decimal("1e-8"):
                continue
            try:
                entry = Decimal(str(p.get("entryPx") or 0))
            except Exception:
                entry = Decimal("0")
            side = "LONG" if szi > 0 else "SHORT"
            live_lev = 0
            lev_obj = p.get("leverage")
            try:
                if isinstance(lev_obj, dict):
                    live_lev = int(float(lev_obj.get("value") or 0))
                elif lev_obj not in (None, ""):
                    live_lev = int(float(lev_obj))
            except (TypeError, ValueError):
                live_lev = 0
            if live_lev > 0:
                self._hl_pos_leverage[coin] = live_lev
            found[coin] = (side, abs(szi), entry)
        now = self.current_timestamp
        for coin in self._coins:
            hit = found.get(coin)
            if hit:
                side, amt, entry = hit
                prev = self._position_side.get(coin)
                self._position_side[coin] = side
                self._amount[coin] = amt
                if entry > 0:
                    self._entry_price[coin] = entry
                elif self._entry_price.get(coin) is None:
                    self._entry_price[coin] = self._get_mid(coin)
                if self._entry_time.get(coin) is None:
                    self._entry_time[coin] = now
                self._pending_entry[coin] = 0.0
                if prev != side:
                    self.logger().info(
                        f"HL fill {side} {amt} {_pair_for(coin)} @ {self._entry_price.get(coin)}"
                    )
                    self._log_trade({
                        "action": "OPEN", "coin": coin, "side": side, "amount": float(amt),
                        "price": float(self._entry_price.get(coin) or 0),
                        "reason": f"hl fill {side}",
                    })
                continue
            pend = float(self._pending_entry.get(coin) or 0)
            if pend and (now - pend) < 25:
                continue
            if pend:
                self.logger().info(f"{coin} market open not on Hyperliquid — retry leftover")
                self._pending_entry[coin] = 0.0
            if self._position_side.get(coin) is not None:
                et = self._entry_time.get(coin)
                if et and (now - et) < 20:
                    continue
                side = self._position_side[coin]
                amount = self._amount[coin]
                entry = self._entry_price[coin]
                self.logger().info(
                    f"{coin} flat on Hyperliquid — clearing local {side} state (no HL close sent)"
                )
                if pend:
                    self._clear_pos(coin)
                else:
                    mid = self._get_mid(coin)
                    roe = self._calc_roe_pct(coin, mid) if mid else None
                    amt = _as_float(amount)
                    exit_px = _as_float(mid)
                    entry_f = _as_float(entry)
                    pnl = None
                    if entry_f and exit_px and amt:
                        if side == "SHORT":
                            pnl = amt * (entry_f - exit_px)
                        else:
                            pnl = amt * (exit_px - entry_f)
                    self._log_trade({
                        "action": "CLOSE", "coin": coin, "side": side, "amount": amt,
                        "price": exit_px, "entry_price": entry_f,
                        "leverage": int(self._pos_leverage(coin)),
                        "reason": "exchange flat",
                        "pnl_usd": round(pnl, 4) if pnl is not None else None,
                        "roe_pct": round(roe, 3) if roe is not None else None,
                    })
                    self._last_closed[coin] = now
                    self._clear_pos(coin)

    def _enter_long(self, coin: str, side: str = "LONG", force: bool = False):
        side = str(side or "LONG").upper()
        if side not in ("LONG", "SHORT"):
            return
        if self._position_side.get(coin) is not None:
            return
        if self._pending_age(coin) and self._pending_age(coin) < 25:
            self._quote_skip = f"{coin} waiting for Hyperliquid fill"
            return
        if self._pending_age(coin) >= 25:
            self._pending_entry[coin] = 0.0
        if not force and self._open_busy(except_coin=coin):
            self._quote_skip = "another coin already using leftover — one clip at a time"
            return
        now = self.current_timestamp
        skip = self._quote_skip or ""
        cooldown = 45.0 if ("429" in skip or "quota" in skip.lower()) else 12.0
        if self._hl_blocked():
            return
        if not force and now - float(self._last_entry_attempt.get(coin) or 0) < cooldown:
            return
        if not force and now - float(self._last_closed.get(coin) or 0) < float(self.config.cooldown_time or 0):
            return
        mid = self._get_mid(coin)
        if not force:
            sl_hold = self._sl_loss_hold(coin)
            if sl_hold:
                self._quote_skip = sl_hold
                return
            hold = self._double_win_hold(coin, mid)
            if hold:
                self._quote_skip = hold
                return
        if self._active_orders_for(coin):
            self._cancel_coin_orders(coin)
            self._quote_skip = "canceling rest bids so market open can use the margin"
            return
        if not mid or mid <= 0:
            self._quote_skip = f"{coin} no mid — wait"
            return
        self._refresh_hl_balances(force=True)
        free = self._tradable_free_usd() if force else self._get_free_usd()
        pct = Decimal("1")
        if pct <= 0:
            return
        margin = (free * pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if margin < self.config.min_margin_usd:
            self._quote_skip = (
                f"perp free ${float(free):.2f} < min ${float(self.config.min_margin_usd):.0f} "
                "— send USD to this sub on Hyperliquid perps (spot USDC does not trade)"
            )
            if now - self._last_status_log >= 25:
                self._last_status_log = now
                self.logger().info(f"Skip {coin} {side} entry | {self._quote_skip}")
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
        amount = self._quantize_amount(coin, notional / mid)
        if amount <= 0:
            return
        max_margin = free * pct
        if lev > 0 and amount * mid / lev > max_margin:
            amount = self._quantize_amount(coin, (max_margin * lev) / mid)
            if amount <= 0:
                return
        pair = _pair_for(coin)
        self._last_entry_attempt[coin] = now
        self._quote_skip = ""
        self.logger().info(
            f"DMAN MAKER ENTRY {side} {pair} | leftover {float(pct)*100:.0f}% "
            f"amount={amount} mid=${float(mid):.4f} margin=${float(margin):.2f} lev={int(lev)}x"
        )
        try:
            if side == "SHORT":
                self.sell(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            else:
                self.buy(self.config.connector_name, pair, amount, OrderType.MARKET, mid, PositionAction.OPEN)
            self._pending_entry[coin] = now
            self._quote_skip = f"{coin} {side} submitted — waiting for Hyperliquid fill"
            self.logger().info(f"DMan Maker {side} {coin} submitted, not marked filled until HL confirms")
        except Exception as e:
            self._pending_entry[coin] = 0.0
            self.logger().warning(f"DMan Maker {side} entry {coin} failed: {e}")
            self._quote_skip = f"market {side} failed: {e}"

    def _is_close_pending(self, coin: str) -> bool:
        until = float(self._close_pending_until.get(coin) or 0)
        return until > 0 and self.current_timestamp < until

    def _restore_positions_from_exchange(self, force: bool = False):
        if not self._wallet_allowed():
            return
        if not force and (time.time() - float(self._last_hl_bal or 0)) < 10:
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
                    if self._entry_time[coin] is None:
                        self._entry_time[coin] = self.current_timestamp
                    found[coin] = True
                    self._pending_entry[coin] = 0.0
                    if prev != side:
                        self.logger().info(
                            f"Restored {side} {self._amount[coin]} {pair} @ {self._entry_price[coin]}"
                        )
            for coin in self._coins:
                if self._is_close_pending(coin):
                    continue
                if self._position_side[coin] is not None and not found[coin]:
                    et = self._entry_time[coin]
                    if et and (self.current_timestamp - et) < 20:
                        continue
                    side = self._position_side[coin]
                    amount = self._amount[coin]
                    entry = self._entry_price[coin]
                    mid = self._get_mid(coin)
                    roe = self._calc_roe_pct(coin, mid) if mid else None
                    amt = _as_float(amount)
                    exit_px = _as_float(mid)
                    entry_f = _as_float(entry)
                    pnl = None
                    if entry_f and exit_px and amt:
                        if side == "SHORT":
                            pnl = amt * (entry_f - exit_px)
                        else:
                            pnl = amt * (exit_px - entry_f)
                    self.logger().info(
                        f"{coin} flat on exchange — clearing maker state"
                        + (f" | was {side} {amt} @ {entry_f} ROE~{roe:.2f}%" if side and amt else "")
                    )
                    self._log_trade({
                        "action": "CLOSE", "coin": coin, "side": side, "amount": amt,
                        "price": exit_px, "entry_price": entry_f,
                        "leverage": int(self._pos_leverage(coin)),
                        "reason": "exchange flat",
                        "pnl_usd": round(pnl, 4) if pnl is not None else None,
                        "roe_pct": round(roe, 3) if roe is not None else None,
                    })
                    self._last_closed[coin] = self.current_timestamp
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
        lev = float(self._pos_leverage(coin) or 1)
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
        self._pending_entry[coin] = 0.0
        self._trail_active[coin] = False
        self._trail_extreme[coin] = None
        self._hl_pos_leverage.pop(coin, None)

    def _close_position(self, coin: str, reason: str):
        if not self._wallet_allowed():
            self.logger().warning(f"Close blocked {coin} ({reason}) — unique subaccount not set")
            return
        if self._hl_blocked():
            self.logger().warning(f"Close deferred {coin} ({reason}) — HL address slot busy")
            self._close_pending_until[coin] = max(
                float(self._close_pending_until.get(coin) or 0),
                float(self._hl_action_block_until or 0),
            )
            return
        self._cancel_coin_orders(coin, force=True)
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
                f"CLOSE submitted {side} {amount} {pair} | {reason} | "
                f"{('ROE~'+format(roe,'.2f')+'%') if roe is not None else 'ROE=n/a'} "
                f"— wait for fill (not flat yet)"
            )
        except Exception as e:
            self._note_hl_action_fail(str(e), coin)
            self.logger().error(f"Close failed {coin}: {e}")
            return
        self._close_pending_until[coin] = self.current_timestamp + 15
        self._write_status()

    def format_status(self) -> str:
        free = self._get_free_usd()
        nq = 0
        try:
            nq = len(self.get_active_orders(self.config.connector_name) or [])
        except Exception:
            pass
        lines = [
            f"DMan Maker UP-ONLY | sub={(_subaccount() or 'UNSET')[:12]}… | "
            f"ok={self._wallet_ok_cached} perp=${float(self._get_equity_usd()):.2f} "
            f"free=${float(free):.2f} quotes={nq}"
        ]
        for coin in self._coins:
            mid = self._get_mid(coin)
            roe = self._calc_roe_pct(coin, mid) if mid and self._position_side[coin] else None
            qn = len(self._active_orders_for(coin))
            pend = self._pending_age(coin)
            pend_s = f" pending={pend:.0f}s" if pend else ""
            lines.append(
                f"  {coin} bias={self._bias.get(coin)} quotes={qn} "
                f"| pos={self._position_side[coin] or 'FLAT'}{pend_s} mid={mid} ROE="
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

    def _close_pnl_usd(self, trade: dict):
        """Dollar pnl for a close. Blank rows use amount times the price difference."""
        if not isinstance(trade, dict):
            return None
        raw = trade.get("pnl_usd")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        try:
            amt = float(trade.get("amount"))
            exit_px = float(trade.get("price"))
            entry = float(trade.get("entry_price"))
        except (TypeError, ValueError):
            return None
        if not amt or not exit_px or not entry:
            return None
        if str(trade.get("side") or "").upper() == "SHORT":
            return amt * (entry - exit_px)
        return amt * (exit_px - entry)

    def _sync_risk_closes(self):
        """Book new closes on the 2nd-win ledger before the next entry spends them."""
        try:
            from risk_park import apply_closes
        except Exception:
            return
        rows = []
        for t in (self._read_trade_log() or {}).get("trades") or []:
            if str((t or {}).get("action") or "").upper() != "CLOSE":
                continue
            pnl = self._close_pnl_usd(t)
            if pnl is None:
                continue
            ts = t.get("close_time") or t.get("ts") or t.get("time") or ""
            coin = t.get("coin") or ""
            rows.append({"key": str(ts) + "|" + str(coin), "pnl": pnl})
        try:
            apply_closes(SELF_ID, rows)
        except Exception as e:
            self.logger().warning(f"risk close book failed: {e}")

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
            return
        if str(rec.get("action") or "").upper() == "CLOSE":
            self._sync_risk_closes()

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
                "controller": "market_making.dman_maker_v2",
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
                "leverage": int(self._pos_leverage(coin)),
            }
        log = self._read_trade_log()
        summary = self._summarize_trades(list(log.get("trades") or []))
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "ready": self._trading_active,
            "wallet_ok": self._wallet_ok_cached,
            "controller": "market_making.dman_maker_v2",
            "connector": self.config.connector_name,
            "subaccount": _subaccount() or None,
            "coins": list(self._coins),
            "pairs": [_pair_for(c) for c in self._coins],
            "leverage": {c: self._leverage_for(c) for c in self._coins},
            "dca_spreads": self._spreads(),
            "take_profit": self._tp_frac(),
            "trail_activation": self._trail_act(),
            "trail_delta": self._trail_dlt(),
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
            "dontGoCrazy": self._dont_go_crazy(),
            "afterLossNoRebuy": self._after_loss_no_rebuy(),
            "double_win_hold": self._quote_skip if "two +" in (self._quote_skip or "") else "",
            "sl_loss_hold": self._quote_skip if "afterLossNoRebuy" in (self._quote_skip or "") else "",
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
