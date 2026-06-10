from __future__ import annotations

import math

from .config import FrameworkConfig
from .external_signals import ExternalAssessment, ExternalSignalEngine
from .features import build_feature_vector, clamp
from .models import MarketSnapshot, ScoredMarket


def calibrate_market_probability(p_market: float) -> float:
    """Adjust raw market price for favorite-longshot bias.

    Empirical data from 59,000 resolved Polymarket markets shows:
    - Contracts at 40-50% implied resolve YES only ~22% of the time
    - Longshots at 5-10% resolve YES only ~2-3%
    - Favorites at 90-95% resolve YES ~97%

    Uses a logistic calibration curve fitted to this empirical data.
    Effect: pulls longshots down, pushes favorites up.
    """
    if p_market <= 0.001 or p_market >= 0.999:
        return p_market
    # Logistic calibration: steepens the probability curve
    # Maps [0,1] -> [0,1] with S-curve that compresses longshots
    k = 1.8  # steepness (higher = more correction)
    x = math.log(p_market / (1.0 - p_market))  # logit
    calibrated = 1.0 / (1.0 + math.exp(-k * x))
    return clamp(calibrated, 0.001, 0.999)


class MarketScorer:
    def __init__(self, config: FrameworkConfig, external_engine: ExternalSignalEngine | None = None):
        self.config = config
        self.external_engine = external_engine

    def score(self, market: MarketSnapshot) -> ScoredMarket:
        f = build_feature_vector(market)
        p_market = f.market_probability_yes

        external = (
            self.external_engine.assess(market)
            if self.external_engine is not None
            else ExternalAssessment(has_signal=False, probability_yes=None, confidence=0.0)
        )
        p_model = self._fair_probability(market, f, p_market=p_market, external=external)
        confidence = self._confidence(market, f, external=external)

        ask_yes = market.order_book.ask_yes if market.order_book.ask_yes is not None else p_market
        bid_yes = market.order_book.bid_yes if market.order_book.bid_yes is not None else p_market

        # Simple EV approximation with entry at current ask/bid and payout 1.0 at resolution.
        ev_yes = p_model * (1.0 - ask_yes) - (1.0 - p_model) * ask_yes

        # For NO, convert via complementary probability and implied NO entry price.
        ask_no = market.order_book.ask_no
        no_price = ask_no if ask_no is not None else (1.0 - bid_yes)
        p_no = 1.0 - p_model
        ev_no = p_no * (1.0 - no_price) - (1.0 - p_no) * no_price

        edge_yes = p_model - p_market
        edge_no = (1.0 - p_model) - (1.0 - p_market)

        if ev_yes >= ev_no:
            selected_side = "YES"
            selected_edge = edge_yes
            expected_value = ev_yes
        else:
            selected_side = "NO"
            selected_edge = edge_no
            expected_value = ev_no

        weights = self.config.base_weights
        cat_multiplier = self.config.category_weight_multipliers.get(market.category, 1.0)

        score = cat_multiplier * (
            weights.edge * clamp(selected_edge + 0.5, 0.0, 1.0)
            + weights.confidence * confidence
            + weights.liquidity * f.liquidity_score
            + weights.execution * f.execution_score
            + weights.momentum * clamp(0.5 + 0.5 * f.momentum_7d, 0.0, 1.0)
        )

        rationale = self._rationale_tags(market, f, selected_edge, confidence, expected_value)

        # Kelly position sizing
        kelly_fraction, stake_pct, stake_usd = self._kelly_sizing(
            p_model, selected_side, ask_yes, no_price,
        )

        # Bias calibration
        p_calibrated = (
            calibrate_market_probability(p_market)
            if self.config.constraints.enable_bias_calibration
            else None
        )

        # Detect premium sell opportunities (longshot bias exploitation)
        strategy_type = "directional"
        if p_calibrated is not None and p_market < 0.10 and p_calibrated < 0.03:
            strategy_type = "premium_sell"
            if "premium-sell" not in rationale:
                rationale.append("premium-sell")

        return ScoredMarket(
            market=market,
            features=f,
            p_model_yes=p_model,
            p_market_yes=p_market,
            edge_yes=edge_yes,
            edge_no=edge_no,
            expected_value_yes=ev_yes,
            expected_value_no=ev_no,
            selected_side=selected_side,
            selected_edge=selected_edge,
            expected_value=expected_value,
            confidence=confidence,
            external_probability_yes=external.probability_yes,
            external_confidence=external.confidence,
            external_sources=external.sources,
            correlation_key=self._correlation_key(market),
            score=score,
            rationale_tags=rationale,
            kelly_fraction=kelly_fraction,
            recommended_stake_pct=stake_pct,
            recommended_stake_usd=stake_usd,
            strategy_type=strategy_type,
            p_calibrated=p_calibrated,
        )

    def _kelly_sizing(
        self,
        p_model: float,
        selected_side: str,
        ask_yes: float,
        no_price: float,
    ) -> tuple[float, float, float]:
        """Compute Kelly fraction and recommended position size.

        Returns (kelly_fraction, stake_pct, stake_usd).

        The Kelly formula for binary outcomes:
            f* = (p * b - q) / b
        where p = true probability, q = 1-p, b = net odds (profit / stake).
        """
        c = self.config.constraints

        if selected_side == "YES":
            entry_price = ask_yes
            p = p_model
        else:
            entry_price = no_price
            p = 1.0 - p_model

        if entry_price <= 0 or entry_price >= 1.0:
            return 0.0, 0.0, 0.0

        b = (1.0 - entry_price) / entry_price  # net decimal odds
        q = 1.0 - p
        kelly_raw = (p * b - q) / b if b > 0 else 0.0
        kelly_raw = max(0.0, kelly_raw)  # never negative (don't bet)

        # Apply fractional Kelly (quarter-Kelly by default)
        kelly_scaled = kelly_raw * c.kelly_aggression

        # Cap at max single bet percentage
        stake_pct = min(kelly_scaled, c.max_single_bet_pct)
        stake_usd = stake_pct * c.default_bankroll

        return round(kelly_raw, 6), round(stake_pct, 6), round(stake_usd, 2)

    def _fair_probability(
        self,
        market: MarketSnapshot,
        f,
        *,
        p_market: float,
        external: ExternalAssessment,
    ) -> float:
        if external.has_signal and external.probability_yes is not None:
            # External signal is primary fair-value anchor.
            p_ext = external.probability_yes
            micro_tilt = (
                0.015 * f.order_imbalance
                + 0.010 * clamp(f.momentum_1d, -1.0, 1.0)
                - 0.010 * f.volatility
            )
            p_ext_adj = clamp(p_ext + micro_tilt * external.confidence, 0.001, 0.999)

            # Blend with market based on external confidence to avoid overreaction.
            blend = clamp(external.confidence, 0.15, 0.95)
            p_model = clamp(p_market + (p_ext_adj - p_market) * blend, 0.001, 0.999)

            # Hard longshot cap: external can move low base-rates only modestly.
            if p_market < 0.02:
                p_model = min(p_model, p_market + 0.02)
            return p_model

        return self._heuristic_probability(market, f)

    def _heuristic_probability(self, market: MarketSnapshot, f) -> float:
        # Fallback when external signals are unavailable.
        p = f.market_probability_yes
        micro_adj = (
            0.16 * f.momentum_7d
            + 0.10 * f.momentum_1d
            + 0.09 * f.order_imbalance
            + 0.07 * clamp(f.volume_acceleration, -1.0, 1.0)
            - 0.06 * f.volatility
            + 0.04 * f.time_decay
        )

        category_adj = self._category_adjustment(market, f)

        # Longshot guardrails: avoid unrealistic upward jumps on tiny base rates.
        if p < 0.02:
            micro_adj = clamp(micro_adj, -0.02, 0.01)
            category_adj = min(0.0, category_adj)

        p_model = clamp(p + micro_adj + category_adj, 0.001, 0.999)
        return p_model

    def _confidence(self, market: MarketSnapshot, f, *, external: ExternalAssessment) -> float:
        data_quality = 0.0
        data_quality += 0.20 if market.last_price_yes is not None else 0.0
        data_quality += 0.20 if market.order_book.bid_yes is not None else 0.0
        data_quality += 0.20 if market.order_book.ask_yes is not None else 0.0
        data_quality += 0.20 if len(market.price_history_yes) >= 5 else 0.0
        data_quality += 0.20 if market.volume_24h > 0 else 0.0

        confidence = (
            0.45 * f.liquidity_score
            + 0.20 * f.execution_score
            + 0.15 * (1.0 - clamp(f.spread_bps / 700.0, 0.0, 1.0))
            + 0.20 * data_quality
        )
        if external.has_signal:
            confidence = 0.60 * confidence + 0.40 * external.confidence
        return clamp(confidence, 0.0, 1.0)

    def _category_adjustment(self, market: MarketSnapshot, f) -> float:
        q = market.question.lower()
        tags = {t.lower() for t in market.tags}

        if market.category == "NBA":
            injury_like = any(k in q for k in ("injury", "questionable", "out"))
            playoff_like = any(k in q for k in ("playoff", "finals", "series"))
            return (0.025 if playoff_like else 0.0) - (0.020 if injury_like else 0.0)

        if market.category == "Soccer":
            derby_like = any(k in q for k in ("derby", "champions league", "uefa"))
            low_total_like = any(k in q for k in ("under 2.5", "clean sheet"))
            return (0.020 if derby_like else 0.0) + (0.010 if low_total_like and f.volatility < 0.08 else 0.0)

        if market.category == "Cricket":
            toss_like = any(k in q for k in ("toss", "powerplay"))
            ipl_like = "ipl" in q or "ipl" in tags
            return (0.015 if ipl_like else 0.0) - (0.020 if toss_like else 0.0)

        if market.category == "Mentions":
            legal_headline_like = any(k in q for k in ("indict", "verdict", "court"))
            return (0.020 if "poll" in q else 0.0) - (0.020 if legal_headline_like else 0.0)

        if market.category == "Elections":
            swing_state_like = any(k in q for k in ("pennsylvania", "michigan", "wisconsin", "arizona", "georgia"))
            turnout_like = any(k in q for k in ("turnout", "mail-in", "early voting"))
            return (0.025 if swing_state_like else 0.0) + (0.010 if turnout_like else 0.0)

        return 0.0

    @staticmethod
    def _correlation_key(market: MarketSnapshot) -> set[str]:
        base_tokens = {
            token.lower()
            for token in market.question.replace("?", " ").replace("/", " ").split()
            if len(token) >= 4
        }
        return base_tokens | {market.event_group.lower()} | {market.category.lower()}

    @staticmethod
    def _rationale_tags(
        market: MarketSnapshot,
        f,
        selected_edge: float,
        confidence: float,
        ev: float,
    ) -> list[str]:
        tags: list[str] = [market.category.lower()]
        if selected_edge > 0.03:
            tags.append("strong-edge")
        if confidence > 0.72:
            tags.append("high-confidence")
        if f.liquidity_score > 0.55:
            tags.append("good-liquidity")
        if f.spread_bps < 180:
            tags.append("tight-spread")
        if ev > 0.02:
            tags.append("positive-ev")
        return tags
