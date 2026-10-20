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

import os
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd
import pandas_ta as ta
from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class SolanaBBTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))

    # ── Connector ──────────────────────────────────────────────────
    connector_name: str = Field("jupiter/router")
    trading_pair: str = Field("SOL-USDC")

    # ── Capital & risk ─────────────────────────────────────────────
    order_amount_quote: Decimal = Field(Decimal("20"))   # USDC per trade
    stop_loss_pct: Decimal = Field(Decimal("0.02"))      # 2%
    take_profit_pct: Decimal = Field(Decimal("0.04"))    # 4%
    max_position_age_seconds: int = Field(7200)          # 2 hours

    # ── Candle signal (Binance) ────────────────────────────────────
    candles_connector: str = Field("binance")
    candles_trading_pair: str = Field("SOL-USDT")
    interval: str = Field("1m")
    bb_length: int = Field(20)
    bb_std: float = Field(2.0)

    # ── 80% bullish bias ──────────────────────────────────────────
    bb_long_threshold: float = Field(0.55)   # BUY  when BBP < 0.55
    bb_short_threshold: float = Field(0.75)  # SELL when BBP > 0.75

    def update_markets(self, markets: MarketDict) -> MarketDict:
        return markets


class SolanaBBTrader(StrategyV2Base):
    """
    Bollinger Bands directional trader for Solana via Jupiter/Raydium.
    - Signal source: Binance SOL-USDT candles
    - Execution: Jupiter router (routes through Raydium CLMM)
    - Bias: 80% bullish (bb_long_threshold=0.40, bb_short_threshold=0.90)
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: SolanaBBTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._candles = None
        self._signal = 0          # 1=LONG, -1=SHORT, 0=NEUTRAL
        self._bbp = None          # Bollinger Band Position (0→1)
        self._position_side: Optional[int] = None    # 1 or -1
        self._position_entry_price: Optional[Decimal] = None
        self._position_entry_time: Optional[float] = None
        self._position_amount_base: Optional[Decimal] = None
        self._last_signal_check = 0
        self._trading_active = False

        # Init candles feed
        candles_config = CandlesConfig(
            connector=self.config.candles_connector,
            trading_pair=self.config.candles_trading_pair,
            interval=self.config.interval,
            max_records=self.config.bb_length + 10
        )
        self.market_data_provider.initialize_candles_feed(candles_config)

    def on_tick(self):
        if not self._trading_active:
            self._check_connector_ready()
            return

        self._update_signal()
        self._manage_position()

    def _check_connector_ready(self):
        try:
            price = self._get_current_price()
            if price and price > 0:
                self._trading_active = True
                self.logger().info(f"Connector ready. SOL price: ${price:.2f}. Strategy active.")
        except Exception:
            pass

    def _get_current_price(self) -> Optional[Decimal]:
        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.interval,
                max_records=5
            )
            if df is not None and not df.empty:
                return Decimal(str(df["close"].iloc[-1]))
        except Exception:
            pass
        return None

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
                max_records=self.config.bb_length + 10
            )
            if df is None or len(df) < self.config.bb_length:
                return

            df.ta.bbands(
                length=self.config.bb_length,
                std=self.config.bb_std,
                append=True
            )
            bbp_col = f"BBP_{self.config.bb_length}_{self.config.bb_std}"
            if bbp_col not in df.columns:
                return

            bbp = float(df[bbp_col].iloc[-1])
            self._bbp = bbp

            if bbp < self.config.bb_long_threshold:
                self._signal = 1
            elif bbp > self.config.bb_short_threshold:
                self._signal = -1
            else:
                self._signal = 0

        except Exception as e:
            self.logger().warning(f"Signal update error: {e}")

    def _manage_position(self):
        # Check if we need to exit current position
        if self._position_side is not None:
            self._check_exit_conditions()
            return

        # Enter new position if signal
        if self._signal == 1:
            self._enter_position(TradeType.BUY)
        elif self._signal == -1:
            self._enter_position(TradeType.SELL)

    def _enter_position(self, side: TradeType):
        current_price = self._get_current_price()
        if not current_price:
            return

        # Calculate base amount from quote budget
        amount_base = self.config.order_amount_quote / current_price

        self.logger().info(
            f"ENTERING {'LONG' if side == TradeType.BUY else 'SHORT'} | "
            f"Price: ${current_price:.4f} | "
            f"Amount: {amount_base:.6f} SOL | "
            f"BBP: {self._bbp:.3f}"
        )

        try:
            if side == TradeType.BUY:
                order_id = self.buy(
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    amount=amount_base,
                    order_type=__import__("hummingbot.core.data_type.common", fromlist=["OrderType"]).OrderType.MARKET,
                    price=current_price,
                )
            else:
                order_id = self.sell(
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    amount=amount_base,
                    order_type=__import__("hummingbot.core.data_type.common", fromlist=["OrderType"]).OrderType.MARKET,
                    price=current_price,
                )

            self._position_side = 1 if side == TradeType.BUY else -1
            self._position_entry_price = current_price
            self._position_entry_time = self.current_timestamp
            self._position_amount_base = amount_base

        except Exception as e:
            self.logger().error(f"Order placement error: {e}")

    def _check_exit_conditions(self):
        current_price = self._get_current_price()
        if not current_price or not self._position_entry_price:
            return

        pnl_pct = (current_price - self._position_entry_price) / self._position_entry_price
        if self._position_side == -1:
            pnl_pct = -pnl_pct

        age = self.current_timestamp - self._position_entry_time
        side_label = "LONG" if self._position_side == 1 else "SHORT"

        should_exit = False
        reason = ""

        if pnl_pct >= self.config.take_profit_pct:
            should_exit = True
            reason = f"TAKE PROFIT +{pnl_pct * 100:.2f}%"
        elif pnl_pct <= -self.config.stop_loss_pct:
            should_exit = True
            reason = f"STOP LOSS {pnl_pct * 100:.2f}%"
        elif age >= self.config.max_position_age_seconds:
            should_exit = True
            reason = f"TIME LIMIT ({age / 3600:.1f}h)"

        if should_exit:
            self.logger().info(
                f"EXITING {side_label} | Reason: {reason} | "
                f"Entry: ${self._position_entry_price:.4f} | "
                f"Current: ${current_price:.4f}"
            )
            self._close_position(current_price)

    def _close_position(self, current_price: Decimal):
        try:
            if self._position_side == 1:
                self.sell(
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    amount=self._position_amount_base,
                    order_type=__import__("hummingbot.core.data_type.common", fromlist=["OrderType"]).OrderType.MARKET,
                    price=current_price,
                )
            else:
                self.buy(
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    amount=self._position_amount_base,
                    order_type=__import__("hummingbot.core.data_type.common", fromlist=["OrderType"]).OrderType.MARKET,
                    price=current_price,
                )
            self._position_side = None
            self._position_entry_price = None
            self._position_entry_time = None
            self._position_amount_base = None
        except Exception as e:
            self.logger().error(f"Close position error: {e}")

    def format_status(self) -> str:
        lines = ["", "  ══════════ Solana BB Trader ══════════"]
        current_price = self._get_current_price()

        lines.append(f"  Connector  : {self.config.connector_name}")
        lines.append(f"  Pair       : {self.config.trading_pair}")
        lines.append(f"  SOL Price  : ${current_price:.4f}" if current_price else "  SOL Price  : loading...")
        lines.append(f"  BBP        : {self._bbp:.3f}" if self._bbp is not None else "  BBP        : building candles...")
        lines.append(f"  Signal     : {'LONG' if self._signal == 1 else 'SHORT' if self._signal == -1 else 'NEUTRAL'}")
        lines.append(f"  Status     : {'ACTIVE' if self._trading_active else 'WARMING UP'}")

        if self._position_side is not None and current_price:
            pnl_pct = (current_price - self._position_entry_price) / self._position_entry_price
            if self._position_side == -1:
                pnl_pct = -pnl_pct
            pnl_usd = float(pnl_pct) * float(self.config.order_amount_quote)
            age = (self.current_timestamp - self._position_entry_time) / 60
            lines.append(f"  ── Open Position ──────────────────────")
            lines.append(f"  Side       : {'LONG' if self._position_side == 1 else 'SHORT'}")
            lines.append(f"  Entry      : ${self._position_entry_price:.4f}")
            lines.append(f"  PnL        : {pnl_pct * 100:+.2f}% (${pnl_usd:+.2f})")
            lines.append(f"  Age        : {age:.1f} min")
            lines.append(f"  TP target  : ${float(self._position_entry_price) * (1 + float(self.config.take_profit_pct)):.4f}")
            lines.append(f"  SL target  : ${float(self._position_entry_price) * (1 - float(self.config.stop_loss_pct)):.4f}")
        else:
            lines.append(f"  Position   : None — waiting for signal")

        lines.append("  ══════════════════════════════════════")
        return "\n".join(lines)
