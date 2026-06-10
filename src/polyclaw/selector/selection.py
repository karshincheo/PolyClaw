from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .config import FrameworkConfig
from .models import ScoredMarket

EDGE_EPSILON = 1e-4


class BetSelector:
    def __init__(self, config: FrameworkConfig):
        self.config = config

    def select(self, scored_markets: list[ScoredMarket]) -> dict[str, list[ScoredMarket]]:
        by_cat: dict[str, list[ScoredMarket]] = defaultdict(list)
        for m in scored_markets:
            by_cat[m.market.category].append(m)

        result: dict[str, list[ScoredMarket]] = {}
        for category, candidates in by_cat.items():
            result[category] = self._select_for_category(candidates)
        return result

    def _select_for_category(self, candidates: list[ScoredMarket]) -> list[ScoredMarket]:
        c = self.config.constraints
        ranked = sorted(candidates, key=lambda m: m.score, reverse=True)

        selected: list[ScoredMarket] = []
        event_group_counts: dict[str, int] = defaultdict(int)
        selected_ids: set[str] = set()

        # Tier 1: strict quality and diversification constraints.
        for candidate in ranked:
            if not self._passes_strict(candidate, c):
                continue
            if self._violates_diversification(
                candidate,
                selected,
                event_group_counts,
                max_per_event_group=c.max_per_event_group,
                max_pairwise_correlation=c.max_pairwise_correlation,
            ):
                continue
            self._append_candidate(candidate, selected, selected_ids, event_group_counts, fill_tag=None)
            if len(selected) >= c.picks_per_category:
                return selected

        # Tier 2: relaxed quality with lightly loosened diversification.
        for candidate in ranked:
            if len(selected) >= c.picks_per_category:
                return selected
            if candidate.market.market_id in selected_ids:
                continue
            if not self._passes_relaxed(candidate, c):
                continue
            if self._violates_diversification(
                candidate,
                selected,
                event_group_counts,
                max_per_event_group=max(2, c.max_per_event_group),
                max_pairwise_correlation=min(0.85, c.max_pairwise_correlation + 0.15),
            ):
                continue
            self._append_candidate(candidate, selected, selected_ids, event_group_counts, fill_tag="relaxed-fill")

        # Tier 3: coverage mode to ensure 5 picks/category when market quality is sparse.
        for candidate in ranked:
            if len(selected) >= c.picks_per_category:
                return selected
            if candidate.market.market_id in selected_ids:
                continue
            if not self._passes_coverage(candidate, c):
                continue
            self._append_candidate(candidate, selected, selected_ids, event_group_counts, fill_tag="coverage-fill")

        return selected

    @staticmethod
    def _append_candidate(
        candidate: ScoredMarket,
        selected: list[ScoredMarket],
        selected_ids: set[str],
        event_group_counts: dict[str, int],
        *,
        fill_tag: str | None,
    ) -> None:
        if fill_tag and fill_tag not in candidate.rationale_tags:
            candidate.rationale_tags.append(fill_tag)
        selected.append(candidate)
        selected_ids.add(candidate.market.market_id)
        event_group_counts[candidate.market.event_group.lower()] += 1

    @staticmethod
    def _passes_strict(m: ScoredMarket, c) -> bool:
        edge_threshold = max(EDGE_EPSILON, c.min_edge, _alpha_edge_floor(m, c))
        return (
            not _is_extreme_probability(m, c)
            and
            m.confidence >= c.min_confidence
            and m.selected_edge > edge_threshold
            and m.features.liquidity_score >= c.min_liquidity_score
            and m.features.spread_bps <= c.max_spread_bps
            and m.expected_value > 0.0
            and not (m.selected_side == "YES" and m.p_market_yes < c.min_market_prob_yes_for_yes_bet)
        )

    @staticmethod
    def _passes_relaxed(m: ScoredMarket, c) -> bool:
        edge_threshold = max(EDGE_EPSILON, c.relaxed_min_edge, _alpha_edge_floor(m, c))
        return (
            not _is_extreme_probability(m, c)
            and
            m.confidence >= c.relaxed_min_confidence
            and m.selected_edge > edge_threshold
            and m.features.liquidity_score >= c.relaxed_min_liquidity_score
            and m.features.spread_bps <= c.relaxed_max_spread_bps
            and m.expected_value > 0.0
            and not (m.selected_side == "YES" and m.p_market_yes < c.min_market_prob_yes_for_yes_bet)
        )

    @staticmethod
    def _passes_coverage(m: ScoredMarket, c) -> bool:
        return (
            not _is_extreme_probability(m, c)
            and m.features.spread_bps <= c.coverage_max_spread_bps
        )

    def _violates_diversification(
        self,
        candidate: ScoredMarket,
        selected: list[ScoredMarket],
        event_group_counts: dict[str, int],
        *,
        max_per_event_group: int,
        max_pairwise_correlation: float,
    ) -> bool:
        event_group = candidate.market.event_group.lower()
        if event_group_counts[event_group] >= max_per_event_group:
            return True
        return self._too_correlated(candidate, selected, max_pairwise_correlation)

    @staticmethod
    def _too_correlated(
        candidate: ScoredMarket,
        selected: list[ScoredMarket],
        max_pairwise_correlation: float,
    ) -> bool:
        for item in selected:
            corr = _jaccard_similarity(candidate.correlation_key, item.correlation_key)
            if corr >= max_pairwise_correlation:
                return True
        return False


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _alpha_edge_floor(m: ScoredMarket, c) -> float:
    end_time = m.market.end_time
    if end_time is None:
        return c.very_long_min_edge

    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    hours = (end_time - datetime.now(timezone.utc)).total_seconds() / 3600.0

    if hours <= c.quick_horizon_hours:
        return c.quick_min_edge
    if hours <= c.medium_horizon_hours:
        return c.medium_min_edge
    if hours <= c.long_horizon_hours:
        return c.long_min_edge
    return c.very_long_min_edge


def _is_extreme_probability(m: ScoredMarket, c) -> bool:
    return m.p_market_yes < c.min_market_prob_yes or m.p_market_yes > c.max_market_prob_yes
