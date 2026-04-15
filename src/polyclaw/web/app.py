import logging
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
FRONTEND_BUILD_DIR = STATIC_DIR / "app"

app = Flask(__name__, static_folder=str(STATIC_DIR))

# Lazy singletons
_gamma = None
_clob = None
_trader = None
_dashboard_service = None


def get_gamma():
    global _gamma
    if _gamma is None:
        from polyclaw.clients.gamma import GammaClient

        _gamma = GammaClient()
    return _gamma


def get_clob():
    global _clob
    if _clob is None:
        from polyclaw.clients.clob import ClobClientWrapper

        _clob = ClobClientWrapper()
    return _clob


def get_trader():
    global _trader
    if _trader is None:
        from polyclaw.config import settings
        from polyclaw.trading.paper_trader import PaperTrader

        _trader = PaperTrader(
            db_path=settings.paper_db_path,
            starting_balance=settings.paper_starting_balance,
            clob=get_clob(),
        )
    return _trader


def get_dashboard_service():
    global _dashboard_service
    if _dashboard_service is None:
        from polyclaw.web.dashboard_service import DashboardService

        _dashboard_service = DashboardService(
            gamma=get_gamma(),
            clob=get_clob(),
            trader=get_trader(),
        )
    return _dashboard_service


def frontend_entry_dir() -> Path:
    """Return the compiled React app when available, else the fallback static directory."""
    return FRONTEND_BUILD_DIR if (FRONTEND_BUILD_DIR / "index.html").exists() else STATIC_DIR


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    return response


@app.route("/")
def index():
    directory = frontend_entry_dir()
    return send_from_directory(str(directory), "index.html")


@app.route("/api/markets")
def search_markets():
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", 20))

    gamma = get_gamma()
    all_markets = []
    for offset in range(0, 500, 100):
        page = gamma.get_markets(limit=100, offset=offset)
        all_markets.extend(page)
        if len(page) < 100:
            break

    if q:
        q_lower = q.lower()
        filtered = [m for m in all_markets if q_lower in m.question.lower()]
    else:
        filtered = all_markets

    filtered.sort(key=lambda m: m.volume, reverse=True)

    return jsonify(
        [
            {
                "id": m.id,
                "question": m.question,
                "slug": m.slug,
                "outcomes": m.outcomes,
                "outcome_prices": m.outcome_prices,
                "clob_token_ids": m.clob_token_ids,
                "condition_id": m.condition_id,
                "liquidity": m.liquidity,
                "volume": m.volume,
                "volume_24hr": m.volume_24hr,
                "end_date": m.end_date,
                "active": m.active,
                "accepting_orders": m.accepting_orders,
                "neg_risk": m.neg_risk,
                "group_item_title": m.group_item_title,
            }
            for m in filtered[:limit]
        ]
    )


@app.route("/api/dashboard/overview")
def dashboard_overview():
    try:
        return jsonify(get_dashboard_service().build_overview())
    except Exception as exc:
        logger.exception("Failed to build dashboard overview")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/opportunities")
def list_opportunities():
    try:
        limit = int(request.args.get("limit", 36))
        return jsonify(get_dashboard_service().list_opportunities(limit=limit))
    except Exception as exc:
        logger.exception("Failed to list opportunities")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/opportunities/<market_id>")
def opportunity_detail(market_id: str):
    try:
        detail = get_dashboard_service().get_opportunity_detail(market_id)
    except Exception as exc:
        logger.exception("Failed to load opportunity detail for %s", market_id)
        return jsonify({"error": str(exc)}), 503

    if detail is None:
        return jsonify({"error": f"Unknown opportunity {market_id}"}), 404

    return jsonify(detail)


