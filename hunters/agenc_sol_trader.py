# Copyright 2026 Laurence Sterne
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

import requests
from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class AgenCSolTraderConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))

    # ── Wallet & Connector ────────────────────────────────────────
    wallet_address: str = Field("FdSrZGouwVZLcm4Uw6zoaFg8UAiR1fBtzHuh3HdhigVR")
    gateway_url: str = Field("http://localhost:15888")
    network: str = Field("mainnet-beta")

    # ── Capital & risk (SOL-denominated) ──────────────────────────
    sol_per_trade: Decimal = Field(Decimal("0.05"))      # SOL to spend per trade
    stop_loss_enabled: bool = Field(True)                # set False to disable stop loss
    stop_loss_pct: Decimal = Field(Decimal("0.05"))      # 5% stop loss (ignored if disabled)
    take_profit_pct: Decimal = Field(Decimal("0.10"))    # 10% take profit
    max_position_age_seconds: int = Field(3600)          # 1 hour max hold

    # ── Candle signal (Binance SOL-USDT as proxy) ─────────────────
    candles_connector: str = Field("binance")
    candles_trading_pair: str = Field("SOL-USDT")
    interval: str = Field("1m")
    bb_length: int = Field(20)
    bb_std: float = Field(2.0)

    # ── Signal thresholds ──────────────────────────────────────────
    bb_long_threshold: float = Field(0.40)   # BUY when BBP < 0.40
    bb_short_threshold: float = Field(0.99)  # reserved — long-only for meme tokens

    # ── Pre-loaded position (optional, for manual buys) ───────────
    initial_position_side: Optional[int] = Field(None)
    initial_position_entry_price: Optional[Decimal] = Field(None)
    initial_position_amount_agenc: Optional[Decimal] = Field(None)
    initial_position_entry_time: Optional[int] = Field(None)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        return markets


