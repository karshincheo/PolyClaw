from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import FrameworkConfig
from .models import MarketSnapshot, OrderBookSnapshot


class PolymarketPublicClient:
    """
    Thin adapter around public Polymarket API patterns.

    Assumed public structure:
    - Market metadata endpoint (Gamma-style): `/markets`
    - CLOB orderbook endpoint: `/book`
    - CLOB trades endpoint: `/trades`

    Exact field naming can differ by endpoint version. We normalize any
    commonly seen aliases to an internal schema.
    """

    def __init__(self, config: FrameworkConfig):
        self.config = config
        # Observed in Polymarket /sports metadata: cricket-family markets share tag 517.
        self._cricket_tag_id = 517
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

    def fetch_markets(self, *, limit: int = 500, closed: bool = False) -> list[dict]:
        # Gamma endpoints often cap per-page sizes; fetch with offsets.
        target = max(1, int(limit))
        collected: list[dict] = []
        seen_ids: set[str] = set()

        query_templates = [
            {"closed": str(closed).lower()},
            {"active": "true", "closed": str(closed).lower()},
        ]

        for template in query_templates:
            if collected:
                break
            rows = self._fetch_market_pages(template, limit=target)
            self._merge_rows(collected, seen_ids, rows, max_items=target)

        # Pull cricket-tagged markets as a targeted supplement to avoid
        # missing cricket markets from broad-feed ordering.
        cricket_rows = self._fetch_market_pages(
            {"closed": str(closed).lower(), "tag_id": self._cricket_tag_id},
            limit=max(200, min(target, 1200)),
        )
        self._merge_rows(collected, seen_ids, cricket_rows, max_items=target + len(cricket_rows))

        return collected

    def _fetch_market_pages(self, base_params: dict, *, limit: int) -> list[dict]:
        target = max(1, int(limit))
        page_size = min(1000, target)
        offset = 0
        rows_out: list[dict] = []

        while len(rows_out) < target:
            params = dict(base_params)
            params["limit"] = min(page_size, target - len(rows_out))
            params["offset"] = offset
            query = urlencode(params)
            url = f"{self.config.gamma_base_url}/markets?{query}"

            try:
                payload = self._get_json(url, timeout=20)
            except HTTPError as err:
                if err.code in {401, 403, 404}:
                    break
                raise

            rows = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
            if not isinstance(rows, list) or not rows:
                break

            rows_out.extend(rows)
            offset += len(rows)
            if len(rows) < params["limit"]:
                break

        return rows_out

    @staticmethod
    def _merge_rows(collected: list[dict], seen_ids: set[str], rows: list[dict], *, max_items: int) -> None:
        for row in rows:
            market_id = str(row.get("id") or row.get("market_id") or row.get("conditionId") or "")
            if market_id and market_id in seen_ids:
                continue
            if market_id:
                seen_ids.add(market_id)
            collected.append(row)
            if len(collected) >= max_items:
                break

    def fetch_order_book(self, token_id: str) -> dict:
        query = urlencode({"token_id": token_id})
        url = f"{self.config.clob_base_url}/book?{query}"
        payload = self._get_json(url, timeout=10)
        return payload if isinstance(payload, dict) else {}

    def fetch_recent_trades(self, token_id: str, *, limit: int = 200) -> list[dict]:
        query = urlencode({"token_id": token_id, "limit": limit})
        url = f"{self.config.clob_base_url}/trades?{query}"
        payload = self._get_json(url, timeout=10)
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload if isinstance(payload, list) else []

    def attach_event_slugs(self, rows: list[dict], *, closed: bool = False, scan_limit: int = 8000) -> list[dict]:
        target_ids = {str(row.get("id")) for row in rows if row.get("id") is not None}
        if not target_ids:
            return rows
        slug_lookup = self.fetch_event_slug_lookup(target_ids, closed=closed, scan_limit=scan_limit)
        if not slug_lookup:
            return rows

        for row in rows:
            market_id = str(row.get("id")) if row.get("id") is not None else None
            if not market_id:
                continue
            event_slug = slug_lookup.get(market_id)
            if event_slug:
                row["eventSlug"] = event_slug
        return rows

    def fetch_event_slug_lookup(
        self,
        market_ids: set[str],
        *,
        closed: bool = False,
        scan_limit: int = 8000,
    ) -> dict[str, str]:
        lookup: dict[str, str] = {}
        if not market_ids:
            return lookup

        offset = 0
        scanned = 0
        page_size = 500
        while scanned < scan_limit and len(lookup) < len(market_ids):
            params = {"limit": page_size, "offset": offset, "closed": str(closed).lower()}
            query = urlencode(params)
            url = f"{self.config.gamma_base_url}/events?{query}"
            payload = self._get_json(url, timeout=25)
            if not isinstance(payload, list) or not payload:
                break

            for event in payload:
                event_slug = event.get("slug")
                if not event_slug:
                    continue
                for market in event.get("markets") or []:
                    market_id = market.get("id")
                    if market_id is None:
                        continue
                    market_id_str = str(market_id)
                    if market_id_str in market_ids:
                        lookup[market_id_str] = str(event_slug)

            offset += len(payload)
            scanned += len(payload)
            if len(payload) < page_size:
                break

        return lookup

    def _get_json(self, url: str, *, timeout: int = 15) -> dict | list:
        req = Request(url=url, headers=self._headers, method="GET")
        with urlopen(req, timeout=timeout) as resp:  # nosec - public GET endpoint
            return json.loads(resp.read().decode("utf-8"))

    def normalize_market(self, raw: dict) -> MarketSnapshot:
        market_id = str(raw.get("id") or raw.get("market_id") or raw.get("conditionId"))
        question = str(raw.get("question") or raw.get("title") or raw.get("name") or "")
        category = self._normalize_category(str(raw.get("category") or ""), question, raw)

        event_group = str(
            raw.get("event")
            or raw.get("eventSlug")
            or raw.get("slug")
            or self._event_group_from_question(question)
        )

        end_time = self._parse_datetime(
            raw.get("endDate")
            or raw.get("end_time")
            or raw.get("resolutionDate")
            or raw.get("resolveBy")
        )

        prices = raw.get("prices") or {}
        outcome_prices = self._as_list(raw.get("outcomePrices"))
        outcomes = self._as_list(raw.get("outcomes"))

        yes_from_outcomes = None
        if outcomes and outcome_prices and len(outcomes) == len(outcome_prices):
            for idx, name in enumerate(outcomes):
                if str(name).strip().lower() == "yes":
                    yes_from_outcomes = outcome_prices[idx]
                    break
        if yes_from_outcomes is None and outcome_prices:
            yes_from_outcomes = outcome_prices[0]

        last_price_yes = self._as_float(
            raw.get("lastPriceYes")
            or raw.get("last_price_yes")
            or prices.get("yes")
            or yes_from_outcomes
        )

        metadata = dict(raw)
        metadata["outcomes"] = outcomes or raw.get("outcomes")
        metadata["outcomePrices"] = outcome_prices or raw.get("outcomePrices")

        order_book = self._normalize_orderbook(
            raw.get("orderbook")
            or raw.get("book")
            or {
                "bestBidYes": raw.get("bestBid"),
                "bestAskYes": raw.get("bestAsk"),
                "bestBidNo": raw.get("bestBidNo"),
                "bestAskNo": raw.get("bestAskNo"),
            }
        )

        return MarketSnapshot(
            market_id=market_id,
            question=question,
            category=category,
            event_group=event_group,
            end_time=end_time,
            volume_24h=self._as_float(raw.get("volume24hr") or raw.get("volume_24h") or 0.0),
            volume_7d=self._as_float(raw.get("volume7d") or raw.get("volume_7d") or 0.0),
            liquidity=self._as_float(raw.get("liquidity") or raw.get("liquidityClob") or 0.0),
            last_price_yes=last_price_yes,
            price_history_yes=self._extract_price_history(raw),
            tags=self._extract_tags(raw),
            metadata=metadata,
            order_book=order_book,
        )

    def normalize_markets(self, rows: list[dict]) -> list[MarketSnapshot]:
        return [self.normalize_market(row) for row in rows]

    @staticmethod
    def _normalize_orderbook(raw: dict) -> OrderBookSnapshot:
        if not isinstance(raw, dict):
            return OrderBookSnapshot()
        return OrderBookSnapshot(
            bid_yes=PolymarketPublicClient._as_float(raw.get("bid_yes") or raw.get("bestBidYes") or raw.get("bid")),
            ask_yes=PolymarketPublicClient._as_float(raw.get("ask_yes") or raw.get("bestAskYes") or raw.get("ask")),
            bid_no=PolymarketPublicClient._as_float(raw.get("bid_no") or raw.get("bestBidNo")),
            ask_no=PolymarketPublicClient._as_float(raw.get("ask_no") or raw.get("bestAskNo")),
            depth_yes=PolymarketPublicClient._as_float(raw.get("depth_yes") or raw.get("depthYes") or 0.0),
            depth_no=PolymarketPublicClient._as_float(raw.get("depth_no") or raw.get("depthNo") or 0.0),
        )

    @staticmethod
    def _extract_price_history(raw: dict) -> list[float]:
        history = raw.get("priceHistoryYes") or raw.get("price_history_yes") or raw.get("prices_yes") or []
        if not isinstance(history, list):
            return []
        output: list[float] = []
        for entry in history:
            if isinstance(entry, dict):
                value = PolymarketPublicClient._as_float(entry.get("price") or entry.get("y"))
            else:
                value = PolymarketPublicClient._as_float(entry)
            if value is not None:
                output.append(value)
        return output

    @staticmethod
    def _as_list(value: object) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return []
            return parsed if isinstance(parsed, list) else []
        return []

    @staticmethod
    def _extract_tags(raw: dict) -> list[str]:
        source = raw.get("tags") or raw.get("labels") or []
        if not isinstance(source, list):
            return []
        return [str(item).strip() for item in source if str(item).strip()]

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        for candidate in (text, text.replace("Z", "+00:00")):
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                continue
        return None

    @staticmethod
    def _event_group_from_question(question: str) -> str:
        words = [w for w in question.lower().split() if w.isalpha()]
        return " ".join(words[:5]) or "unknown-event"

    @staticmethod
    def _as_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_category(category: str, question: str, raw: dict) -> str:
        norm = category.strip().lower()
        question_low = question.lower()
        slug_low = str(raw.get("slug") or "").lower()
        combined = f"{question_low} {slug_low}"

        if norm in {"nba", "basketball"} or "nba" in question_low:
            return "NBA"
        if norm in {"cricket"} or PolymarketPublicClient._is_cricket_market(combined):
            return "Cricket"
        if norm in {"soccer", "football"} or "premier league" in question_low or "uefa" in question_low:
            return "Soccer"
        if norm in {"trump", "donald trump"} or "trump" in question_low:
            return "Mentions"
        if norm in {"elections", "election", "politics"} or "election" in question_low:
            return "Elections"

        tags = [str(t).lower() for t in (raw.get("tags") or [])]
        if "trump" in tags:
            return "Mentions"
        if "elections" in tags:
            return "Elections"

        return category.strip().title() if category.strip() else "Unknown"

    @staticmethod
    def _is_cricket_market(text: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if not normalized:
            return False

        phrase_terms = (
            "test match",
            "the hundred",
            "mumbai indians",
            "chennai super kings",
            "kolkata knight riders",
            "sunrisers hyderabad",
            "delhi capitals",
            "rajasthan royals",
            "gujarat titans",
            "lucknow super giants",
            "punjab kings",
            "royal challengers",
            "ranji",
            "vijay hazare",
        )
        if any(f" {term} " in f" {normalized} " for term in phrase_terms):
            return True

        tokens = set(normalized.split())
        cricket_tokens = {
            "cricket",
            "cric",
            "ipl",
            "odi",
            "t20",
            "ashes",
            "bbl",
            "psl",
            "cpl",
            "rcb",
            "csk",
            "kkr",
            "pbks",
            "lsg",
            "srh",
        }
        return any(tok in cricket_tokens for tok in tokens)

    def debug_dump_market(self, raw: dict) -> dict:
        snap = self.normalize_market(raw)
        payload = asdict(snap)
        payload["end_time"] = snap.end_time.isoformat() if snap.end_time else None
        return payload