@app.route("/api/orderbook/<token_id>")
def get_orderbook(token_id):
    try:
        ob = get_clob().get_orderbook(token_id)
        return jsonify(
            {
                "token_id": ob.token_id,
                "market_id": ob.market_id,
                "best_bid": ob.best_bid,
                "best_ask": ob.best_ask,
                "spread": ob.spread,
                "midpoint": ob.midpoint,
                "updatedAt": ob.timestamp,
                "bids": [{"price": l.price, "size": l.size} for l in ob.bids[:15]],
                "asks": [{"price": l.price, "size": l.size} for l in ob.asks[:15]],
            }
        )
    except Exception as e:
        logger.exception("Failed to fetch orderbook for %s", token_id)
        return jsonify({"error": str(e)}), 503


@app.route("/api/trade", methods=["POST"])
def place_trade():
    from polyclaw.trading.models import Side, TradeOrder, TradeOrderType

    req = request.json or {}
    environment = req.get("environment", "paper")
    if environment != "paper":
        return (
            jsonify(
                {
                    "error": "Live execution is disabled in Phase 1.",
                    "paperExecutionAvailable": True,
                    "liveExecutionAvailable": False,
                }
            ),
            501,
        )

    order = TradeOrder(
        token_id=req["token_id"],
        market_id=req.get("market_id", ""),
        market_question=req.get("question", ""),
        outcome=req.get("outcome", ""),
        side=Side(req["side"]),
        order_type=TradeOrderType.LIMIT if req.get("price") else TradeOrderType.MARKET,
        price=req.get("price"),
        size=req["size"],
    )
    result = get_trader().place_order(order)
    get_dashboard_service().invalidate_paper_state()
    get_dashboard_service().invalidate_market_state(req.get("market_id"))
    return jsonify(
        {
            "order_id": result.order_id,
            "status": result.status.value,
            "filled_price": result.filled_price,
            "filled_size": result.filled_size,
            "total_cost": result.total_cost,
            "message": result.message,
            "environment": environment,
        }
    )


@app.route("/api/positions")
def get_positions():
    environment = request.args.get("environment", "paper")
    try:
        return jsonify(get_dashboard_service().list_positions(environment=environment))
    except Exception as exc:
        logger.exception("Failed to fetch positions for %s", environment)
        return jsonify({"error": str(exc)}), 503


@app.route("/api/portfolio")
def get_portfolio():
    environment = request.args.get("environment", "paper")
    try:
        return jsonify(get_dashboard_service().get_portfolio(environment=environment))
    except Exception as exc:
        logger.exception("Failed to fetch portfolio for %s", environment)
        return jsonify({"error": str(exc)}), 503


@app.route("/api/reset", methods=["POST"])
def reset_portfolio():
    from polyclaw.config import settings

    get_trader().reset()
    get_dashboard_service().invalidate_paper_state()
    return jsonify({"message": "Paper trading reset", "balance": settings.paper_starting_balance})


@app.route("/api/strategy/opportunities")
def strategy_opportunities():
    try:
        from polyclaw.web.strategy_service import get_scored_opportunities

        picks = get_scored_opportunities()
        return jsonify({"items": picks, "count": len(picks)})
    except Exception as exc:
        logger.exception("Failed to get scored opportunities")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/strategy/opportunities/<market_id>/bet", methods=["POST"])