class AgenCSolTrader(StrategyV2Base):
    """
    AgenC token trader on Solana via Jupiter/Meteora.
    - Signal: Binance SOL-USDT Bollinger Bands (market conditions proxy)
    - Execution: Jupiter router → Meteora DLMM (SOL ↔ AGENC)
    - Long-only: buys AGENC with SOL when market is oversold
    - Gateway direct calls bypass ExactOut limitation
    """

    AGENC_ADDR = "5yC9BM8KUsJTPbWPLfA2N8qH1s9V8DQ3Vcw1G6Jdpump"
    SOL_ADDR = "So11111111111111111111111111111111111111112"

    def __init__(self, connectors: dict, config: AgenCSolTraderConfig):
        super().__init__(connectors, config)
        self.config = config
        self._signal = 0
        self._bbp = None
        self._last_signal_check = 0
        self._trading_active = False
        self._last_status_log = 0
        self._last_price_check = 0
        self._cached_agenc_price: Optional[Decimal] = None

        # Restore pre-loaded position if set in config
        if config.initial_position_side is not None:
            self._position_side = config.initial_position_side
            self._position_entry_price = config.initial_position_entry_price
            self._position_amount_agenc = config.initial_position_amount_agenc
            self._position_entry_time = float(config.initial_position_entry_time)
            self._trading_active = True  # skip warmup when restoring position
            sl_info = "NO STOP LOSS" if not config.stop_loss_enabled else f"SL: -{float(config.stop_loss_pct)*100:.0f}%"
            self.logger().info(
                f"Restored position from config: LONG {float(config.initial_position_amount_agenc):,.0f} AGENC "
                f"@ {float(config.initial_position_entry_price):.8f} SOL | "
                f"TP: +{float(config.take_profit_pct) * 100:.0f}% | {sl_info}"
            )
        else:
            self._position_side: Optional[int] = None
            self._position_entry_price: Optional[Decimal] = None
            self._position_entry_time: Optional[float] = None
            self._position_amount_agenc: Optional[Decimal] = None

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
        close_file = "/home/hummingbot/conf/force_close_agenc.txt"
        if os.path.exists(close_file):
            try:
                os.remove(close_file)
            except Exception:
                pass
            if self._position_side is not None:
                self.logger().info("Dashboard force close triggered — selling AGENC immediately")
                self._exit_long()
                return

        self._update_signal()
        self._manage_position()

        now = self.current_timestamp
        if now - self._last_status_log >= 120:
            self._last_status_log = now
            self.logger().info(self.format_status())

    def _check_ready(self):
        # Throttle: only attempt once per 60s (reuses price cache cooldown)
        price = self._get_agenc_price_in_sol()
        if price and price > 0:
            self._trading_active = True
            sol_usd = self._get_sol_usd_price()
            usd_str = f" (${float(price) * sol_usd:.6f})" if sol_usd else ""
            self.logger().info(f"AgenC trader ready. AGENC price: {price:.8f} SOL{usd_str}")

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

            import pandas_ta as ta  # noqa: F401
            df.ta.bbands(length=self.config.bb_length, std=self.config.bb_std, append=True)
            bbp_col = f"BBP_{self.config.bb_length}_{self.config.bb_std}"
            if bbp_col not in df.columns:
                return

            self._bbp = float(df[bbp_col].iloc[-1])
            if self._bbp < self.config.bb_long_threshold:
                self._signal = 1
            elif self._bbp > self.config.bb_short_threshold:
                self._signal = -1
            else:
                self._signal = 0

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
        sol_amount = float(self.config.sol_per_trade)
        try:
            quote = self._gateway_get(
                "/connectors/jupiter/router/quote-swap",
                params={
                    "network": self.config.network,
                    "baseToken": "SOL",
                    "quoteToken": "AGENC",
                    "amount": sol_amount,
                    "side": "SELL",
                },
            )
            if not quote:
                return

            agenc_amount = float(quote["amountOut"])
            agenc_price_sol = sol_amount / agenc_amount  # SOL per AGENC

            self.logger().info(
                f"ENTERING LONG AGENC | "
                f"Spending: {sol_amount:.4f} SOL | "
                f"Expected: {agenc_amount:,.0f} AGENC | "
                f"Price: {agenc_price_sol:.8f} SOL/AGENC | "
                f"BBP: {self._bbp:.3f}"
            )

            result = self._gateway_post(
                "/connectors/jupiter/router/execute-swap",
                body={
                    "walletAddress": self.config.wallet_address,
                    "network": self.config.network,
                    "baseToken": "SOL",
                    "quoteToken": "AGENC",
                    "amount": sol_amount,
                    "side": "SELL",
                },
            )
            if not result or result.get("txHash") is None:
                self.logger().error(f"Execute-swap returned unexpected result: {result}")
                return

            self.logger().info(f"BUY AGENC TX: {result.get('txHash', 'unknown')}")

            self._position_side = 1
            self._position_entry_price = Decimal(str(agenc_price_sol))
            self._position_entry_time = self.current_timestamp
            self._position_amount_agenc = Decimal(str(agenc_amount))

        except Exception as e:
            self.logger().error(f"Enter long error: {e}")

    def _check_exit_conditions(self):
        current_price = self._get_agenc_price_in_sol()
        if not current_price or not self._position_entry_price:
            return

        pnl_pct = (current_price - self._position_entry_price) / self._position_entry_price
        age = self.current_timestamp - self._position_entry_time

        should_exit = False
        reason = ""

        if pnl_pct >= self.config.take_profit_pct:
            should_exit = True
            reason = f"TAKE PROFIT +{pnl_pct * 100:.2f}%"
        elif self.config.stop_loss_enabled and pnl_pct <= -self.config.stop_loss_pct:
            should_exit = True
            reason = f"STOP LOSS {pnl_pct * 100:.2f}%"
        elif self.config.max_position_age_seconds > 0 and age >= self.config.max_position_age_seconds:
            should_exit = True
            reason = f"TIME LIMIT ({age / 60:.0f}min)"

        if should_exit:
            self.logger().info(
                f"EXITING LONG AGENC | Reason: {reason} | "
                f"Entry: {self._position_entry_price:.8f} SOL | "
                f"Current: {current_price:.8f} SOL"
            )
            self._exit_long()

    def _exit_long(self):
        if not self._position_amount_agenc:
            return
        agenc_amount = float(self._position_amount_agenc)
        try:
            result = self._gateway_post(
                "/connectors/jupiter/router/execute-swap",
                body={
                    "walletAddress": self.config.wallet_address,
                    "network": self.config.network,
                    "baseToken": "AGENC",
                    "quoteToken": "SOL",
                    "amount": agenc_amount,
                    "side": "SELL",
                },
            )
            if result:
                self.logger().info(f"SELL AGENC TX: {result.get('txHash', 'unknown')}")
        except Exception as e:
            self.logger().error(f"Exit long error: {e}")
        finally:
            self._position_side = None
            self._position_entry_price = None
            self._position_entry_time = None
            self._position_amount_agenc = None

    # ── Price helpers ─────────────────────────────────────────────

    def _get_agenc_price_in_sol(self) -> Optional[Decimal]:
        """Quote 1000 AGENC → SOL to get the current price. Refreshes at most every 60s."""
        now = self.current_timestamp
        if now - self._last_price_check < 60:
            return self._cached_agenc_price  # return cached (may be None on cold start)
        self._last_price_check = now  # mark attempt regardless of success
        try:
            quote = self._gateway_get(
                "/connectors/jupiter/router/quote-swap",
                params={
                    "network": self.config.network,
                    "baseToken": "AGENC",
                    "quoteToken": "SOL",
                    "amount": 1000,
                    "side": "SELL",
                },
            )
            if quote:
                self._cached_agenc_price = Decimal(str(quote["price"]))
        except Exception:
            pass
        return self._cached_agenc_price

    def _get_sol_usd_price(self) -> float:
        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.interval,
                max_records=2,
            )
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    # ── Gateway HTTP helpers ───────────────────────────────────────

    def _gateway_get(self, path: str, params: dict) -> Optional[dict]:
        try:
            r = requests.get(
                self.config.gateway_url + path,
                params=params,
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            self.logger().warning(f"Gateway GET {path} → {r.status_code}: {r.text[:200]}")
        except Exception as e:
            self.logger().warning(f"Gateway GET {path} error: {e}")
        return None

    def _gateway_post(self, path: str, body: dict) -> Optional[dict]:
        try:
            r = requests.post(
                self.config.gateway_url + path,
                json=body,
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()
            self.logger().error(f"Gateway POST {path} → {r.status_code}: {r.text[:300]}")
        except Exception as e:
            self.logger().error(f"Gateway POST {path} error: {e}")
        return None

    # ── Status display ────────────────────────────────────────────

    def format_status(self) -> str:
        lines = ["", "  ══════════ AgenC SOL Trader ══════════"]
        agenc_price = self._get_agenc_price_in_sol()
        sol_usd = self._get_sol_usd_price()

        agenc_usd = (float(agenc_price) * sol_usd) if (agenc_price and sol_usd) else None

        lines.append(f"  Token      : AgenC (5yC9...pump)")
        if agenc_price:
            usd_str = f" (${agenc_usd:.6f})" if agenc_usd else ""
            lines.append(f"  Price      : {agenc_price:.8f} SOL{usd_str}")
        else:
            lines.append(f"  Price      : fetching...")
        lines.append(f"  SOL/USD    : ${sol_usd:.2f}" if sol_usd else "  SOL/USD    : loading...")
        lines.append(f"  BBP        : {self._bbp:.3f}" if self._bbp is not None else "  BBP        : building...")
        lines.append(f"  Signal     : {'LONG' if self._signal == 1 else 'NEUTRAL'}")
        lines.append(f"  Status     : {'ACTIVE' if self._trading_active else 'WARMING UP'}")

        if self._position_side and self._position_entry_price:
            age_min = (self.current_timestamp - self._position_entry_time) / 60
            tp_price = float(self._position_entry_price) * (1 + float(self.config.take_profit_pct))
            lines.append(f"  ── Open LONG ──────────────────────────")
            lines.append(f"  Amount     : {float(self._position_amount_agenc):,.0f} AGENC")
            lines.append(f"  Entry      : {float(self._position_entry_price):.8f} SOL")
            if agenc_price:
                pnl_pct = (agenc_price - self._position_entry_price) / self._position_entry_price
                pnl_sol = float(pnl_pct) * float(self.config.sol_per_trade)
                lines.append(f"  PnL        : {float(pnl_pct) * 100:+.2f}% ({pnl_sol:+.4f} SOL)")
            else:
                lines.append(f"  PnL        : (price updating...)")
            lines.append(f"  Age        : {age_min:.1f} min")
            lines.append(f"  TP target  : {tp_price:.8f} SOL (+{float(self.config.take_profit_pct)*100:.0f}%)")
            sl_line = f"  Stop Loss  : DISABLED — holding through dips" if not self.config.stop_loss_enabled else \
                      f"  SL target  : {float(self._position_entry_price) * (1 - float(self.config.stop_loss_pct)):.8f} SOL"
            lines.append(sl_line)
        else:
            lines.append(f"  Position   : None — waiting for signal (BBP < {self.config.bb_long_threshold})")

        lines.append("  ══════════════════════════════════════")
        return "\n".join(lines)
