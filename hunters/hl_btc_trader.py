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

import os
import statistics
from decimal import Decimal
from typing import Dict, Optional

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, PositionAction, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class HLBTCTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))

    # ── Connector ──────────────────────────────────────────────────
    connector_name: str = Field("hyperliquid_perpetual")
    trading_pair: str = Field("BTC-USD")
    leverage: int = Field(10)

    # ── Position sizing ────────────────────────────────────────────
    order_amount_usd: Decimal = Field(Decimal("25"))     # unused — bot uses half of balance
    stop_loss_pct: Decimal = Field(Decimal("0.015"))     # 1.5% hard SL
    trailing_stop_pct: Decimal = Field(Decimal("0.02"))  # 2% trailing stop from peak
    max_position_age_seconds: int = Field(0)             # 0 = disabled

    # ── Entry signal: 15m BB(20, 2.0) ─────────────────────────────
    candles_connector: str = Field("binance")
    candles_trading_pair: str = Field("BTC-USDT")
    interval: str = Field("15m")
    bb_length: int = Field(20)
    bb_std: float = Field(2.0)
    bb_long_threshold: float = Field(0.35)   # LONG when BBP < 0.35

    # ── Trend filter: 1h SMA(50) ───────────────────────────────────
    trend_interval: str = Field("1h")
    trend_sma_length: int = Field(50)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        if self.connector_name not in markets:
            markets[self.connector_name] = set()
        markets[self.connector_name].add(self.trading_pair)
        return markets