def strategy_bet(market_id: str):
    from polyclaw.trading.models import Side, TradeOrder, TradeOrderType

    req = request.json or {}
    side_str = req.get("side", "YES").upper()
    size = float(req.get("size", 100))

    # Try to resolve token_id from the dashboard opportunity cache first
    dashboard = get_dashboard_service()
    token_id = None
    question = ""

    try:
        items = dashboard._load_opportunities()
    except Exception:
        items = dashboard.opportunities_cache.value or []

    opp = next((o for o in items if o["id"] == market_id), None)
    if not opp:
        detail = dashboard.get_opportunity_detail(market_id)
        if detail:
            opp = detail

    if opp:
        token_ids = opp.get("tokenIds", {})
        token_id = token_ids.get(side_str)
        question = opp.get("question", "")

    # If not in feed, fetch the market directly from Gamma API
    if not token_id:
        try:
            import httpx
            resp = httpx.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=10)
            if resp.status_code == 200:
                from polyclaw.models.market import Market as MarketModel
                market = MarketModel.model_validate(resp.json())
                question = market.question
                for idx, outcome in enumerate(market.outcomes):
                    if outcome.upper() == side_str and idx < len(market.clob_token_ids):
                        token_id = market.clob_token_ids[idx]
                        break
        except Exception as exc:
            logger.warning("Could not fetch market %s from Gamma: %s", market_id, exc)

    if not token_id:
        return jsonify({"error": f"No {side_str} token available for market {market_id}"}), 400

    order = TradeOrder(
        token_id=token_id,
        market_id=market_id,
        market_question=question,
        outcome=side_str,
        side=Side.BUY,
        order_type=TradeOrderType.MARKET,
        size=size,
    )
    result = get_trader().place_order(order)
    dashboard.invalidate_paper_state()
    return jsonify(
        {
            "order_id": result.order_id,
            "status": result.status.value,
            "filled_price": result.filled_price,
            "filled_size": result.filled_size,
            "total_cost": result.total_cost,
            "message": result.message,
        }
    )


@app.route("/api/backtest/strategies")
def list_backtest_strategies():
    """List available backtest strategies with their parameters."""
    from polyclaw.backtest.strategies import STRATEGY_REGISTRY

    strategies = []
    for name, cls in sorted(STRATEGY_REGISTRY.items()):
        instance = cls()
        params = {k: v for k, v in instance.__dict__.items() if not k.startswith('_')}
        strategies.append({
            "name": name,
            "description": cls.__doc__.split('\n')[0] if cls.__doc__ else "",
            "params": params,
        })
    return jsonify(strategies)


@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    """Run a backtest with historical data."""
    from polyclaw.backtest.data_loader import DataLoader
    from polyclaw.backtest.engine import BacktestEngine
    from polyclaw.backtest.strategies import get_strategy

    req = request.json or {}
    strategy_name = req.get("strategy")
    markets_query = req.get("markets")

    if not strategy_name or not markets_query:
        return jsonify({"error": "strategy and markets are required"}), 400

    try:
        strategy = get_strategy(strategy_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    strategy.configure(req.get("params", {}))

    loader = DataLoader()
    market_data = loader.load_markets_by_query(
        markets_query,
        fidelity=req.get("fidelity", 60),
        limit=req.get("max_markets", 5),
    )

    if not market_data:
        return jsonify({"error": f"No markets found for: {markets_query!r}"}), 404

    engine = BacktestEngine(
        starting_cash=req.get("cash", 10_000.0),
        fee_bps=req.get("fee_bps", 0),
        slippage_pct=req.get("slippage_pct", 0.005),
    )
    result = engine.run(strategy, market_data)

    # Cap equity curve to 500 points for frontend performance
    curve = result.equity_curve
    if len(curve) > 500:
        step = len(curve) // 500
        curve = curve[::step] + [curve[-1]]

    return jsonify({
        "backtest_id": result.backtest_id,
        "strategy_name": result.strategy_name,
        "starting_cash": result.starting_cash,
        "ending_cash": result.ending_cash,
        "ending_equity": result.ending_equity,
        "fee_bps": result.fee_bps,
        "fidelity": result.fidelity,
        "markets": result.markets,
        "strategy_params": result.strategy_params,
        "metrics": result.metrics.model_dump(),
        "trades": [t.model_dump() for t in result.trades],
        "equity_curve": [e.model_dump() for e in curve],
    })


@app.route("/<path:full_path>")
def frontend_routes(full_path: str):
    """Serve the compiled React frontend for non-API routes."""
    if full_path.startswith("api/"):
        abort(404)

    directory = frontend_entry_dir()
    requested = directory / full_path

    if requested.is_file():
        return send_from_directory(str(directory), full_path)

    if "." in Path(full_path).name:
        abort(404)

    return send_from_directory(str(directory), "index.html")
