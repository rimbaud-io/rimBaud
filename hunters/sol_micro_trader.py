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

import os
from decimal import Decimal
from typing import Optional

import pandas_ta as ta  # noqa: F401
import requests
from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class SolMicroTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))

    # ── Connector ──────────────────────────────────────────────────
    connector_name: str = Field("jupiter/router")
    trading_pair: str = Field("SOL-USDC")
    wallet_address: str = Field("FdSrZGouwVZLcm4Uw6zoaFg8UAiR1fBtzHuh3HdhigVR")
    gateway_url: str = Field("http://localhost:15888")
    network: str = Field("mainnet-beta")

    # ── Capital & risk ─────────────────────────────────────────────
    order_amount_usdc: Decimal = Field(Decimal("13"))    # USDC per trade
    stop_loss_pct: Decimal = Field(Decimal("0.005"))     # 0.5% stop loss
    take_profit_pct: Decimal = Field(Decimal("0.010"))   # 1% take profit
    max_position_age_seconds: int = Field(1800)          # 30 min max hold

    # ── Candle signal ─────────────────────────────────────────────
    candles_connector: str = Field("binance")
    candles_trading_pair: str = Field("SOL-USDT")
    interval: str = Field("3m")
    bb_length: int = Field(14)
    bb_std: float = Field(1.5)

    # ── Signal thresholds (long-only) ─────────────────────────────
    bb_long_threshold: float = Field(0.35)   # BUY when BBP < 0.35

    # ── Pre-loaded position (for manual buys) ─────────────────────
    initial_position_entry_price: Optional[Decimal] = Field(None)
    initial_position_amount_sol: Optional[Decimal] = Field(None)
    initial_position_entry_time: Optional[int] = Field(None)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        return markets


