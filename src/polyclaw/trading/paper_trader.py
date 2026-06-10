import logging
import sqlite3
import time
import uuid

from polyclaw.clients.clob import ClobClientWrapper
from polyclaw.trading.interface import TraderInterface
from polyclaw.trading.models import (
    OrderResult,
    OrderStatus,
    PortfolioSummary,
    Position,
    Side,
    TradeOrder,
    TradeOrderType,
)

logger = logging.getLogger(__name__)

PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_config (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    requested_price REAL,
    filled_price REAL,
    filled_size REAL,
    total_cost REAL,
    fee REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    timestamp BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    token_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    shares REAL NOT NULL DEFAULT 0,
    avg_entry_price REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_open_orders (
    id TEXT PRIMARY KEY,
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    filled_size REAL NOT NULL DEFAULT 0,
    timestamp BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_token ON paper_trades(token_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_ts ON paper_trades(timestamp);
"""


class PaperTrader(TraderInterface):
    """Simulated trading engine that uses real Polymarket orderbook data.

    Supports two backends:
    - SQLite (local dev, default)
    - Supabase Postgres (production / Vercel)

    Set via POLYCLAW_DB_BACKEND=sqlite|supabase in .env.
    """

    def __init__(
        self,
        db_path: str = "paper_trading.db",
        starting_balance: float = 10_000.0,
        clob: ClobClientWrapper | None = None,
        backend: str | None = None,
    ):
        from polyclaw.config import settings
        self._backend = backend or settings.db_backend
        self._sb = None  # Supabase client (lazy)
        self._conn = None  # SQLite connection (lazy)

        if self._backend == "supabase":
            from polyclaw.storage.supabase_db import SupabaseDB
            self._sb = SupabaseDB(url=settings.supabase_url, key=settings.supabase_key)
            # Tables + initial data already created via migration
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(PAPER_SCHEMA)
            try:
                self._conn.execute("ALTER TABLE paper_trades ADD COLUMN fee REAL NOT NULL DEFAULT 0")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass
            # Initialize balance if first run
            row = self._conn.execute(
                "SELECT value FROM paper_config WHERE key = 'cash_balance'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO paper_config (key, value) VALUES ('cash_balance', ?)",
                    (str(starting_balance),),
                )
                self._conn.execute(
                    "INSERT INTO paper_config (key, value) VALUES ('starting_balance', ?)",
                    (str(starting_balance),),
                )
                self._conn.commit()

        self.clob = clob or ClobClientWrapper()
        self._fee_cache: dict[str, int] = {}
        logger.info("Paper trading initialized (%s backend)", self._backend)

    # ── DB helpers ──────────────────────────────────────────────

    def _get_cash(self) -> float:
        if self._backend == "supabase":
            row = self._sb.select_one("paper_config", where={"key": "cash_balance"})
            return float(row["value"]) if row else 0.0
        else:
            row = self._conn.execute(
                "SELECT value FROM paper_config WHERE key = 'cash_balance'"
            ).fetchone()
            return float(row["value"])

    def _set_cash(self, amount: float):
        if self._backend == "supabase":
            self._sb.update("paper_config", {"value": str(amount)}, where={"key": "cash_balance"})
        else:
            self._conn.execute(
                "UPDATE paper_config SET value = ? WHERE key = 'cash_balance'",
                (str(amount),),
            )

    def _get_fee_rate(self, token_id: str) -> float:
        if token_id in self._fee_cache:
            return self._fee_cache[token_id] / 10_000
        try:
            bps = self.clob._client.get_fee_rate_bps(token_id)
        except Exception:
            bps = 0
            logger.warning("Failed to fetch fee rate for %s, defaulting to 0", token_id[:30])
        self._fee_cache[token_id] = bps
        return bps / 10_000

    def _get_held_shares(self, token_id: str) -> float:
        if self._backend == "supabase":
            row = self._sb.select_one("paper_positions", where={"token_id": token_id})
            return float(row["shares"]) if row else 0.0
        else:
            row = self._conn.execute(
                "SELECT shares FROM paper_positions WHERE token_id = ?",
                (token_id,),
            ).fetchone()
            return row["shares"] if row else 0.0

    def _insert_trade(self, trade_id, order, avg_price, total_shares, total_cost, fee, status):
        now = int(time.time() * 1000)
        data = {
            "id": trade_id,
            "token_id": order.token_id,
            "market_id": order.market_id,
            "market_question": order.market_question,
            "outcome": order.outcome,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "requested_price": order.price,
            "filled_price": avg_price,
            "filled_size": total_shares,
            "total_cost": total_cost,
            "fee": fee,
            "status": status.value,
            "timestamp": now,
        }
        if self._backend == "supabase":
            self._sb.insert("paper_trades", data)
        else:
            self._conn.execute(
                """INSERT INTO paper_trades
                   (id, token_id, market_id, market_question, outcome, side,
                    order_type, requested_price, filled_price, filled_size,
                    total_cost, fee, status, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, order.token_id, order.market_id, order.market_question,
                 order.outcome, order.side.value, order.order_type.value,
                 order.price, avg_price, total_shares, total_cost, fee,
                 status.value, now),
            )
            self._conn.commit()

    # ── Order execution ─────────────────────────────────────────

    def place_order(self, order: TradeOrder) -> OrderResult:
        if order.order_type == TradeOrderType.MARKET:
            return self._execute_market_order(order)
        else:
            return self._execute_limit_order(order)

    def _execute_market_order(self, order: TradeOrder) -> OrderResult:
        trade_id = str(uuid.uuid4())[:12]

        try:
            ob = self.clob.get_orderbook(order.token_id)
        except Exception as e:
            return OrderResult(
                order_id=trade_id, status=OrderStatus.REJECTED,
                message=f"Failed to fetch orderbook: {e}",
            )

        if order.side == Side.BUY:
            levels = sorted(ob.asks, key=lambda lvl: lvl.price)
        else:
            levels = sorted(ob.bids, key=lambda lvl: lvl.price, reverse=True)

        if not levels:
            return OrderResult(
                order_id=trade_id, status=OrderStatus.REJECTED,
                message=f"No {'asks' if order.side == Side.BUY else 'bids'} in orderbook",
            )

        cash = self._get_cash()

        if order.side == Side.SELL:
            held = self._get_held_shares(order.token_id)
            if held <= 0:
                return OrderResult(
                    order_id=trade_id, status=OrderStatus.REJECTED,
                    message="No shares held to sell",
                )
            effective_size = min(order.size, held)
        else:
            effective_size = order.size

        remaining = effective_size
        total_cost = 0.0
        total_shares = 0.0

        for level in levels:
            if remaining <= 0:
                break
            if order.side == Side.BUY:
                cash_left = cash - total_cost
                if cash_left <= 0:
                    break
                usdc_at_level = level.size * level.price
                usdc_to_fill = min(remaining, usdc_at_level, cash_left)
                shares_filled = usdc_to_fill / level.price
                total_cost += usdc_to_fill
                total_shares += shares_filled
                remaining -= usdc_to_fill
            else:
                shares_at_level = min(remaining, level.size)
                usdc_received = shares_at_level * level.price
                total_cost += usdc_received
                total_shares += shares_at_level
                remaining -= shares_at_level

        if total_shares == 0:
            return OrderResult(
                order_id=trade_id, status=OrderStatus.REJECTED,
                message="Insufficient liquidity in orderbook",
            )

        avg_price = total_cost / total_shares if total_shares > 0 else 0
        fee_rate = self._get_fee_rate(order.token_id)
        fee = total_cost * fee_rate

        if order.side == Side.BUY:
            self._set_cash(cash - total_cost - fee)
        else:
            self._set_cash(cash + total_cost - fee)
        self._update_position(order, total_shares, avg_price)
        self._insert_trade(trade_id, order, avg_price, total_shares, total_cost, fee, OrderStatus.FILLED)

        fee_str = f" (fee ${fee:.2f})" if fee > 0 else ""
        logger.info(
            "PAPER %s %s: %.2f shares @ avg $%.4f (cost $%.2f%s) | %s [%s]",
            order.side.value, order.outcome, total_shares, avg_price,
            total_cost, fee_str, order.market_question[:40], trade_id,
        )

        return OrderResult(
            order_id=trade_id, status=OrderStatus.FILLED,
            filled_price=avg_price, filled_size=total_shares, total_cost=total_cost,
        )

    def _execute_limit_order(self, order: TradeOrder) -> OrderResult:
        trade_id = str(uuid.uuid4())[:12]

        if order.price is None:
            return OrderResult(
                order_id=trade_id, status=OrderStatus.REJECTED,
                message="Limit orders require a price.",
            )

        try:
            ob = self.clob.get_orderbook(order.token_id)
        except Exception as e:
            return OrderResult(
                order_id=trade_id, status=OrderStatus.REJECTED,
                message=f"Failed to fetch orderbook: {e}",
            )

        if order.side == Side.BUY:
            fillable_levels = [a for a in ob.asks if a.price <= order.price]
            fillable_levels.sort(key=lambda lvl: lvl.price)
        else:
            fillable_levels = [b for b in ob.bids if b.price >= order.price]
            fillable_levels.sort(key=lambda lvl: lvl.price, reverse=True)

        if not fillable_levels:
            now = int(time.time() * 1000)
            data = {
                "id": trade_id, "token_id": order.token_id,
                "market_id": order.market_id, "market_question": order.market_question,
                "outcome": order.outcome, "side": order.side.value,
                "price": order.price, "size": order.size,
                "filled_size": 0, "timestamp": now,
            }
            if self._backend == "supabase":
                self._sb.insert("paper_open_orders", data)
            else:
                self._conn.execute(
                    """INSERT INTO paper_open_orders
                       (id, token_id, market_id, market_question, outcome,
                        side, price, size, filled_size, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (trade_id, order.token_id, order.market_id,
                     order.market_question, order.outcome,
                     order.side.value, order.price, order.size, now),
                )
                self._conn.commit()

            return OrderResult(
                order_id=trade_id, status=OrderStatus.PENDING,
                message="Limit order placed (not yet fillable at current prices)",
            )

        effective_size = order.size
        if order.side == Side.SELL:
            held = self._get_held_shares(order.token_id)
            if held <= 0:
                return OrderResult(
                    order_id=trade_id, status=OrderStatus.REJECTED,
                    message="No shares held to sell",
                )
            effective_size = min(order.size, held)

        cash = self._get_cash()
        remaining = effective_size
        total_cost = 0.0
        total_shares = 0.0

        for level in fillable_levels:
            if remaining <= 0:
                break
            fill = min(remaining, level.size)
            if order.side == Side.BUY:
                level_cost = fill * level.price
                cash_left = cash - total_cost
                if level_cost > cash_left:
                    fill = cash_left / level.price
                    if fill <= 0:
                        break
            total_cost += fill * level.price
            total_shares += fill
            remaining -= fill

        if total_shares == 0:
            return OrderResult(
                order_id=trade_id, status=OrderStatus.REJECTED,
                message="Insufficient funds or liquidity",
            )

        avg_price = total_cost / total_shares if total_shares > 0 else order.price
        fee_rate = self._get_fee_rate(order.token_id)
        fee = total_cost * fee_rate

        if order.side == Side.BUY:
            self._set_cash(cash - total_cost - fee)
        else:
            self._set_cash(cash + total_cost - fee)
        self._update_position(order, total_shares, avg_price)

        status = OrderStatus.FILLED if remaining <= 0 else OrderStatus.PARTIALLY_FILLED
        self._insert_trade(trade_id, order, avg_price, total_shares, total_cost, fee, status)

        return OrderResult(
            order_id=trade_id, status=status,
            filled_price=avg_price, filled_size=total_shares, total_cost=total_cost,
        )

    # ── Position management ─────────────────────────────────────

    def _update_position(self, order: TradeOrder, shares: float, price: float):
        if self._backend == "supabase":
            self._update_position_supabase(order, shares, price)
        else:
            self._update_position_sqlite(order, shares, price)

    def _update_position_supabase(self, order: TradeOrder, shares: float, price: float):
        row = self._sb.select_one("paper_positions", where={"token_id": order.token_id})

        if order.side == Side.BUY:
            if row:
                old_shares = float(row["shares"])
                old_avg = float(row["avg_entry_price"])
                new_shares = old_shares + shares
                new_avg = ((old_shares * old_avg) + (shares * price)) / new_shares if new_shares > 0 else 0
                self._sb.update("paper_positions",
                    {"shares": new_shares, "avg_entry_price": new_avg},
                    where={"token_id": order.token_id},
                )
            else:
                self._sb.insert("paper_positions", {
                    "token_id": order.token_id, "market_id": order.market_id,
                    "market_question": order.market_question, "outcome": order.outcome,
                    "shares": shares, "avg_entry_price": price, "realized_pnl": 0,
                })
        else:  # SELL
            if not row or float(row["shares"]) <= 0:
                return
            old_shares = float(row["shares"])
            old_avg = float(row["avg_entry_price"])
            sell_shares = min(shares, old_shares)
            realized = sell_shares * (price - old_avg)
            new_shares = old_shares - sell_shares
            old_realized = float(row["realized_pnl"])

            if new_shares <= 0.001:
                self._sb.update("paper_positions",
                    {"shares": 0, "realized_pnl": old_realized + realized},
                    where={"token_id": order.token_id},
                )
            else:
                self._sb.update("paper_positions",
                    {"shares": new_shares, "realized_pnl": old_realized + realized},
                    where={"token_id": order.token_id},
                )

    def _update_position_sqlite(self, order: TradeOrder, shares: float, price: float):
        row = self._conn.execute(
            "SELECT * FROM paper_positions WHERE token_id = ?",
            (order.token_id,),
        ).fetchone()

        if order.side == Side.BUY:
            if row:
                old_shares = row["shares"]
                old_avg = row["avg_entry_price"]
                new_shares = old_shares + shares
                new_avg = ((old_shares * old_avg) + (shares * price)) / new_shares if new_shares > 0 else 0
                self._conn.execute(
                    "UPDATE paper_positions SET shares = ?, avg_entry_price = ? WHERE token_id = ?",
                    (new_shares, new_avg, order.token_id),
                )
            else:
                self._conn.execute(
                    """INSERT INTO paper_positions
                       (token_id, market_id, market_question, outcome, shares, avg_entry_price, realized_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, 0)""",
                    (order.token_id, order.market_id, order.market_question,
                     order.outcome, shares, price),
                )
        else:  # SELL
            if not row or row["shares"] <= 0:
                return
            old_shares = row["shares"]
            old_avg = row["avg_entry_price"]
            sell_shares = min(shares, old_shares)
            realized = sell_shares * (price - old_avg)
            new_shares = old_shares - sell_shares

            if new_shares <= 0.001:
                self._conn.execute(
                    "UPDATE paper_positions SET shares = 0, realized_pnl = realized_pnl + ? WHERE token_id = ?",
                    (realized, order.token_id),
                )
            else:
                self._conn.execute(
                    "UPDATE paper_positions SET shares = ?, realized_pnl = realized_pnl + ? WHERE token_id = ?",
                    (new_shares, realized, order.token_id),
                )
        self._conn.commit()

    # ── Read operations ─────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if self._backend == "supabase":
            count = self._sb.delete("paper_open_orders", where={"id": order_id})
            cancelled = count > 0
        else:
            result = self._conn.execute(
                "DELETE FROM paper_open_orders WHERE id = ?", (order_id,)
            )
            self._conn.commit()
            cancelled = result.rowcount > 0
        if cancelled:
            logger.info("PAPER cancelled order %s", order_id)
        return cancelled

    def get_positions(self) -> list[Position]:
        if self._backend == "supabase":
            rows = self._sb.select("paper_positions", where={"shares": "gt.0.001"})
        else:
            rows = self._conn.execute(
                "SELECT * FROM paper_positions WHERE shares > 0.001"
            ).fetchall()

        positions = []
        for r in rows:
            current_price = None
            unrealized = 0.0
            try:
                snap = self.clob.get_price(r["token_id"], market_id=r["market_id"])
                current_price = snap.midpoint
                if current_price is not None:
                    unrealized = float(r["shares"]) * (current_price - float(r["avg_entry_price"]))
            except Exception as e:
                logger.warning("Failed to fetch price for %s: %s", str(r["token_id"])[:30], e)

            positions.append(Position(
                token_id=r["token_id"],
                market_id=r["market_id"],
                market_question=r["market_question"],
                outcome=r["outcome"],
                shares=float(r["shares"]),
                avg_entry_price=float(r["avg_entry_price"]),
                current_price=current_price,
                unrealized_pnl=unrealized,
            ))
        return positions

    def get_portfolio(self) -> PortfolioSummary:
        cash = self._get_cash()
        positions = self.get_positions()

        total_position_value = sum(
            p.shares * (p.current_price or p.avg_entry_price)
            for p in positions
        )
        total_unrealized = sum(p.unrealized_pnl or 0 for p in positions)

        # Sum realized PnL from ALL positions (including closed ones)
        if self._backend == "supabase":
            all_pos = self._sb.select("paper_positions")
            total_realized = sum(float(r["realized_pnl"]) for r in all_pos)
        else:
            realized_row = self._conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) as total FROM paper_positions"
            ).fetchone()
            total_realized = realized_row["total"]

        return PortfolioSummary(
            cash_balance=cash,
            positions=positions,
            total_position_value=total_position_value,
            total_equity=cash + total_position_value,
            total_realized_pnl=total_realized,
            total_unrealized_pnl=total_unrealized,
        )

    def get_balance(self) -> float:
        return self._get_cash()

    def get_trade_history(self) -> list[dict]:
        if self._backend == "supabase":
            rows = self._sb.select("paper_trades", order="timestamp.desc")
            return [dict(r) for r in rows]
        else:
            rows = self._conn.execute(
                "SELECT * FROM paper_trades ORDER BY timestamp DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def check_open_orders(self):
        if self._backend == "supabase":
            rows = self._sb.select("paper_open_orders")
        else:
            rows = self._conn.execute("SELECT * FROM paper_open_orders").fetchall()

        for r in rows:
            order = TradeOrder(
                token_id=r["token_id"], market_id=r["market_id"],
                market_question=r["market_question"], outcome=r["outcome"],
                side=Side(r["side"]), order_type=TradeOrderType.LIMIT,
                price=float(r["price"]), size=float(r["size"]) - float(r["filled_size"]),
            )
            result = self._execute_limit_order(order)
            if result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                if self._backend == "supabase":
                    self._sb.delete("paper_open_orders", where={"id": r["id"]})
                else:
                    self._conn.execute("DELETE FROM paper_open_orders WHERE id = ?", (r["id"],))
                    self._conn.commit()

    def reset(self):
        if self._backend == "supabase":
            self._sb.delete_all("paper_trades", pk_column="id")
            self._sb.delete_all("paper_positions", pk_column="token_id")
            self._sb.delete_all("paper_open_orders", pk_column="id")
            starting = self._sb.select_one("paper_config", where={"key": "starting_balance"})
            balance = float(starting["value"]) if starting else 10_000.0
            self._sb.update("paper_config", {"value": str(balance)}, where={"key": "cash_balance"})
        else:
            starting = self._conn.execute(
                "SELECT value FROM paper_config WHERE key = 'starting_balance'"
            ).fetchone()
            balance = float(starting["value"]) if starting else 10_000.0
            self._conn.executescript("""
                DELETE FROM paper_trades;
                DELETE FROM paper_positions;
                DELETE FROM paper_open_orders;
            """)
            self._set_cash(balance)
            self._conn.commit()

        self._fee_cache.clear()
        logger.info("Paper trading reset. Balance: $%.2f", balance)

    def close(self):
        if self._backend == "supabase" and self._sb:
            self._sb.close()
        elif self._conn:
            self._conn.close()
