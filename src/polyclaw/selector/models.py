from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class OrderBookSnapshot:
    bid_yes: float | None = None
    ask_yes: float | None = None
    bid_no: float | None = None
    ask_no: float | None = None
    depth_yes: float = 0.0
    depth_no: float = 0.0


@dataclass
class MarketSnapshot:
    market_id: str
    question: str
    category: str
    event_group: str
    end_time: datetime | None
    volume_24h: float
    volume_7d: float
    liquidity: float
    last_price_yes: float | None
    price_history_yes: list[float] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    order_book: OrderBookSnapshot = field(default_factory=OrderBookSnapshot)


@dataclass
class FeatureVector:
    market_probability_yes: float
    spread_bps: float
    liquidity_score: float
    order_imbalance: float
    momentum_1d: float
    momentum_7d: float
    volume_acceleration: float
    volatility: float
    time_decay: float
    execution_score: float


@dataclass
class ScoredMarket:
    market: MarketSnapshot
    features: FeatureVector
    p_model_yes: float
    p_market_yes: float
    edge_yes: float
    edge_no: float
    expected_value_yes: float
    expected_value_no: float
    selected_side: str
    selected_edge: float
    expected_value: float
    confidence: float
    external_probability_yes: float | None
    external_confidence: float
    external_sources: list[str]
    correlation_key: set[str]
    score: float
    rationale_tags: list[str]
    # Kelly position sizing (Phase 1)
    kelly_fraction: float = 0.0         # raw Kelly fraction (before aggression scaling)
    recommended_stake_pct: float = 0.0  # after aggression + caps
    recommended_stake_usd: float = 0.0  # dollar amount (requires bankroll context)
    strategy_type: str = "directional"  # "directional", "premium_sell", "arbitrage"
    p_calibrated: float | None = None   # bias-adjusted market probability

    def as_output_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["market"]["end_time"] = (
            self.market.end_time.isoformat() if self.market.end_time else None
        )
        return payload
