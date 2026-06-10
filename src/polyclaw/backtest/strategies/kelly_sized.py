
from polyclaw.backtest.strategy import Signal, Strategy, TickContext
from polyclaw.trading.models import Side


class KellySizedStrategy(Strategy):
    """Uses Kelly criterion for position sizing based on estimated edge.

    Estimates model probability from momentum signals, computes Kelly fraction,
    and sizes each trade as a percentage of total bankroll.
    Quarter-Kelly by default for safety.
    """

    name = "kelly_sized"

    def __init__(self):
        self.kelly_aggression: float = 0.25   # quarter-Kelly
        self.max_bet_pct: float = 0.05        # 5% max of bankroll
        self.min_edge_to_trade: float = 0.02  # 2% minimum edge
        self.momentum_window: int = 10
        self.entry_below: float = 0.45        # only buy when price < this

    def on_tick(self, ctx: TickContext) -> Signal | None:
        if len(ctx.prices) < self.momentum_window + 1:
            return None

        # Estimate "model probability" from momentum
        recent = ctx.prices[-self.momentum_window:]
        ma = sum(recent) / len(recent)
        momentum = ctx.price - ma

        # Simple model: adjust market price by momentum signal
        p_model = min(0.95, max(0.05, ctx.price + momentum * 2))

        # Determine side and entry price
        if ctx.price < self.entry_below and p_model > ctx.price:
            entry_price = ctx.price
            p = p_model
        elif ctx.position_shares > 0 and p_model < ctx.avg_entry_price:
            # Exit losing position
            return Signal(side=Side.SELL, size=ctx.position_shares,
                          reason=f"Kelly exit: model={p_model:.3f} < entry={ctx.avg_entry_price:.3f}")
        else:
            return None

        # Kelly fraction: f* = (p*b - q) / b
        if entry_price <= 0 or entry_price >= 1.0:
            return None
        b = (1.0 - entry_price) / entry_price  # net odds
        q = 1.0 - p
        kelly_raw = (p * b - q) / b if b > 0 else 0.0

        if kelly_raw <= 0:
            return None

        edge = p - entry_price
        if edge < self.min_edge_to_trade:
            return None

        # Apply fractional Kelly and cap
        kelly_scaled = kelly_raw * self.kelly_aggression
        stake_pct = min(kelly_scaled, self.max_bet_pct)
        stake_usd = stake_pct * ctx.bankroll

        if stake_usd < 1.0 or ctx.cash < stake_usd:
            return None

        return Signal(
            side=Side.BUY,
            size=stake_usd,
            reason=f"Kelly {kelly_raw*100:.1f}% → {stake_pct*100:.2f}% (${stake_usd:.0f}), edge={edge*100:.1f}%",
        )
