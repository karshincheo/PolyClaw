import math

from polyclaw.backtest.results import EquityPoint, PerformanceMetrics, TradeRecord


def compute_metrics(
    trades: list[TradeRecord],
    equity_curve: list[EquityPoint],
    starting_cash: float,
) -> PerformanceMetrics:
    ending_equity = equity_curve[-1].total_equity if equity_curve else starting_cash
    total_return_usd = ending_equity - starting_cash
    total_return_pct = (total_return_usd / starting_cash) * 100 if starting_cash > 0 else 0

    sharpe = _compute_sharpe_ratio(equity_curve)
    dd_pct, dd_usd = _compute_max_drawdown(equity_curve)
    trade_stats = _compute_trade_stats(trades)
    total_fees = sum(t.fee for t in trades)

    return PerformanceMetrics(
        total_return_pct=round(total_return_pct, 2),
        total_return_usd=round(total_return_usd, 2),
        sharpe_ratio=round(sharpe, 2) if sharpe is not None else None,
        max_drawdown_pct=round(dd_pct, 2),
        max_drawdown_usd=round(dd_usd, 2),
        win_rate=round(trade_stats["win_rate"], 4),
        profit_factor=round(trade_stats["profit_factor"], 2) if trade_stats["profit_factor"] is not None else None,
        avg_trade_pnl=round(trade_stats["avg_pnl"], 2),
        total_trades=trade_stats["total"],
        winning_trades=trade_stats["wins"],
        losing_trades=trade_stats["losses"],
        avg_win=round(trade_stats["avg_win"], 2),
        avg_loss=round(trade_stats["avg_loss"], 2),
        best_trade_pnl=round(trade_stats["best"], 2),
        worst_trade_pnl=round(trade_stats["worst"], 2),
        total_fees_paid=round(total_fees, 2),
    )


def _compute_sharpe_ratio(equity_curve: list[EquityPoint], risk_free_rate: float = 0.0) -> float | None:
    if len(equity_curve) < 2:
        return None

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1].total_equity
        curr = equity_curve[i].total_equity
        if prev > 0:
            returns.append((curr - prev) / prev)

    if not returns:
        return None

    mean_ret = sum(returns) / len(returns)
    if len(returns) < 2:
        return None

    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    std_ret = math.sqrt(variance)

    if std_ret == 0:
        return None

    # Annualize: assume hourly ticks, 365*24 = 8760 periods/year
    periods_per_year = 8760
    annualized_return = mean_ret * periods_per_year
    annualized_std = std_ret * math.sqrt(periods_per_year)

    return (annualized_return - risk_free_rate) / annualized_std


def _compute_max_drawdown(equity_curve: list[EquityPoint]) -> tuple[float, float]:
    if not equity_curve:
        return 0.0, 0.0

    peak = equity_curve[0].total_equity
    max_dd_pct = 0.0
    max_dd_usd = 0.0

    for point in equity_curve:
        if point.total_equity > peak:
            peak = point.total_equity
        drawdown_usd = peak - point.total_equity
        drawdown_pct = (drawdown_usd / peak * 100) if peak > 0 else 0
        if drawdown_pct > max_dd_pct:
            max_dd_pct = drawdown_pct
            max_dd_usd = drawdown_usd

    return max_dd_pct, max_dd_usd


def _compute_trade_stats(trades: list[TradeRecord]) -> dict:
    """Compute per-trade PnL by pairing BUY/SELL for each token."""
    if not trades:
        return {
            "win_rate": 0.0, "profit_factor": None, "avg_pnl": 0.0,
            "total": 0, "wins": 0, "losses": 0,
            "avg_win": 0.0, "avg_loss": 0.0, "best": 0.0, "worst": 0.0,
        }

    # Track cost basis per token
    holdings: dict[str, list[tuple[float, float]]] = {}  # token -> [(shares, price)]
    pnls: list[float] = []

    for t in trades:
        if t.side == "BUY":
            holdings.setdefault(t.token_id, []).append((t.shares, t.price))
        elif t.side == "SELL":
            lots = holdings.get(t.token_id, [])
            remaining = t.shares
            sell_pnl = 0.0
            while remaining > 0 and lots:
                lot_shares, lot_price = lots[0]
                sold = min(remaining, lot_shares)
                sell_pnl += sold * (t.price - lot_price)
                remaining -= sold
                if sold >= lot_shares:
                    lots.pop(0)
                else:
                    lots[0] = (lot_shares - sold, lot_price)
            # Subtract fees from PnL
            sell_pnl -= t.fee
            pnls.append(sell_pnl)


    if not pnls:
        return {
            "win_rate": 0.0, "profit_factor": None, "avg_pnl": 0.0,
            "total": len(trades), "wins": 0, "losses": 0,
            "avg_win": 0.0, "avg_loss": 0.0, "best": 0.0, "worst": 0.0,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "avg_pnl": sum(pnls) / len(pnls),
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "best": max(pnls) if pnls else 0.0,
        "worst": min(pnls) if pnls else 0.0,
    }