class HLBTCTrader(StrategyV2Base):
    """
    BTC-USD perpetual trader on Hyperliquid.
    - Entry : 15m BB(20, 2.0), BBP < 0.35  AND  price > 1h SMA(50)
    - Exit  : 2% trailing stop from peak  OR  1.5% hard stop loss
    - Sizing: half of available USDC as margin × leverage
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: HLBTCTraderConfig):
        super().__init__(connectors, config)
        self.config = config

        # Signal state
        self._bbp: Optional[float] = None
        self._signal = 0
        self._above_trend: Optional[bool] = None
        self._sma50: Optional[float] = None
        self._entry_trigger_price: Optional[Decimal] = None   # current BBP=0.35 level
        self._last_signal_check = 0

        # Pending entry limit order
        self._entry_order_id: Optional[str] = None
        self._pending_entry_price: Optional[Decimal] = None
        self._pending_entry_amount: Optional[Decimal] = None

        # Position state
        self._position_side: Optional[int] = None
        self._position_entry_price: Optional[Decimal] = None
        self._position_entry_time: Optional[float] = None
        self._position_amount_btc: Optional[Decimal] = None
        self._position_order_usd: Optional[Decimal] = None
        self._position_peak_price: Optional[Decimal] = None   # highest price since entry
        self._tp_order_id: Optional[str] = None

        # Lifecycle
        self._trading_active = False
        self._leverage_set = False
        self._last_status_log = 0

        # Init 15m candles feed
        self.market_data_provider.initialize_candles_feed(CandlesConfig(
            connector=self.config.candles_connector,
            trading_pair=self.config.candles_trading_pair,
            interval=self.config.interval,
            max_records=self.config.bb_length + 10,
        ))
        # Init 1h candles feed for trend filter
        self.market_data_provider.initialize_candles_feed(CandlesConfig(
            connector=self.config.candles_connector,
            trading_pair=self.config.candles_trading_pair,
            interval=self.config.trend_interval,
            max_records=self.config.trend_sma_length + 5,
        ))

    # ── Lifecycle ─────────────────────────────────────────────────

    def on_tick(self):
        if not self._trading_active:
            self._check_ready()
            return

        if not self._leverage_set:
            self._set_leverage()

        # Dashboard force-entry
        force_file = "/home/hummingbot/conf/force_entry_hl_btc.txt"
        if os.path.exists(force_file):
            try:
                os.remove(force_file)
            except Exception:
                pass
            if self._position_side is None:
                self.logger().info("Dashboard force entry — cancelling limit buy, opening LONG at market")
                self._cancel_entry_order()
                btc_price = self._get_btc_price()
                available = self._get_available_usdc()
                margin_usd = (available / Decimal("2")).quantize(Decimal("0.01"))
                notional_usd = margin_usd * Decimal(str(self.config.leverage))
                amount_btc = Decimal(str(round(float(notional_usd / btc_price), 4)))
                self.buy(
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    amount=amount_btc,
                    order_type=OrderType.MARKET,
                    price=btc_price,
                    position_action=PositionAction.OPEN,
                )
                self._position_side = 1
                self._position_entry_price = btc_price
                self._position_entry_time = self.current_timestamp
                self._position_amount_btc = amount_btc
                self._position_order_usd = notional_usd
                self._position_peak_price = btc_price
                self.logger().info(f"Force LONG opened at ${float(btc_price):,.2f}")
                return

        # Dashboard force-close
        close_file = "/home/hummingbot/conf/force_close_hl_btc.txt"
        if os.path.exists(close_file):
            try:
                os.remove(close_file)
            except Exception:
                pass
            if self._position_side is not None:
                self.logger().info("Dashboard force close — closing position immediately")
                self._close_position(self._get_btc_price())
                return
            elif self._entry_order_id:
                self.logger().info("Dashboard force close — cancelling all open orders")
                self._cancel_all_orders()
                return

        self._update_signal()
        self._manage_position()

        now = self.current_timestamp
        if now - self._last_status_log >= 60:
            self._last_status_log = now
            self.logger().info(self.format_status())

    def _check_ready(self):
        try:
            connector = self.connectors[self.config.connector_name]
            if connector.ready:
                self._trading_active = True
                price = self._get_btc_price()
                self.logger().info(
                    f"Hyperliquid connected. BTC: ${float(price):,.2f}. "
                    f"{self.config.leverage}x leverage. Trailing stop: {float(self.config.trailing_stop_pct)*100:.1f}%"
                )
                self._restore_position_from_exchange()
        except Exception:
            pass

    def _restore_position_from_exchange(self):
        """On startup, check if an open position exists on Hyperliquid and restore state."""
        try:
            connector = self.connectors[self.config.connector_name]
            positions = connector.account_positions
            for pos_key, position in positions.items():
                if self.config.trading_pair in pos_key or "BTC" in pos_key:
                    amount = Decimal(str(abs(float(position.amount))))
                    if amount < Decimal("0.0001"):
                        continue
                    entry_price = Decimal(str(position.entry_price))
                    is_long = float(position.amount) > 0
                    self._position_side = 1 if is_long else -1
                    self._position_entry_price = entry_price
                    self._position_entry_time = self.current_timestamp
                    self._position_amount_btc = amount
                    self._position_peak_price = max(entry_price, self._get_btc_price() or entry_price)
                    self._position_order_usd = amount * entry_price
                    self.logger().info(
                        f"RESTORED {'LONG' if is_long else 'SHORT'} position from exchange | "
                        f"Entry: ${float(entry_price):,.2f} | Amount: {float(amount):.4f} BTC | "
                        f"Peak set to: ${float(self._position_peak_price):,.2f}"
                    )
                    return
        except Exception as e:
            self.logger().warning(f"Could not restore position from exchange: {e}")

    def _set_leverage(self):
        try:
            self.connectors[self.config.connector_name].set_leverage(
                trading_pair=self.config.trading_pair,
                leverage=self.config.leverage
            )
            self._leverage_set = True
            self.logger().info(f"Leverage set to {self.config.leverage}x")
        except Exception as e:
            self.logger().warning(f"Leverage set error (will retry): {e}")

    # ── Signal ────────────────────────────────────────────────────

    def _update_signal(self):
        now = self.current_timestamp
        if now - self._last_signal_check < 30:
            return
        self._last_signal_check = now

        try:
            # 15m BBP + entry trigger price
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.interval,
                max_records=self.config.bb_length + 10,
            )
            if df is None or len(df) < self.config.bb_length:
                return

            closes = list(df["close"].astype(float).values[-self.config.bb_length:])
            sma = sum(closes) / self.config.bb_length
            std = statistics.stdev(closes)
            lower = sma - self.config.bb_std * std
            upper = sma + self.config.bb_std * std
            price = closes[-1]
            if upper == lower:
                return
            self._bbp = (price - lower) / (upper - lower)
            self._entry_trigger_price = Decimal(str(round(lower + self.config.bb_long_threshold * (upper - lower), 1)))

            # 1h SMA(50) trend filter
            df1h = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.trend_interval,
                max_records=self.config.trend_sma_length + 5,
            )
            if df1h is not None and len(df1h) >= self.config.trend_sma_length:
                h1_closes = list(df1h["close"].astype(float).values[-self.config.trend_sma_length:])
                self._sma50 = sum(h1_closes) / self.config.trend_sma_length
                self._above_trend = price > self._sma50
            else:
                self._above_trend = True

        except Exception as e:
            self.logger().warning(f"Signal error: {e}")

    # ── Position management ───────────────────────────────────────

    def _manage_position(self):
        if self._position_side is not None:
            self._check_exit_conditions()
            return

        # Check if pending entry order was filled
        if self._entry_order_id:
            in_flight = self.connectors[self.config.connector_name].in_flight_orders
            if self._entry_order_id not in in_flight:
                # Order filled or cancelled — check if we have a position
                self.logger().info(f"Entry order {self._entry_order_id} no longer pending — assuming filled")
                self._position_side = 1
                self._position_entry_price = self._pending_entry_price
                self._position_entry_time = self.current_timestamp
                self._position_amount_btc = self._pending_entry_amount
                available = self._get_available_usdc()
                margin_usd = (available / Decimal("2")).quantize(Decimal("0.01"))
                self._position_order_usd = margin_usd * Decimal(str(self.config.leverage))
                self._position_peak_price = self._pending_entry_price
                self._entry_order_id = None
                self._pending_entry_price = None
                self._pending_entry_amount = None
                self.logger().info(
                    f"ENTERING LONG BTC | Entry: ${float(self._position_entry_price):,.2f} | "
                    f"Amount: {float(self._position_amount_btc):.4f} BTC (limit order filled)"
                )
                return

            # Order still open — update if trigger price shifted > 0.5%
            if self._entry_trigger_price and self._pending_entry_price:
                shift = abs(float(self._entry_trigger_price - self._pending_entry_price) / float(self._pending_entry_price))
                if shift > 0.005:
                    self._cancel_entry_order()
                    self._post_entry_order()
            return

        # No position and no pending order — post limit buy at trigger price
        self._post_entry_order()

    def _post_entry_order(self):
        if not self._entry_trigger_price or not self._above_trend:
            return
        if self._entry_trigger_price >= self._get_btc_price():
            return  # trigger is above current price — would fill immediately, use market instead

        available = self._get_available_usdc()
        margin_usd = (available / Decimal("2")).quantize(Decimal("0.01"))
        if margin_usd < Decimal("5"):
            return

        notional_usd = margin_usd * Decimal(str(self.config.leverage))
        amount_btc = Decimal(str(round(float(notional_usd / self._entry_trigger_price), 4)))

        try:
            order_id = self.buy(
                connector_name=self.config.connector_name,
                trading_pair=self.config.trading_pair,
                amount=amount_btc,
                order_type=OrderType.LIMIT,
                price=self._entry_trigger_price,
                position_action=PositionAction.OPEN,
            )
            self._entry_order_id = order_id
            self._pending_entry_price = self._entry_trigger_price
            self._pending_entry_amount = amount_btc
            trend_info = f"SMA50: ${self._sma50:,.0f}" if self._sma50 else "trend: building"
            self.logger().info(
                f"LIMIT BUY posted at ${float(self._entry_trigger_price):,.2f} | "
                f"Amount: {float(amount_btc):.4f} BTC | Margin: ${float(margin_usd):.2f} | "
                f"Notional: ${float(notional_usd):.2f} ({self.config.leverage}x) | {trend_info}"
            )
        except Exception as e:
            self.logger().error(f"Limit buy order error: {e}")

    def _cancel_entry_order(self):
        if not self._entry_order_id:
            return
        try:
            self.cancel(self.config.connector_name, self._entry_order_id)
            self.logger().info(f"Cancelled stale entry order {self._entry_order_id} (trigger shifted)")
        except Exception as e:
            self.logger().warning(f"Could not cancel entry order: {e}")
        self._entry_order_id = None
        self._pending_entry_price = None
        self._pending_entry_amount = None

    def _get_available_usdc(self) -> Decimal:
        try:
            bal = self.connectors[self.config.connector_name].get_available_balance("USD")
            return bal if bal else Decimal("0")
        except Exception:
            return Decimal("0")


    def _check_exit_conditions(self):
        btc_price = self._get_btc_price()
        if not btc_price or not self._position_entry_price:
            return

        # Update peak price (trailing stop tracks the highest point)
        if btc_price > self._position_peak_price:
            self._position_peak_price = btc_price

        pnl_pct = float((btc_price - self._position_entry_price) / self._position_entry_price)
        drawdown_from_peak = float((self._position_peak_price - btc_price) / self._position_peak_price)
        should_exit = False
        reason = ""

        # Hard stop loss
        if pnl_pct <= -float(self.config.stop_loss_pct):
            should_exit = True
            reason = f"STOP LOSS {pnl_pct*100:.2f}%"
            self._tp_order_id = None

        # Trailing stop: only activates once position is in profit (peak > entry)
        elif float(self._position_peak_price) > float(self._position_entry_price):
            if drawdown_from_peak >= float(self.config.trailing_stop_pct):
                should_exit = True
                peak_pnl = float((self._position_peak_price - self._position_entry_price) / self._position_entry_price)
                reason = (f"TRAILING STOP | Peak: ${float(self._position_peak_price):,.2f} "
                          f"(+{peak_pnl*100:.2f}%) | Drop: -{drawdown_from_peak*100:.2f}%")
                self._tp_order_id = None

        # Time limit fallback
        elif self.config.max_position_age_seconds > 0:
            age = self.current_timestamp - self._position_entry_time
            if age >= self.config.max_position_age_seconds:
                should_exit = True
                reason = f"TIME LIMIT ({age/60:.0f}min)"

        if should_exit:
            lev_pnl = pnl_pct * self.config.leverage
            self.logger().info(
                f"EXITING LONG BTC | Reason: {reason} | "
                f"Entry: ${float(self._position_entry_price):,.2f} | "
                f"Current: ${float(btc_price):,.2f} | "
                f"PnL: {pnl_pct*100:+.2f}% → {lev_pnl*100:+.2f}% leveraged"
            )
            self._close_position(btc_price)

    def _cancel_all_orders(self):
        """Cancel every open order on this trading pair to avoid accidental shorts."""
        try:
            connector = self.connectors[self.config.connector_name]
            for order_id, order in list(connector.in_flight_orders.items()):
                if order.trading_pair == self.config.trading_pair:
                    try:
                        self.cancel(self.config.connector_name, order_id)
                        self.logger().info(f"Cancelled open order {order_id} ({order.order_type} {order.trade_type})")
                    except Exception:
                        pass
        except Exception as e:
            self.logger().warning(f"Cancel all orders error: {e}")
        self._tp_order_id = None
        self._entry_order_id = None
        self._pending_entry_price = None
        self._pending_entry_amount = None

    def _close_position(self, btc_price: Decimal):
        self._cancel_all_orders()

        try:
            self.sell(
                connector_name=self.config.connector_name,
                trading_pair=self.config.trading_pair,
                amount=self._position_amount_btc,
                order_type=OrderType.MARKET,
                price=btc_price,
                position_action=PositionAction.CLOSE,
            )
        except Exception as e:
            self.logger().error(f"Close position error: {e}")
        finally:
            self._position_side = None
            self._position_entry_price = None
            self._position_entry_time = None
            self._position_amount_btc = None
            self._position_order_usd = None
            self._position_peak_price = None
            self._tp_order_id = None

    # ── Price helper ──────────────────────────────────────────────

    def _get_btc_price(self) -> Optional[Decimal]:
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

    # ── Status display ────────────────────────────────────────────

    def format_status(self) -> str:
        lines = ["", "  ══════════ Hyperliquid BTC Trader ══════════"]
        btc_price = self._get_btc_price()

        lines.append(f"  Pair       : {self.config.trading_pair}  ({self.config.leverage}x leverage)")
        lines.append(f"  BTC Price  : ${float(btc_price):,.2f}" if btc_price else "  BTC Price  : loading...")
        lines.append(f"  BBP (15m)  : {self._bbp:.3f}" if self._bbp is not None else "  BBP (15m)  : building...")
        trend_str = f"${self._sma50:,.0f} ({'✓ above' if self._above_trend else '✗ below'})" if self._sma50 else "building..."
        lines.append(f"  SMA50 (1h) : {trend_str}")
        lines.append(f"  Signal     : {'LONG ✓' if self._signal == 1 else 'NEUTRAL'}")
        lines.append(f"  Status     : {'ACTIVE' if self._trading_active else 'CONNECTING...'}")

        if self._position_side and self._position_entry_price:
            age_min = (self.current_timestamp - self._position_entry_time) / 60
            trail_sl = float(self._position_peak_price) * (1 - float(self.config.trailing_stop_pct))
            sl_price = float(self._position_entry_price) * (1 - float(self.config.stop_loss_pct))
            lines.append(f"  ── Open LONG ──────────────────────────")
            lines.append(f"  Amount     : {float(self._position_amount_btc):.4f} BTC")
            lines.append(f"  Entry      : ${float(self._position_entry_price):,.2f}")
            if btc_price:
                pnl_pct = float((btc_price - self._position_entry_price) / self._position_entry_price)
                pnl_usd = pnl_pct * float(self._position_order_usd or 0) if self._position_order_usd else 0
                lines.append(f"  PnL        : {pnl_pct*100:+.2f}% → {pnl_pct*self.config.leverage*100:+.2f}% leveraged (${pnl_usd:+.2f})")
            lines.append(f"  Peak       : ${float(self._position_peak_price):,.2f}")
            lines.append(f"  Trail SL   : ${trail_sl:,.2f} (peak -{float(self.config.trailing_stop_pct)*100:.1f}%)")
            lines.append(f"  Hard SL    : ${sl_price:,.2f} (entry -{float(self.config.stop_loss_pct)*100:.1f}%)")
            lines.append(f"  Age        : {age_min:.1f} min")
        elif self._entry_order_id and self._pending_entry_price:
            lines.append(f"  ── Pending Limit Buy ──────────────────")
            lines.append(f"  Order at   : ${float(self._pending_entry_price):,.2f}")
            lines.append(f"  Amount     : {float(self._pending_entry_amount):.4f} BTC")
            if btc_price:
                gap = float(btc_price - self._pending_entry_price)
                lines.append(f"  Gap        : ${gap:,.2f} to fill")
        else:
            lines.append(f"  Position   : None — waiting for signal")
            lines.append(f"  Long when  : BBP < {self.config.bb_long_threshold} AND price > SMA50")

        lines.append("  ════════════════════════════════════════════")
        return "\n".join(lines)
