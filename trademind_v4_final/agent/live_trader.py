"""
agent/live_trader.py
Phase 4 ONLY — real order execution via Zerodha Kite API.

DO NOT USE until:
1. Backtest shows 55%+ win rate with positive expectancy
2. Paper trading for 60+ days matches backtest performance
3. You understand every line of code in this file

Setup:
1. pip install kiteconnect
2. Get API key from https://developers.kite.trade/
3. Set KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN in .env
4. Access token refreshes daily — automate this or do it manually each morning
"""

import logging
from config.settings import KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN

logger = logging.getLogger(__name__)


class LiveTrader:
    """
    Executes real orders on Zerodha via Kite API.
    Only used when TRADING_MODE = "live" in settings.
    """

    def __init__(self):
        self.kite = self._init_kite()

    def _init_kite(self):
        """Initialize Kite connection."""
        if not KITE_API_KEY or not KITE_ACCESS_TOKEN:
            logger.error("Kite credentials missing. Set them in .env file.")
            return None

        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=KITE_API_KEY)
            kite.set_access_token(KITE_ACCESS_TOKEN)
            profile = kite.profile()
            logger.info(f"Kite connected: {profile['user_name']}")
            return kite
        except ImportError:
            logger.error("kiteconnect not installed: pip install kiteconnect")
            return None
        except Exception as e:
            logger.error(f"Kite connection failed: {e}")
            return None

    def place_buy_order(self, ticker: str, qty: int,
                        sl_price: float, target: float) -> str | None:
        """
        Place a CNC (delivery) BUY order with GTT stop loss.

        Args:
            ticker:    NSE symbol (WITHOUT .NS suffix — Kite uses raw symbols)
            qty:       number of shares
            sl_price:  stop loss trigger price
            target:    target exit price (for reference only — set manually)

        Returns:
            order_id string if successful, None if failed
        """
        if not self.kite:
            logger.error("Kite not initialized — cannot place order")
            return None

        # Strip yfinance suffix
        symbol = ticker.replace(".NS", "").replace(".BO", "")

        try:
            # Main buy order (CNC = delivery, not intraday)
            order_id = self.kite.place_order(
                variety    = self.kite.VARIETY_REGULAR,
                exchange   = self.kite.EXCHANGE_NSE,
                tradingsymbol = symbol,
                transaction_type = self.kite.TRANSACTION_TYPE_BUY,
                quantity   = qty,
                product    = self.kite.PRODUCT_CNC,          # delivery
                order_type = self.kite.ORDER_TYPE_MARKET,    # market order
            )
            logger.info(f"BUY order placed: {symbol} x{qty} | Order ID: {order_id}")

            # GTT stop loss order (Good Till Triggered)
            self._place_gtt_sl(symbol, qty, sl_price)

            return str(order_id)

        except Exception as e:
            logger.error(f"Order placement failed for {symbol}: {e}")
            return None

    def _place_gtt_sl(self, symbol: str, qty: int, sl_price: float):
        """Place a GTT (Good Till Triggered) stop loss order."""
        try:
            gtt_id = self.kite.place_gtt(
                trigger_type = self.kite.GTT_TYPE_SINGLE,
                tradingsymbol = symbol,
                exchange     = self.kite.EXCHANGE_NSE,
                trigger_values = [sl_price],
                last_price   = sl_price,
                orders       = [{
                    "transaction_type": self.kite.TRANSACTION_TYPE_SELL,
                    "quantity":         qty,
                    "product":          self.kite.PRODUCT_CNC,
                    "order_type":       self.kite.ORDER_TYPE_LIMIT,
                    "price":            round(sl_price * 0.995, 2),  # 0.5% below trigger
                }]
            )
            logger.info(f"GTT SL set for {symbol} @ ₹{sl_price} | GTT ID: {gtt_id}")
        except Exception as e:
            logger.error(f"GTT SL placement failed: {e}")

    def place_sell_order(self, ticker: str, qty: int) -> str | None:
        """Place a market sell order to exit a position."""
        if not self.kite:
            return None

        symbol = ticker.replace(".NS", "").replace(".BO", "")

        try:
            order_id = self.kite.place_order(
                variety          = self.kite.VARIETY_REGULAR,
                exchange         = self.kite.EXCHANGE_NSE,
                tradingsymbol    = symbol,
                transaction_type = self.kite.TRANSACTION_TYPE_SELL,
                quantity         = qty,
                product          = self.kite.PRODUCT_CNC,
                order_type       = self.kite.ORDER_TYPE_MARKET,
            )
            logger.info(f"SELL order placed: {symbol} x{qty} | Order ID: {order_id}")
            return str(order_id)
        except Exception as e:
            logger.error(f"Sell order failed for {symbol}: {e}")
            return None

    def get_positions(self) -> list[dict]:
        """Get current open positions."""
        if not self.kite:
            return []
        try:
            return self.kite.positions().get("net", [])
        except Exception as e:
            logger.error(f"Position fetch failed: {e}")
            return []

    def get_portfolio_value(self) -> float:
        """Calculate current portfolio value."""
        positions = self.get_positions()
        return sum(
            float(p.get("last_price", 0)) * int(p.get("quantity", 0))
            for p in positions
        )
