import json
import sqlite3

from polyclaw.models.market import Market
from polyclaw.models.orderbook import OrderBook, OrderLevel
from polyclaw.models.price import PriceSnapshot


class MarketRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_markets(self, markets: list[Market]) -> int:
        sql = """
            INSERT OR REPLACE INTO markets (
                id, question, condition_id, slug, description,
                outcomes, outcome_prices, clob_token_ids,
                active, closed, accepting_orders, neg_risk,
                liquidity, volume, volume_24hr, volume_1wk, volume_1mo,
                order_price_min_tick_size, order_min_size,
                end_date, start_date, group_item_title,
                created_at, updated_at, fetched_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, datetime('now')
            )
        """
        rows = [
            (
                m.id, m.question, m.condition_id, m.slug, m.description,
                json.dumps(m.outcomes), json.dumps(m.outcome_prices), json.dumps(m.clob_token_ids),
                m.active, m.closed, m.accepting_orders, m.neg_risk,
                m.liquidity, m.volume, m.volume_24hr, m.volume_1wk, m.volume_1mo,
                m.order_price_min_tick_size, m.order_min_size,
                m.end_date, m.start_date, m.group_item_title,
                m.created_at, m.updated_at,
            )
            for m in markets
        ]
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def get_active_token_ids(self) -> list[tuple[str, str]]:
        """Returns (token_id, condition_id) for all active markets' tokens."""
        rows = self.conn.execute(
            "SELECT clob_token_ids, condition_id FROM markets WHERE active = 1"
        ).fetchall()
        result = []
        for row in rows:
            token_ids = json.loads(row["clob_token_ids"])
            for tid in token_ids:
                if tid:
                    result.append((tid, row["condition_id"]))
        return result

    def get_market_count(self, active_only: bool = True) -> int:
        if active_only:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM markets WHERE active = 1").fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM markets").fetchone()
        return row["cnt"]


class OrderBookRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_snapshots(self, orderbooks: list[OrderBook]) -> int:
        sql = """
            INSERT OR REPLACE INTO orderbook_snapshots (
                token_id, market_id, bids, asks,
                best_bid, best_ask, spread, midpoint,
                neg_risk, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                ob.token_id, ob.market_id,
                json.dumps([{"price": lvl.price, "size": lvl.size} for lvl in ob.bids]),
                json.dumps([{"price": lvl.price, "size": lvl.size} for lvl in ob.asks]),
                ob.best_bid, ob.best_ask, ob.spread, ob.midpoint,
                ob.neg_risk, ob.timestamp,
            )
            for ob in orderbooks
        ]
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def get_latest(self, token_id: str) -> OrderBook | None:
        row = self.conn.execute(
            "SELECT * FROM orderbook_snapshots WHERE token_id = ? ORDER BY timestamp DESC LIMIT 1",
            (token_id,),
        ).fetchone()
        if not row:
            return None
        return OrderBook(
            token_id=row["token_id"],
            market_id=row["market_id"],
            bids=[OrderLevel(**lvl) for lvl in json.loads(row["bids"])],
            asks=[OrderLevel(**lvl) for lvl in json.loads(row["asks"])],
            best_bid=row["best_bid"],
            best_ask=row["best_ask"],
            spread=row["spread"],
            midpoint=row["midpoint"],
            neg_risk=bool(row["neg_risk"]),
            timestamp=row["timestamp"],
        )


class PriceRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_snapshots(self, prices: list[PriceSnapshot]) -> int:
        sql = """
            INSERT OR REPLACE INTO price_snapshots (
                token_id, market_id, buy_price, sell_price, midpoint, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
        """
        rows = [
            (p.token_id, p.market_id, p.buy_price, p.sell_price, p.midpoint, p.timestamp)
            for p in prices
        ]
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def get_latest(self, token_id: str) -> PriceSnapshot | None:
        row = self.conn.execute(
            "SELECT * FROM price_snapshots WHERE token_id = ? ORDER BY timestamp DESC LIMIT 1",
            (token_id,),
        ).fetchone()
        if not row:
            return None
        return PriceSnapshot(
            token_id=row["token_id"],
            market_id=row["market_id"],
            buy_price=row["buy_price"],
            sell_price=row["sell_price"],
            midpoint=row["midpoint"],
            timestamp=row["timestamp"],
        )