class SolMicroTrader(StrategyV2Base):
    """
    SOL/USDC long-only micro scalper.
    - Signal: Binance 3m BB(14, 1.5) — buy oversold bounces
    - Exit: +1% TP or -0.5% SL or 30min time limit
    - Supports pre-loaded position from manual buys
    """

    def __init__(self, connectors: dict, config: SolMicroTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._signal = 0
        self._bbp = None
        self._last_signal_check = 0
        self._last_status_log = 0
        self._trading_active = False

        # Restore pre-loaded position if set in config
        if config.initial_position_entry_price is not None:
            self._position_side = 1
            self._position_entry_price = config.initial_position_entry_price
            self._position_amount_sol = config.initial_position_amount_sol
            self._position_entry_time = float(config.initial_position_entry_time)
            self._trading_active = True
            self.logger().info(
                f"Restored SOL position: LONG {float(config.initial_position_amount_sol):.4f} SOL "
                f"@ ${float(config.initial_position_entry_price):.4f} | "
                f"TP: +{float(config.take_profit_pct)*100:.1f}% | "
                f"SL: -{float(config.stop_loss_pct)*100:.1f}%"
            )
        else:
            self._position_side: Optional[int] = None
            self._position_entry_price: Optional[Decimal] = None
            self._position_amount_sol: Optional[Decimal] = None
            self._position_entry_time: Optional[float] = None

        candles_config = CandlesConfig(
            connector=self.config.candles_connector,
            trading_pair=self.config.candles_trading_pair,
            interval=self.config.interval,
            max_records=self.config.bb_length + 10,
        )
        self.market_data_provider.initialize_candles_feed(candles_config)

    # ── Lifecycle ─────────────────────────────────────────────────

    def on_tick(self):
        if not self._trading_active:
            self._check_ready()
            return

        # Check for dashboard force-close signal
        close_file = "/home/hummingbot/conf/force_close_sol_micro.txt"
        if os.path.exists(close_file):
            try:
                os.remove(close_file)
            except Exception:
                pass
            if self._position_side is not None:
                self.logger().info("Dashboard force close triggered — closing SOL position immediately")
                self._exit_long()
                return

        self._update_signal()
        self._manage_position()

        now = self.current_timestamp
        if now - self._last_status_log >= 120:
            self._last_status_log = now
            self.logger().info(self.format_status())

    def _check_ready(self):
        price = self._get_sol_price()
        if price and price > 0:
            self._trading_active = True
            self.logger().info(f"SOL Micro Trader ready. SOL price: ${price:.2f}. Strategy active.")

    # ── Signal ────────────────────────────────────────────────────

    def _update_signal(self):
        now = self.current_timestamp
        if now - self._last_signal_check < 30:
            return
        self._last_signal_check = now

        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.interval,
                max_records=self.config.bb_length + 10,
            )
            if df is None or len(df) < self.config.bb_length:
                return

            df.ta.bbands(length=self.config.bb_length, std=self.config.bb_std, append=True)
            bbp_col = f"BBP_{self.config.bb_length}_{self.config.bb_std}"
            if bbp_col not in df.columns:
                return

            self._bbp = float(df[bbp_col].iloc[-1])
            self._signal = 1 if self._bbp < self.config.bb_long_threshold else 0

        except Exception as e:
            self.logger().warning(f"Signal update error: {e}")

    # ── Position management ───────────────────────────────────────

    def _manage_position(self):
        if self._position_side is not None:
            self._check_exit_conditions()
            return
        if self._signal == 1:
            self._enter_long()

    def _enter_long(self):
        usdc_amount = float(self.config.order_amount_usdc)
        try:
            result = self._gateway_post("/connectors/jupiter/router/execute-swap", {
                "walletAddress": self.config.wallet_address,
                "network": self.config.network,
                "baseToken": "USDC",
                "quoteToken": "SOL",
                "amount": usdc_amount,
                "side": "SELL",
                "slippagePct": 1,
            })
            if not result or result.get("status") != 1:
                self.logger().error(f"Entry swap failed: {result}")
                return

            sol_received = float(result["data"]["quoteTokenBalanceChange"])
            entry_price = usdc_amount / sol_received

            self.logger().info(
                f"ENTERING LONG SOL | "
                f"Spent: ${usdc_amount:.2f} USDC | "
                f"Got: {sol_received:.4f} SOL | "
                f"Price: ${entry_price:.4f} | BBP: {self._bbp:.3f}"
            )

            self._position_side = 1
            self._position_entry_price = Decimal(str(entry_price))
            self._position_entry_time = self.current_timestamp
            self._position_amount_sol = Decimal(str(sol_received))

        except Exception as e:
            self.logger().error(f"Enter long error: {e}")

    def _check_exit_conditions(self):
        current_price = self._get_sol_price()
        if not current_price or not self._position_entry_price:
            return

        pnl_pct = (current_price - self._position_entry_price) / self._position_entry_price
        age = self.current_timestamp - self._position_entry_time

        should_exit = False
        reason = ""

        if pnl_pct >= self.config.take_profit_pct:
            should_exit = True
            reason = f"TAKE PROFIT +{float(pnl_pct)*100:.2f}%"
        elif pnl_pct <= -self.config.stop_loss_pct:
            should_exit = True
            reason = f"STOP LOSS {float(pnl_pct)*100:.2f}%"
        elif self.config.max_position_age_seconds > 0 and age >= self.config.max_position_age_seconds:
            should_exit = True
            reason = f"TIME LIMIT ({age/60:.0f}min)"

        if should_exit:
            self.logger().info(
                f"EXITING LONG SOL | Reason: {reason} | "
                f"Entry: ${float(self._position_entry_price):.4f} | "
                f"Current: ${float(current_price):.4f}"
            )
            self._exit_long()

    def _exit_long(self):
        if not self._position_amount_sol:
            return
        try:
            result = self._gateway_post("/connectors/jupiter/router/execute-swap", {
                "walletAddress": self.config.wallet_address,
                "network": self.config.network,
                "baseToken": "SOL",
                "quoteToken": "USDC",
                "amount": float(self._position_amount_sol),
                "side": "SELL",
                "slippagePct": 1,
            })
            if result:
                self.logger().info(f"SELL SOL TX: {result.get('signature','?')} | Status: {result.get('status')}")
        except Exception as e:
            self.logger().error(f"Exit long error: {e}")
        finally:
            self._position_side = None
            self._position_entry_price = None
            self._position_entry_time = None
            self._position_amount_sol = None
            # Clear initial position from running state after exit
            self.config.initial_position_entry_price = None

    # ── Price helpers ─────────────────────────────────────────────

    def _get_sol_price(self) -> Optional[Decimal]:
        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.interval,
                max_records=3,
            )
            if df is not None and not df.empty:
                return Decimal(str(df["close"].iloc[-1]))
        except Exception:
            pass
        return None

    # ── Gateway helpers ───────────────────────────────────────────

    def _gateway_post(self, path: str, body: dict) -> Optional[dict]:
        try:
            r = requests.post(self.config.gateway_url + path, json=body, timeout=30)
            if r.status_code == 200:
                return r.json()
            self.logger().error(f"Gateway POST {path} → {r.status_code}: {r.text[:200]}")
        except Exception as e:
            self.logger().error(f"Gateway POST {path} error: {e}")
        return None

    # ── Status display ────────────────────────────────────────────

    def format_status(self) -> str:
        lines = ["", "  ══════════ SOL Micro Scalper ══════════"]
        sol_price = self._get_sol_price()

        lines.append(f"  Pair       : SOL-USDC")
        lines.append(f"  SOL Price  : ${float(sol_price):.4f}" if sol_price else "  SOL Price  : loading...")
        lines.append(f"  BBP        : {self._bbp:.3f}" if self._bbp is not None else "  BBP        : building...")
        lines.append(f"  Signal     : {'LONG' if self._signal == 1 else 'NEUTRAL'}")
        lines.append(f"  Status     : {'ACTIVE' if self._trading_active else 'WARMING UP'}")

        if self._position_side and self._position_entry_price:
            age_min = (self.current_timestamp - self._position_entry_time) / 60
            tp_price = float(self._position_entry_price) * (1 + float(self.config.take_profit_pct))
            sl_price = float(self._position_entry_price) * (1 - float(self.config.stop_loss_pct))
            lines.append(f"  ── Open LONG ──────────────────────────")
            lines.append(f"  Amount     : {float(self._position_amount_sol):.4f} SOL")
            lines.append(f"  Entry      : ${float(self._position_entry_price):.4f}")
            if sol_price:
                pnl_pct = (sol_price - self._position_entry_price) / self._position_entry_price
                pnl_usd = float(pnl_pct) * float(self.config.order_amount_usdc)
                lines.append(f"  PnL        : {float(pnl_pct)*100:+.2f}% (${pnl_usd:+.2f})")
            lines.append(f"  Age        : {age_min:.1f} min")
            lines.append(f"  TP target  : ${tp_price:.4f} (+{float(self.config.take_profit_pct)*100:.1f}%)")
            lines.append(f"  SL target  : ${sl_price:.4f} (-{float(self.config.stop_loss_pct)*100:.1f}%)")
        else:
            lines.append(f"  Position   : None — waiting for signal (BBP < {self.config.bb_long_threshold})")

        lines.append("  ════════════════════════════════════════")
        return "\n".join(lines)
