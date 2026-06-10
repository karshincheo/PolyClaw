from __future__ import annotations

from dataclasses import dataclass, field

CATEGORIES = ("NBA", "Soccer", "Cricket", "Mentions", "Elections")


@dataclass(frozen=True)
class CategoryWeights:
    edge: float
    confidence: float
    liquidity: float
    execution: float
    momentum: float


@dataclass(frozen=True)
class SelectionConstraints:
    min_confidence: float = 0.55
    min_edge: float = 0.0
    min_liquidity_score: float = 0.20
    min_market_prob_yes_for_yes_bet: float = 0.02
    min_market_prob_yes: float = 0.03
    max_market_prob_yes: float = 0.97
    max_spread_bps: float = 450.0
    relaxed_min_confidence: float = 0.45
    relaxed_min_edge: float = 0.0
    relaxed_min_liquidity_score: float = 0.12
    relaxed_max_spread_bps: float = 1200.0
    coverage_max_spread_bps: float = 5000.0
    quick_horizon_hours: float = 72.0
    medium_horizon_hours: float = 24.0 * 14.0
    long_horizon_hours: float = 24.0 * 45.0
    quick_min_edge: float = 0.005
    medium_min_edge: float = 0.012
    long_min_edge: float = 0.020
    very_long_min_edge: float = 0.035
    max_per_event_group: int = 1
    max_pairwise_correlation: float = 0.65
    picks_per_category: int = 5
    # Kelly position sizing
    kelly_aggression: float = 0.25       # quarter-Kelly (conservative)
    max_single_bet_pct: float = 0.05     # 5% max of bankroll per bet
    max_correlated_exposure_pct: float = 0.20  # 20% max across correlated bets
    max_drawdown_stop_pct: float = 0.50  # stop trading at 50% drawdown
    default_bankroll: float = 10_000.0   # default bankroll for sizing
    # Favorite-longshot bias
    enable_bias_calibration: bool = True # adjust market prices for known bias


@dataclass(frozen=True)
class FrameworkConfig:
    constraints: SelectionConstraints = field(default_factory=SelectionConstraints)
    base_weights: CategoryWeights = field(
        default_factory=lambda: CategoryWeights(
            edge=0.42,
            confidence=0.23,
            liquidity=0.16,
            execution=0.11,
            momentum=0.08,
        )
    )
    category_weight_multipliers: dict[str, float] = field(
        default_factory=lambda: {
            "NBA": 1.00,
            "Soccer": 0.98,
            "Cricket": 1.02,
            "Mentions": 1.07,
            "Elections": 1.05,
        }
    )
    # These endpoints mirror the commonly documented separation between
    # Polymarket's market metadata API and CLOB execution API.
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
