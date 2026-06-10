import logging

import httpx

from polyclaw.clients.rate_limiter import gamma_limiter
from polyclaw.config import settings
from polyclaw.models.market import Market

logger = logging.getLogger(__name__)


class GammaClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.gamma_base_url
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    def get_markets(
        self,
        *,
        active: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Market]:
        limit = limit or settings.gamma_page_size
        params: dict = {"limit": limit, "offset": offset, "active": active}

        gamma_limiter.acquire()
        resp = self._client.get("/markets", params=params)
        resp.raise_for_status()

        markets = []
        for raw in resp.json():
            try:
                markets.append(Market.model_validate(raw))
            except Exception:
                logger.warning("Failed to parse market id=%s", raw.get("id"), exc_info=True)
        return markets

    def get_all_markets(self, *, active: bool = True) -> list[Market]:
        """Auto-paginate through all markets, deduplicating by ID."""
        seen_ids: set[str] = set()
        all_markets: list[Market] = []
        offset = 0
        page_size = settings.gamma_page_size

        while True:
            page = self.get_markets(active=active, limit=page_size, offset=offset)

            new_count = 0
            for m in page:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    all_markets.append(m)
                    new_count += 1

            logger.info(
                "Fetched page offset=%d: %d markets (%d new, %d total)",
                offset, len(page), new_count, len(all_markets),
            )

            if len(page) < page_size:
                break
            offset += page_size

        return all_markets

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
