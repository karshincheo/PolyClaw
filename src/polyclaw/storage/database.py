import sqlite3

from polyclaw.config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    slug TEXT,
    description TEXT DEFAULT '',
    outcomes TEXT DEFAULT '[]',
    outcome_prices TEXT DEFAULT '[]',
    clob_token_ids TEXT DEFAULT '[]',
    active INTEGER DEFAULT 1,
    closed INTEGER DEFAULT 0,
    accepting_orders INTEGER DEFAULT 0,
    neg_risk INTEGER DEFAULT 0,
    liquidity REAL DEFAULT 0,
    volume REAL DEFAULT 0,
    volume_24hr REAL DEFAULT 0,
    volume_1wk REAL DEFAULT 0,
    volume_1mo REAL DEFAULT 0,
    order_price_min_tick_size REAL DEFAULT 0.01,
    order_min_size REAL DEFAULT 5.0,
    end_date TEXT,
    start_date TEXT,
    group_item_title TEXT,
    created_at TEXT,
    updated_at TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    bids TEXT DEFAULT '[]',
    asks TEXT DEFAULT '[]',
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    midpoint REAL,
    neg_risk INTEGER DEFAULT 0,
    timestamp INTEGER NOT NULL,
    PRIMARY KEY (token_id, timestamp)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    buy_price REAL,
    sell_price REAL,
    midpoint REAL,
    timestamp INTEGER NOT NULL,
    PRIMARY KEY (token_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active);
CREATE INDEX IF NOT EXISTS idx_markets_condition_id ON markets(condition_id);
CREATE INDEX IF NOT EXISTS idx_orderbook_market ON orderbook_snapshots(market_id);
CREATE INDEX IF NOT EXISTS idx_price_market ON price_snapshots(market_id);
"""


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn
