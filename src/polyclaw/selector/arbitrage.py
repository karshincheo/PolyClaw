"""Intra-market arbitrage scanner.

Detects opportunities where buying YES + NO simultaneously costs less than $1.00,
guaranteeing risk-free profit regardless of outcome.

Polymarket structure: YES + NO always resolves to exactly $1.00.
If best_ask(YES) + best_ask(NO) < $1.00, the difference is free money.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .features import clamp

logger = logging.getLogger(__name__)

# Polymarket protocol fee: 2% on net winnings
PROTOCOL_FEE_RATE = 0.02
# Polygon gas cost per transaction (approximately)
GAS_COST_USD = 0.007


@dataclass
class ArbitrageOpportunity:
    market_id: str
    question: str
    yes_ask: float
    no_ask: float
    combined_cost: float
    gross_profit: float    # 1.0 - combined_cost
    net_profit: float      # after fees and gas
    net_profit_pct: float  # as percentage of cost
    category: str = ""


def scan_for_arbitrage(
    markets: list[dict],
    min_profit_cents: float = 0.005,
) -> list[ArbitrageOpportunity]:
    """Scan a list of market dicts for intra-market arbitrage.

    Args:
        markets: List of market dicts with 'outcomePrices' or bid/ask data.
        min_profit_cents: Minimum net profit in dollars to report.

    Returns:
        List of ArbitrageOpportunity, sorted by net_profit descending.
    """
    opportunities = []

    for m in markets:
        try:
            yes_ask, no_ask = _extract_asks(m)
            if yes_ask is None or no_ask is None:
                continue
            if yes_ask <= 0 or no_ask <= 0:
                continue

            combined = yes_ask + no_ask
            if combined >= 1.0:
                continue

            gross_profit = 1.0 - combined
            # Fee is 2% on net winnings (the gross profit)
            fee = gross_profit * PROTOCOL_FEE_RATE
            net_profit = gross_profit - fee - (2 * GAS_COST_USD)  # 2 txns

            if net_profit < min_profit_cents:
                continue

            net_pct = (net_profit / combined) * 100

            opportunities.append(ArbitrageOpportunity(
                market_id=str(m.get("id", m.get("market_id", ""))),
                question=str(m.get("question", "")),
                yes_ask=yes_ask,
                no_ask=no_ask,
                combined_cost=round(combined, 4),
                gross_profit=round(gross_profit, 4),
                net_profit=round(net_profit, 4),
                net_profit_pct=round(net_pct, 2),
                category=str(m.get("category", "")),
            ))
        except Exception:
            continue

    opportunities.sort(key=lambda o: o.net_profit, reverse=True)
    return opportunities


def _extract_asks(m: dict) -> tuple[float | None, float | None]:
    """Extract best ask prices for YES and NO from market data."""
    import json as _json

    # Try bestAsk fields directly
    yes_ask = _as_float(m.get("bestAsk"))
    no_ask = _as_float(m.get("bestAskNo"))

    # Fallback to outcomePrices (Gamma API format)
    if yes_ask is None or no_ask is None:
        raw_prices = m.get("outcomePrices")
        if isinstance(raw_prices, str):
            try:
                raw_prices = _json.loads(raw_prices)
            except Exception:
                raw_prices = None
        if isinstance(raw_prices, list) and len(raw_prices) >= 2:
            if yes_ask is None:
                yes_ask = _as_float(raw_prices[0])
            if no_ask is None:
                no_ask = _as_float(raw_prices[1])

    return yes_ask, no_ask


def _as_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
