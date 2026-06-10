from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import CATEGORIES, FrameworkConfig
from .external_signals import ExternalSignalEngine
from .models import ScoredMarket
from .polymarket_client import PolymarketPublicClient
from .scoring import MarketScorer
from .selection import BetSelector


class SelectionPipeline:
    def __init__(
        self,
        config: FrameworkConfig | None = None,
        *,
        external_signals_path: str | Path | None = None,
        require_external_signal: bool = False,
    ):
        self.config = config or FrameworkConfig()
        self.client = PolymarketPublicClient(self.config)
        self.external_engine = (
            ExternalSignalEngine.from_file(external_signals_path)
            if external_signals_path
            else ExternalSignalEngine([])
        )
        self.scorer = MarketScorer(self.config, external_engine=self.external_engine)
        self.selector = BetSelector(self.config)
        self.require_external_signal = require_external_signal

    def run_with_public_api(self, *, limit: int = 600) -> dict[str, list[ScoredMarket]]:
        raw_markets = self.client.fetch_markets(limit=limit, closed=False)
        self.client.attach_event_slugs(raw_markets, closed=False, scan_limit=max(4000, limit * 2))
        return self.run(raw_markets)

    def run(self, raw_markets: list[dict]) -> dict[str, list[ScoredMarket]]:
        normalized = self.client.normalize_markets(raw_markets)
        filtered = [
            m
            for m in normalized
            if m.category in CATEGORIES and self._is_public_tradable_market(m.metadata)
        ]
        scored = [self.scorer.score(m) for m in filtered]
        if self.require_external_signal:
            scored = [s for s in scored if s.external_probability_yes is not None]
        selected = self.selector.select(scored)
        self._enrich_selected_event_slugs(selected)

        # Always return each target category with a list.
        return {category: selected.get(category, []) for category in CATEGORIES}

    def run_from_file(self, input_path: str | Path) -> dict[str, list[ScoredMarket]]:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        rows = payload["markets"] if isinstance(payload, dict) and "markets" in payload else payload
        if not isinstance(rows, list):
            raise ValueError("Input JSON must be a list of market objects or an object with a 'markets' list.")
        return self.run(rows)

    @staticmethod
    def to_output_dict(results: dict[str, list[ScoredMarket]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for category, picks in results.items():
            out[category] = [
                {
                    "market_id": p.market.market_id,
                    "question": p.market.question,
                    "market_url": SelectionPipeline._market_url(p.market.metadata),
                    "side": p.selected_side,
                    "score": round(p.score, 4),
                    "confidence": round(p.confidence, 4),
                    "p_model_yes": round(p.p_model_yes, 4),
                    "p_market_yes": round(p.p_market_yes, 4),
                    "p_external_yes": (round(p.external_probability_yes, 4) if p.external_probability_yes is not None else None),
                    "external_confidence": round(p.external_confidence, 4),
                    "external_sources": p.external_sources,
                    "selected_edge": round(p.selected_edge, 4),
                    "expected_value": round(p.expected_value, 4),
                    "liquidity_score": round(p.features.liquidity_score, 4),
                    "spread_bps": round(p.features.spread_bps, 2),
                    "hours_to_resolution": SelectionPipeline._hours_to_resolution(p.market.end_time),
                    "event_group": p.market.event_group,
                    "condition_id": p.market.metadata.get("conditionId"),
                    "rationale_tags": p.rationale_tags,
                    "kelly_fraction": round(p.kelly_fraction, 4),
                    "recommended_stake_pct": round(p.recommended_stake_pct, 4),
                    "recommended_stake_usd": round(p.recommended_stake_usd, 2),
                    "strategy_type": p.strategy_type,
                    "p_calibrated": round(p.p_calibrated, 4) if p.p_calibrated is not None else None,
                }
                for p in picks
            ]
        return out

    @staticmethod
    def write_output(path: str | Path, results: dict[str, list[ScoredMarket]]) -> None:
        serialized = SelectionPipeline.to_output_dict(results)
        Path(path).write_text(json.dumps(serialized, indent=2), encoding="utf-8")

    @staticmethod
    def _market_url(metadata: dict[str, Any]) -> str | None:
        event_slug = metadata.get("eventSlug")
        condition_id = metadata.get("conditionId")
        if event_slug:
            base = f"https://polymarket.com/event/{event_slug}"
            return f"{base}?tid={condition_id}" if condition_id else base
        slug = metadata.get("slug")
        if slug:
            return f"https://polymarket.com/event/{slug}"
        return None

    @staticmethod
    def _hours_to_resolution(end_time) -> float | None:
        if end_time is None:
            return None
        dt = end_time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        return round(hours, 2)

    def _enrich_selected_event_slugs(self, selected: dict[str, list[ScoredMarket]]) -> None:
        market_ids = {
            p.market.market_id
            for picks in selected.values()
            for p in picks
            if p.market.market_id
        }
        if not market_ids:
            return
        # Use a deep scan for selected picks to avoid dead URL fallbacks.
        lookup = self.client.fetch_event_slug_lookup(set(market_ids), closed=False, scan_limit=120000)
        if not lookup:
            return
        for picks in selected.values():
            for item in picks:
                event_slug = lookup.get(item.market.market_id)
                if event_slug:
                    item.market.metadata["eventSlug"] = event_slug

    @staticmethod
    def _is_public_tradable_market(metadata: dict[str, Any]) -> bool:
        if not isinstance(metadata, dict):
            return False
        if metadata.get("closed") is True:
            return False
        if metadata.get("archived") is True:
            return False
        if metadata.get("active") is False:
            return False
        if metadata.get("acceptingOrders") is False:
            return False
        return True
