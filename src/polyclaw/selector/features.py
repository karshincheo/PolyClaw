from __future__ import annotations

import math
from datetime import datetime, timezone

from .models import FeatureVector, MarketSnapshot


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(num: float, den: float, default: float = 0.0) -> float:
    if abs(den) < 1e-12:
        return default
    return num / den


def build_feature_vector(market: MarketSnapshot) -> FeatureVector:
    book = market.order_book
    price_yes = infer_market_probability_yes(market)

    bid = book.bid_yes if book.bid_yes is not None else market.last_price_yes
    ask = book.ask_yes if book.ask_yes is not None else market.last_price_yes

    spread_bps = 10000.0 * max(0.0, (ask or price_yes or 0.5) - (bid or price_yes or 0.5))

    total_depth = max(0.0, book.depth_yes + book.depth_no)
    depth_component = clamp(math.log1p(total_depth) / 12.0, 0.0, 1.0)
    liq_component = clamp(math.log1p(max(0.0, market.liquidity)) / 14.0, 0.0, 1.0)
    liquidity_score = clamp(0.55 * liq_component + 0.45 * depth_component, 0.0, 1.0)

    order_imbalance = safe_div(book.depth_yes - book.depth_no, total_depth, 0.0)

    p_hist = market.price_history_yes[-30:]
    momentum_1d = (p_hist[-1] - p_hist[-2]) if len(p_hist) >= 2 else 0.0
    momentum_7d = (p_hist[-1] - p_hist[0]) if len(p_hist) >= 2 else 0.0

    if market.volume_7d and market.volume_7d > 0:
        baseline_daily = market.volume_7d / 7.0
        volume_acceleration = safe_div(
            market.volume_24h - baseline_daily,
            max(1.0, baseline_daily),
            0.0,
        )
    else:
        # Missing 7d volume should be neutral, not a bullish signal.
        volume_acceleration = 0.0

    volatility = _price_volatility(p_hist)
    time_decay = _time_decay(market.end_time)

    spread_penalty = clamp(spread_bps / 900.0, 0.0, 1.0)
    execution_score = clamp(liquidity_score * (1.0 - 0.6 * spread_penalty), 0.0, 1.0)

    return FeatureVector(
        market_probability_yes=price_yes,
        spread_bps=spread_bps,
        liquidity_score=liquidity_score,
        order_imbalance=order_imbalance,
        momentum_1d=momentum_1d,
        momentum_7d=momentum_7d,
        volume_acceleration=volume_acceleration,
        volatility=volatility,
        time_decay=time_decay,
        execution_score=execution_score,
    )


def infer_market_probability_yes(market: MarketSnapshot) -> float:
    book = market.order_book

    if book.bid_yes is not None and book.ask_yes is not None:
        return clamp((book.bid_yes + book.ask_yes) / 2.0, 0.001, 0.999)

    if market.last_price_yes is not None:
        return clamp(market.last_price_yes, 0.001, 0.999)

    if book.ask_no is not None:
        return clamp(1.0 - book.ask_no, 0.001, 0.999)

    # Fallback to metadata outcome prices from Gamma payload.
    outcome_prices = market.metadata.get("outcomePrices")
    outcomes = market.metadata.get("outcomes")
    if isinstance(outcome_prices, list) and outcome_prices:
        if isinstance(outcomes, list) and len(outcomes) == len(outcome_prices):
            for idx, name in enumerate(outcomes):
                if str(name).strip().lower() == "yes":
                    try:
                        return clamp(float(outcome_prices[idx]), 0.001, 0.999)
                    except (TypeError, ValueError):
                        pass
        try:
            return clamp(float(outcome_prices[0]), 0.001, 0.999)
        except (TypeError, ValueError, IndexError):
            pass

    return 0.50


def _price_volatility(history: list[float]) -> float:
    if len(history) < 3:
        return 0.0
    mean = sum(history) / len(history)
    var = sum((x - mean) ** 2 for x in history) / max(1, len(history) - 1)
    return clamp(math.sqrt(var), 0.0, 1.0)


def _time_decay(end_time: datetime | None) -> float:
    if end_time is None:
        return 0.5
    now = datetime.now(timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    hours = (end_time - now).total_seconds() / 3600.0
    if hours <= 0:
        return 0.0
    # Higher value when resolution is near (but not immediate)
    return clamp(math.exp(-hours / 72.0), 0.0, 1.0)
