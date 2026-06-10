
from pydantic import BaseModel


class PriceSnapshot(BaseModel):
    token_id: str
    market_id: str  # condition_id
    buy_price: float | None = None
    sell_price: float | None = None
    midpoint: float | None = None
    timestamp: int  # unix ms
