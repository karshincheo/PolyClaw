import json

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel


class Event(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        alias_generator=to_camel,
    )

    id: str
    ticker: str | None = None
    slug: str
    title: str
    description: str = ""
    start_date: str | None = None
    end_date: str | None = None
    active: bool = True
    closed: bool = False
    liquidity: float = 0.0
    volume: float = 0.0
    volume_24hr: float = 0.0
    neg_risk: bool = False
    comment_count: int = 0


class Market(BaseModel):
    """A single Polymarket binary market (YES/NO outcome pair).

    Field names use snake_case; the alias_generator handles camelCase from the API.
    outcomes and clob_token_ids arrive as JSON-encoded strings from Gamma API,
    so we parse them into lists via validators.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        alias_generator=to_camel,
    )

    id: str
    question: str
    condition_id: str
    slug: str
    description: str = ""
    outcomes: list[str] = []
    outcome_prices: list[str] = []
    clob_token_ids: list[str] = []
    active: bool = True
    closed: bool = False
    accepting_orders: bool = False
    enable_order_book: bool = False
    neg_risk: bool = False

    # Numeric market data
    liquidity: float = 0.0
    volume: float = 0.0
    volume_num: float = 0.0
    volume_24hr: float = 0.0
    volume_1wk: float = 0.0
    volume_1mo: float = 0.0
    liquidity_num: float = 0.0

    # Order book config
    order_price_min_tick_size: float = 0.01
    order_min_size: float = 5.0

    # Dates
    end_date: str | None = None
    start_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    # Group info (for multi-market events)
    group_item_title: str | None = None

    # Nested events
    events: list[Event] = []

    @field_validator("outcomes", "outcome_prices", "clob_token_ids", mode="before")
    @classmethod
    def parse_json_list(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v if v is not None else []
