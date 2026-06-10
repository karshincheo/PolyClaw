import argparse
import logging

from polyclaw.ingestion.scheduler import IngestionScheduler


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="polyclaw",
        description="PolyClaw — Polymarket data ingestion & trading agent",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--db", default=None, help="SQLite database path (default: polyclaw.db)")

    sub = parser.add_subparsers(dest="command", required=True)

    # Ingestion commands
    sub.add_parser("fetch-markets", help="Fetch all active markets from Gamma API")
    sub.add_parser("fetch-prices", help="Fetch prices for all active market tokens")
    sub.add_parser("fetch-orderbooks", help="Fetch orderbooks for all active market tokens")
    sub.add_parser("fetch-all", help="Full ingestion: markets + prices + orderbooks")
    sub.add_parser("daemon", help="Run continuous ingestion loop")

    # Web UI
    web_parser = sub.add_parser("web", help="Launch the web dashboard")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    web_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")

    # Trading commands
    trade_parser = sub.add_parser("trade", help="Place a paper/live trade")
    trade_parser.add_argument("--mode", choices=["paper", "live"], default=None, help="Trading mode")
    trade_parser.add_argument("--token-id", required=True, help="CLOB token ID")
    trade_parser.add_argument("--market-id", default="", help="Condition ID (market)")
    trade_parser.add_argument("--side", choices=["BUY", "SELL"], required=True)
    trade_parser.add_argument("--size", type=float, required=True, help="USDC amount (market buy) or shares")
    trade_parser.add_argument("--price", type=float, default=None, help="Limit price (omit for market order)")
    trade_parser.add_argument("--question", default="", help="Market question (for logging)")
    trade_parser.add_argument("--outcome", default="", help="Outcome name (Yes/No)")

    portfolio_parser = sub.add_parser("portfolio", help="Show paper/live portfolio")
    portfolio_parser.add_argument("--mode", choices=["paper", "live"], default=None)

    history_parser = sub.add_parser("history", help="Show paper trade history")
    history_parser.add_argument("--mode", choices=["paper", "live"], default=None)

    sub.add_parser("paper-reset", help="Reset paper trading to starting balance")

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Web UI
    if args.command == "web":
        from polyclaw.web.app import app as flask_app
        print(f"Starting PolyClaw web UI at http://{args.host}:{args.port}")
        flask_app.run(host=args.host, port=args.port, debug=False)
        return

    # Ingestion commands
    if args.command in ("fetch-markets", "fetch-prices", "fetch-orderbooks", "fetch-all", "daemon"):
        scheduler = IngestionScheduler(db_path=args.db)
        try:
            if args.command == "fetch-markets":
                scheduler.market_ingester.ingest()
            elif args.command == "fetch-prices":
                scheduler.price_ingester.ingest_prices()
            elif args.command == "fetch-orderbooks":
                scheduler.price_ingester.ingest_orderbooks()
            elif args.command == "fetch-all":
                scheduler.run_once()
            elif args.command == "daemon":
                scheduler.run_loop()
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            scheduler.close()
        return

    # Trading commands
    if args.command == "trade":
        from polyclaw.trading.factory import create_trader
        from polyclaw.trading.models import Side, TradeOrder, TradeOrderType

        trader = create_trader(mode=args.mode)
        order = TradeOrder(
            token_id=args.token_id,
            market_id=args.market_id,
            market_question=args.question,
            outcome=args.outcome,
            side=Side(args.side),
            order_type=TradeOrderType.LIMIT if args.price else TradeOrderType.MARKET,
            price=args.price,
            size=args.size,
        )
        result = trader.place_order(order)
        print("\nOrder Result:")
        print(f"  ID:     {result.order_id}")
        print(f"  Status: {result.status.value}")
        if result.filled_price:
            print(f"  Price:  ${result.filled_price:.4f}")
        if result.filled_size:
            print(f"  Shares: {result.filled_size:.2f}")
        if result.total_cost:
            print(f"  Cost:   ${result.total_cost:.2f}")
        if result.message:
            print(f"  Note:   {result.message}")

    elif args.command == "portfolio":
        from polyclaw.trading.factory import create_trader

        trader = create_trader(mode=args.mode)
        portfolio = trader.get_portfolio()

        print(f"\n{'='*60}")
        print(f" PORTFOLIO ({'PAPER' if (args.mode or 'paper') == 'paper' else 'LIVE'})")
        print(f"{'='*60}")
        print(f" Cash Balance:      ${portfolio.cash_balance:,.2f}")
        print(f" Position Value:    ${portfolio.total_position_value:,.2f}")
        print(f" Total Equity:      ${portfolio.total_equity:,.2f}")
        print(f" Realized PnL:      ${portfolio.total_realized_pnl:,.2f}")
        print(f" Unrealized PnL:    ${portfolio.total_unrealized_pnl:,.2f}")

        if portfolio.positions:
            print("\n Positions:")
            print(f" {'Outcome':<12} {'Shares':>10} {'Entry':>8} {'Current':>8} {'PnL':>10}  Market")
            print(f" {'-'*12} {'-'*10} {'-'*8} {'-'*8} {'-'*10}  {'-'*30}")
            for p in portfolio.positions:
                curr = f"${p.current_price:.4f}" if p.current_price else "N/A"
                pnl = f"${p.unrealized_pnl:+.2f}" if p.unrealized_pnl is not None else "N/A"
                print(f" {p.outcome:<12} {p.shares:>10.2f} ${p.avg_entry_price:>7.4f} {curr:>8} {pnl:>10}  {p.market_question[:30]}")
        else:
            print("\n No open positions.")
        print()

    elif args.command == "history":
        from polyclaw.trading.factory import create_trader
        from polyclaw.trading.paper_trader import PaperTrader

        trader = create_trader(mode=args.mode)
        if not isinstance(trader, PaperTrader):
            print("Trade history is only available in paper mode.")
            return

        trades = trader.get_trade_history()
        if not trades:
            print("\nNo trades yet.")
            return

        print(f"\n{'='*80}")
        print(" TRADE HISTORY (last 20)")
        print(f"{'='*80}")
        for t in trades[:20]:
            from datetime import datetime
            ts = datetime.fromtimestamp(t["timestamp"] / 1000).strftime("%Y-%m-%d %H:%M")
            print(f" [{ts}] {t['side']:4s} {t['outcome']:3s} "
                  f"{t['filled_size']:>10.2f} shares @ ${t['filled_price']:.4f} "
                  f"(${t['total_cost']:.2f}) — {t['market_question'][:35]}")
        print()

    elif args.command == "paper-reset":
        from polyclaw.trading.paper_trader import PaperTrader
        from polyclaw.config import settings

        trader = PaperTrader(
            db_path=settings.paper_db_path,
            starting_balance=settings.paper_starting_balance,
        )
        trader.reset()
        print(f"Paper trading reset. Balance: ${settings.paper_starting_balance:,.2f}")


if __name__ == "__main__":
    main()
