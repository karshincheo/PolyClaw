from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .features import clamp
from .models import MarketSnapshot


@dataclass
class ExternalSignal:
    category: str
    source: str
    probability_yes: float
    confidence: float = 0.7
    weight: float = 1.0
    market_ref: str | None = None
    match_terms: list[str] = field(default_factory=list)
    timestamp: datetime | None = None


@dataclass
class ExternalAssessment:
    has_signal: bool
    probability_yes: float | None
    confidence: float
    sources: list[str] = field(default_factory=list)
    matched_count: int = 0
    disagreement: float = 0.0


class ExternalSignalEngine:
    def __init__(self, signals: list[ExternalSignal] | None = None):
        self.signals = signals or []

    @classmethod
    def from_file(cls, path: str | Path) -> "ExternalSignalEngine":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = payload.get("signals", payload)
        if not isinstance(rows, list):
            raise ValueError("External signal file must be a list or {'signals': [...]}.")

        signals: list[ExternalSignal] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            prob = _as_float(row.get("probability_yes"))
            if prob is None:
                continue
            signals.append(
                ExternalSignal(
                    category=str(row.get("category") or "").strip(),
                    source=str(row.get("source") or "unknown"),
                    probability_yes=clamp(prob, 0.001, 0.999),
                    confidence=clamp(_as_float(row.get("confidence")) or 0.7, 0.05, 1.0),
                    weight=max(0.01, _as_float(row.get("weight")) or 1.0),
                    market_ref=(str(row.get("market_ref")).strip() if row.get("market_ref") else None),
                    match_terms=[str(x).strip().lower() for x in (row.get("match_terms") or []) if str(x).strip()],
                    timestamp=_parse_ts(row.get("timestamp")),
                )
            )
        return cls(signals)

    def assess(self, market: MarketSnapshot) -> ExternalAssessment:
        candidates: list[tuple[ExternalSignal, float]] = []
        for s in self.signals:
            if s.category and s.category != market.category:
                continue
            match_score = self._match_score(s, market)
            if match_score <= 0:
                continue
            candidates.append((s, match_score))

        if not candidates:
            return ExternalAssessment(
                has_signal=False,
                probability_yes=None,
                confidence=0.0,
            )

        weighted_probs: list[tuple[float, float]] = []
        source_names: list[str] = []
        for signal, match_score in candidates:
            recency_w = _recency_weight(signal.timestamp)
            w = signal.weight * signal.confidence * recency_w * match_score
            weighted_probs.append((signal.probability_yes, w))
            source_names.append(signal.source)

        total_w = sum(w for _, w in weighted_probs)
        if total_w <= 0:
            return ExternalAssessment(has_signal=False, probability_yes=None, confidence=0.0)

        p_raw = sum(p * w for p, w in weighted_probs) / total_w
        var = sum(w * ((p - p_raw) ** 2) for p, w in weighted_probs) / total_w
        disagreement = math.sqrt(max(0.0, var))

        # Confidence increases with signal weight, decreases with disagreement.
        conf_strength = clamp(total_w / 3.0, 0.0, 1.0)
        confidence = clamp(conf_strength * (1.0 - 1.5 * disagreement), 0.05, 0.98)

        # Keep external probability faithful to source consensus; uncertainty is
        # represented separately via confidence and later blending in scoring.
        p_cal = clamp(p_raw, 0.001, 0.999)

        return ExternalAssessment(
            has_signal=True,
            probability_yes=p_cal,
            confidence=confidence,
            sources=sorted(set(source_names)),
            matched_count=len(candidates),
            disagreement=disagreement,
        )

    @staticmethod
    def _match_score(signal: ExternalSignal, market: MarketSnapshot) -> float:
        text = f"{market.question} {market.event_group} {market.metadata.get('slug', '')}".lower()
        score = 0.0

        if signal.market_ref:
            market_ref = signal.market_ref.lower()
            if market_ref == market.market_id.lower():
                score += 1.2
            elif market_ref in text:
                score += 1.0

        if signal.match_terms:
            for term in signal.match_terms:
                if term and term in text:
                    score += 0.4

        if score == 0.0 and not signal.market_ref and not signal.match_terms:
            # Category-level generic signal, low confidence match.
            score = 0.2

        return clamp(score, 0.0, 1.5)


def _parse_ts(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _recency_weight(ts: datetime | None) -> float:
    if ts is None:
        return 0.75
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    if age_h <= 0:
        return 1.0
    return clamp(math.exp(-age_h / 72.0), 0.2, 1.0)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
