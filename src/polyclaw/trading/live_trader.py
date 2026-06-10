import logging
import uuid

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    BalanceAllowanceParams,
    MarketOrderArgs,
    OpenOrderParams,
    OrderArgs,
    OrderType,
)

from polyclaw.config import settings
from polyclaw.trading.interface import TraderInterface
from polyclaw.trading.models import (
    OrderResult,
    OrderStatus,
    PortfolioSummary,
    Position,
    TradeOrder,
    TradeOrderType,
)

logger = logging.getLogger(__name__)


class LiveTrader(TraderInterface):
    """Executes real trades on Polymarket via py-clob-client.

    Requires:
      - POLYCLAW_PRIVATE_KEY set in .env (Polygon EOA private key)
      - USDC balance on Polygon
      - USDC approved to the Polymarket exchange contract

    Setup flow:
      1. Set POLYCLAW_PRIVATE_KEY in .env
      2. Call trader.setup() once to derive API credentials
      3. Ensure your wallet has USDC on Polygon and allowances are set
    """

    def __init__(self, private_key: str | None = None):
        pk = private_key or settings.private_key
        if not pk:
            raise ValueError(
                "Private key required for live trading. "
                "Set POLYCLAW_PRIVATE_KEY in your .env file."
            )

        self._client = ClobClient(
            host=settings.clob_base_url,
            chain_id=settings.chain_id,
            key=pk,
        )
        self._creds: ApiCreds | None = None

    def setup(self) -> ApiCreds:
        """Derive or create API credentials (run once, then store creds)."""
        logger.info("Deriving API credentials...")
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)
        self._creds = creds
        logger.info("API credentials ready. API key: %s...", creds.api_key[:12])
        return creds

    def set_creds(self, api_key: str, api_secret: str, api_passphrase: str):
        """Set previously derived API credentials."""
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
        self._client.set_api_creds(creds)
        self._creds = creds

    def _ensure_creds(self):
        if self._creds is None:
            raise RuntimeError("Call setup() or set_creds() before trading.")

    def place_order(self, order: TradeOrder) -> OrderResult:
        self._ensure_creds()

        try:
            if order.order_type == TradeOrderType.MARKET:
                return self._place_market_order(order)
            else:
                return self._place_limit_order(order)
        except Exception as e:
            logger.error("Order failed: %s", e, exc_info=True)
            return OrderResult(
                order_id="",
                status=OrderStatus.REJECTED,
                message=str(e),
            )

    def _place_market_order(self, order: TradeOrder) -> OrderResult:
        args = MarketOrderArgs(
            token_id=order.token_id,
            amount=order.size,
            side=order.side.value,
            fee_rate_bps=0,
            nonce=0,
            order_type=OrderType.FOK,
        )
        signed = self._client.create_market_order(args)
        resp = self._client.post_order(signed, orderType=OrderType.FOK)

        order_id = resp.get("orderID", resp.get("id", str(uuid.uuid4())))
        status = OrderStatus.FILLED if resp.get("success") else OrderStatus.REJECTED

        logger.info("Market order %s: %s %s %.2f on %s",
                     status.value, order.side.value, order.size,
                     order.token_id[:20], order.market_question[:40])

        return OrderResult(
            order_id=order_id,
            status=status,
            filled_size=order.size if status == OrderStatus.FILLED else None,
            message=str(resp),
        )

    def _place_limit_order(self, order: TradeOrder) -> OrderResult:
        if order.price is None:
            raise ValueError("Limit orders require a price.")

        args = OrderArgs(
            token_id=order.token_id,
            price=order.price,
            size=order.size,
            side=order.side.value,
            fee_rate_bps=0,
            nonce=0,
            expiration=0,  # GTC
        )
        signed = self._client.create_order(args)
        resp = self._client.post_order(signed, orderType=OrderType.GTC)

        order_id = resp.get("orderID", resp.get("id", str(uuid.uuid4())))
        logger.info("Limit order placed: %s %s %.0f @ %.4f on %s",
                     order.side.value, order.outcome, order.size,
                     order.price, order.market_question[:40])

        return OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            message=str(resp),
        )

    def cancel_order(self, order_id: str) -> bool:
        self._ensure_creds()
        try:
            self._client.cancel(order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return False

    def cancel_all(self) -> bool:
        self._ensure_creds()
        try:
            self._client.cancel_all()
            logger.info("Cancelled all open orders")
            return True
        except Exception as e:
            logger.error("Cancel all failed: %s", e)
            return False

    def get_positions(self) -> list[Position]:
        # Live positions must be tracked via the Data API or on-chain
        # py-clob-client doesn't expose a positions endpoint directly
        logger.warning("Live position tracking requires Data API integration (Phase 2+)")
        return []

    def get_portfolio(self) -> PortfolioSummary:
        balance = self.get_balance()
        positions = self.get_positions()
        return PortfolioSummary(
            cash_balance=balance,
            positions=positions,
            total_position_value=0.0,
            total_equity=balance,
            total_realized_pnl=0.0,
            total_unrealized_pnl=0.0,
        )

    def get_balance(self) -> float:
        self._ensure_creds()
        try:
            resp = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type="COLLATERAL")
            )
            return float(resp.get("balance", 0))
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return 0.0

    def get_open_orders(self) -> list[dict]:
        self._ensure_creds()
        return self._client.get_orders(OpenOrderParams())
