from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from polyclaw.storage.supabase_db import SupabaseDB

AGENTS_TABLE = "arena_agents"
BETS_TABLE = "arena_bets"
TICKER_TABLE = "arena_ticker_events"
KEYS_TABLE = "arena_agent_keys"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


class AgentArenaSimulation:
    def __init__(
        self,
        *,
        db_path: str | Path = "data/agent_arena.db",
        state_path: str | Path = "data/agent_arena_state.json",
        starting_balance: float = 1000.0,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
    ):
        self.db_path = Path(db_path)
        self.state_path = Path(state_path)
        self.starting_balance = float(starting_balance)
        self._supabase: SupabaseDB | None = None
        if supabase_url and supabase_key:
            self._supabase = SupabaseDB(supabase_url, supabase_key)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def _use_supabase(self) -> bool:
        return self._supabase is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        if self._use_supabase:
            return
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    name TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    side TEXT NOT NULL,
                    stake REAL NOT NULL,
                    shares REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    expected_value REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    settled_at TEXT,
                    exit_price REAL,
                    pnl REAL,
                    status TEXT NOT NULL DEFAULT 'OPEN'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ticker_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_keys (
                    agent_name TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def ensure_agent(self, name: str) -> None:
        now = _iso_now()
        if self._use_supabase:
            row = self._supabase.select_one(AGENTS_TABLE, where={"name": name})
            if row is None:
                self._supabase.insert(
                    AGENTS_TABLE,
                    {"name": name, "balance": self.starting_balance, "realized_pnl": 0.0, "created_at": now, "updated_at": now},
                )
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agents (name, balance, realized_pnl, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, self.starting_balance, now, now),
            )
            conn.commit()

    def get_agent_balance(self, name: str) -> float:
        self.ensure_agent(name)
        if self._use_supabase:
            row = self._supabase.select_one(AGENTS_TABLE, where={"name": name})
            return float(row["balance"]) if row else self.starting_balance
        with self._connect() as conn:
            row = conn.execute("SELECT balance FROM agents WHERE name = ?", (name,)).fetchone()
            return float(row["balance"]) if row else self.starting_balance

    def record_bet(self, *, agent_name: str, recommendation: dict[str, Any], side: str, stake: float) -> None:
        self.ensure_agent(agent_name)
        side = side.upper()
        if side not in {"YES", "NO"}:
            side = "YES"
        entry_price = float(recommendation.get("p_market_yes", 0.5) or 0.5)
        entry_price = min(max(entry_price, 0.01), 0.99)
        price_for_side = entry_price if side == "YES" else (1.0 - entry_price)
        shares = stake / price_for_side if price_for_side > 0 else 0.0
        now = _iso_now()

        if self._use_supabase:
            row = self._supabase.select_one(AGENTS_TABLE, where={"name": agent_name})
            current_balance = float(row["balance"]) if row else self.starting_balance
            if stake <= 0 or stake > current_balance:
                return
            self._supabase.insert(
                BETS_TABLE,
                {
                    "agent_name": agent_name,
                    "market_id": str(recommendation.get("market_id", "")),
                    "question": str(recommendation.get("question", "")),
                    "side": side,
                    "stake": stake,
                    "shares": shares,
                    "entry_price": entry_price,
                    "score": float(recommendation.get("score", 0.0) or 0.0),
                    "confidence": float(recommendation.get("confidence", 0.0) or 0.0),
                    "expected_value": float(recommendation.get("expected_value", 0.0) or 0.0),
                    "opened_at": now,
                    "status": "OPEN",
                },
            )
            self._supabase.update(AGENTS_TABLE, {"balance": current_balance - stake, "updated_at": now}, where={"name": agent_name})
            self._supabase.insert(
                TICKER_TABLE,
                {
                    "event_type": "BET",
                    "message": f"{agent_name} just bet {stake:.2f} coins on {side} - {recommendation.get('question', 'Unknown market')}",
                    "created_at": now,
                },
            )
            return

        with self._connect() as conn:
            current_balance_row = conn.execute("SELECT balance FROM agents WHERE name = ?", (agent_name,)).fetchone()
            current_balance = float(current_balance_row["balance"]) if current_balance_row else self.starting_balance
            if stake <= 0 or stake > current_balance:
                return
            conn.execute(
                """
                INSERT INTO bets (
                    agent_name, market_id, question, side, stake, shares, entry_price,
                    score, confidence, expected_value, opened_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_name,
                    str(recommendation.get("market_id", "")),
                    str(recommendation.get("question", "")),
                    side,
                    stake,
                    shares,
                    entry_price,
                    float(recommendation.get("score", 0.0) or 0.0),
                    float(recommendation.get("confidence", 0.0) or 0.0),
                    float(recommendation.get("expected_value", 0.0) or 0.0),
                    now,
                ),
            )
            conn.execute("UPDATE agents SET balance = balance - ?, updated_at = ? WHERE name = ?", (stake, now, agent_name))
            conn.execute(
                "INSERT INTO ticker_events (event_type, message, created_at) VALUES (?, ?, ?)",
                ("BET", f"{agent_name} just bet {stake:.2f} coins on {side} - {recommendation.get('question', 'Unknown market')}", now),
            )
            conn.commit()

    def settle_open_bets(self, market_prices_yes: dict[str, float], *, min_age_seconds: int = 3600) -> int:
        cutoff = _utc_now() - timedelta(seconds=min_age_seconds)
        settled = 0
        if self._use_supabase:
            open_bets = self._supabase.select(BETS_TABLE, where={"status": "OPEN"})
            for bet in open_bets:
                opened_at = datetime.fromisoformat(str(bet["opened_at"]).replace("Z", "+00:00"))
                if opened_at > cutoff:
                    continue
                market_id = str(bet["market_id"])
                if market_id not in market_prices_yes:
                    continue
                exit_price_yes = min(max(float(market_prices_yes[market_id]), 0.01), 0.99)
                entry_price_yes = float(bet["entry_price"])
                side = str(bet["side"]).upper()
                shares = float(bet["shares"])
                stake = float(bet["stake"])
                pnl = (exit_price_yes - entry_price_yes) * shares if side == "YES" else (entry_price_yes - exit_price_yes) * shares
                now = _iso_now()
                self._supabase.update(BETS_TABLE, {"status": "SETTLED", "settled_at": now, "exit_price": exit_price_yes, "pnl": pnl}, where={"id": int(bet["id"])})
                agent = self._supabase.select_one(AGENTS_TABLE, where={"name": str(bet["agent_name"])})
                if agent:
                    self._supabase.update(
                        AGENTS_TABLE,
                        {"balance": float(agent["balance"]) + stake + pnl, "realized_pnl": float(agent["realized_pnl"]) + pnl, "updated_at": now},
                        where={"name": str(bet["agent_name"])},
                    )
                self._supabase.insert(
                    TICKER_TABLE,
                    {"event_type": "SETTLEMENT", "message": f"Settled {bet['agent_name']} on {bet['market_id']} ({side}) with PnL {pnl:+.2f}", "created_at": now},
                )
                settled += 1
            return settled

        with self._connect() as conn:
            open_bets = conn.execute("SELECT * FROM bets WHERE status = 'OPEN'").fetchall()
            for bet in open_bets:
                opened_at = datetime.fromisoformat(str(bet["opened_at"]).replace("Z", "+00:00"))
                if opened_at > cutoff:
                    continue
                market_id = str(bet["market_id"])
                if market_id not in market_prices_yes:
                    continue
                exit_price_yes = min(max(float(market_prices_yes[market_id]), 0.01), 0.99)
                entry_price_yes = float(bet["entry_price"])
                side = str(bet["side"]).upper()
                shares = float(bet["shares"])
                stake = float(bet["stake"])
                pnl = (exit_price_yes - entry_price_yes) * shares if side == "YES" else (entry_price_yes - exit_price_yes) * shares
                now = _iso_now()
                conn.execute("UPDATE bets SET status = 'SETTLED', settled_at = ?, exit_price = ?, pnl = ? WHERE id = ?", (now, exit_price_yes, pnl, int(bet["id"])))
                conn.execute("UPDATE agents SET balance = balance + ?, realized_pnl = realized_pnl + ?, updated_at = ? WHERE name = ?", (stake + pnl, pnl, now, str(bet["agent_name"])))
                conn.execute(
                    "INSERT INTO ticker_events (event_type, message, created_at) VALUES (?, ?, ?)",
                    ("SETTLEMENT", f"Settled {bet['agent_name']} on {bet['market_id']} ({side}) with PnL {pnl:+.2f}", now),
                )
                settled += 1
            conn.commit()
        return settled

    def leaderboard(self) -> list[dict[str, Any]]:
        if self._use_supabase:
            rows = self._supabase.select(AGENTS_TABLE, order="balance.desc")
        else:
            with self._connect() as conn:
                rows = conn.execute("SELECT name, balance, realized_pnl FROM agents ORDER BY balance DESC").fetchall()
        return [
            {
                "agent": str(row["name"]),
                "balance": round(float(row["balance"]), 2),
                "realized_pnl": round(float(row["realized_pnl"]), 2),
                "total_pnl": round(float(row["balance"]) - self.starting_balance, 2),
            }
            for row in rows
        ]

    def active_bets(self) -> list[dict[str, Any]]:
        if self._use_supabase:
            rows = self._supabase.select(BETS_TABLE, where={"status": "OPEN"}, order="opened_at.desc", limit=100)
            return [
                {
                    "agent_name": row["agent_name"],
                    "market_id": row["market_id"],
                    "question": row["question"],
                    "side": row["side"],
                    "stake": row["stake"],
                    "shares": row["shares"],
                    "entry_price": row["entry_price"],
                    "opened_at": row["opened_at"],
                }
                for row in rows
            ]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_name, market_id, question, side, stake, shares, entry_price, opened_at
                FROM bets WHERE status = 'OPEN' ORDER BY opened_at DESC LIMIT 100
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def ticker(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._use_supabase:
            return [dict(row) for row in self._supabase.select(TICKER_TABLE, order="id.desc", limit=limit)]
        with self._connect() as conn:
            rows = conn.execute("SELECT event_type, message, created_at FROM ticker_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def register_agent_key(self, agent_name: str) -> str:
        self.ensure_agent(agent_name)
        raw_key = f"arena_{secrets.token_urlsafe(24)}"
        now = _iso_now()
        if self._use_supabase:
            existing = self._supabase.select_one(KEYS_TABLE, where={"agent_name": agent_name})
            if existing is None:
                self._supabase.insert(KEYS_TABLE, {"agent_name": agent_name, "key_hash": self._hash_key(raw_key), "created_at": now})
            else:
                self._supabase.update(KEYS_TABLE, {"key_hash": self._hash_key(raw_key), "created_at": now}, where={"agent_name": agent_name})
            return raw_key
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_keys (agent_name, key_hash, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_name) DO UPDATE SET key_hash = excluded.key_hash
                """,
                (agent_name, self._hash_key(raw_key), now),
            )
            conn.commit()
        return raw_key

    def resolve_agent_by_key(self, raw_key: str) -> str | None:
        if not raw_key:
            return None
        key_hash = self._hash_key(raw_key)
        if self._use_supabase:
            row = self._supabase.select_one(KEYS_TABLE, where={"key_hash": key_hash})
            return str(row["agent_name"]) if row else None
        with self._connect() as conn:
            row = conn.execute("SELECT agent_name FROM agent_keys WHERE key_hash = ?", (key_hash,)).fetchone()
        return str(row["agent_name"]) if row else None

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "generated_at": None,
                "starting_balance": self.starting_balance,
                "leaderboard": [],
                "ticker": [],
                "active_bets": [],
                "markets": [],
            }
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def submit_external_decision(self, *, agent_name: str, market_id: str, side: str, stake: float) -> dict[str, Any]:
        state = self.load_state()
        markets = state.get("markets", [])
        recommendation = next((m for m in markets if str(m.get("market_id")) == str(market_id)), None)
        if recommendation is None:
            raise ValueError(f"Unknown market_id={market_id}. Run an arena tick first.")
        balance = self.get_agent_balance(agent_name)
        if stake <= 0:
            raise ValueError("Stake must be positive.")
        if stake > balance:
            raise ValueError(f"Insufficient balance: {balance:.2f}")
        self.record_bet(agent_name=agent_name, recommendation=recommendation, side=side, stake=stake)
        return {
            "agent": agent_name,
            "market_id": market_id,
            "side": side,
            "stake": round(stake, 2),
            "balance_after": round(self.get_agent_balance(agent_name), 2),
        }

    def export_state(self, *, markets: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "generated_at": _iso_now(),
            "starting_balance": self.starting_balance,
            "leaderboard": self.leaderboard(),
            "ticker": self.ticker(),
            "active_bets": self.active_bets(),
            "markets": markets,
        }
        try:
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass
        return payload
