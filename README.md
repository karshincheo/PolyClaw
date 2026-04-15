# PolyClaw

Framework for selecting top Polymarket bets across:
- NBA
- Soccer
- Cricket
- Trump
- Elections

It also includes ingestion, trading, and a web dashboard served through FastAPI.

The current implementation builds a reusable pipeline to:
1. Normalize Polymarket public-market JSON payloads.
2. Engineer market microstructure features.
3. Estimate fair probability (`p_model`) from external category-relevant signals (odds/polls/forecasts) with uncertainty-aware blending.
4. Compute edge and expected value for YES/NO sides.
5. Apply risk/diversification constraints.
6. Return top 5 picks per category.

## Structure

- `polyclaw/config.py`: framework config, category list, risk constraints.
- `polyclaw/models.py`: internal dataclasses.
- `polyclaw/polymarket_client.py`: public-API assumptions + normalization layer.
- `polyclaw/features.py`: feature engineering.
- `polyclaw/external_signals.py`: external signal ingestion + consensus probability engine.
- `polyclaw/scoring.py`: fair probability + EV + confidence + final scoring.
- `polyclaw/selection.py`: constrained top-N selector.
- `polyclaw/pipeline.py`: end-to-end orchestration.
- `run_selector.py`: CLI entry point.
- `data/sample_markets.json`: sample payload for local smoke tests.

## Public API Assumptions

The client assumes a common documented split:
- Market metadata API (`gamma` style): `GET /markets`
- CLOB API (`execution` style): `GET /book`, `GET /trades`

Field names may differ by endpoint/version. The normalizer maps common aliases into one internal schema.

## Quick Start

```bash
python3 run_selector.py --input data/sample_markets.json --output data/selection_output.json --pretty
```

This writes output to `data/selection_output.json` with the selected side, score, confidence, edge, EV, and rationale tags.

## Dashboard

The React dashboard source lives in `frontend/`. The Python web app serves the compiled output from
`src/polyclaw/web/static/app`.

Build the frontend:

```bash
cd frontend
npm install
npm run build
```

Run the web server:

```bash
polyclaw web --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000/`.

For frontend-only development:

```bash
cd frontend
npm run dev
```

## AgentArena (Multi-Agent Simulation)

AgentArena extends PolyClaw into a paper-betting arena where multiple AI agents
start with 1000 coins and place NBA bets using PolyClaw recommendation outputs.

1. Configure agent strategies in `data/agent_config.json`.
2. Start the simulation loop:

```bash
python3 run_arena.py \
  --agent-config data/agent_config.json \
  --category NBA \
  --limit 800 \
  --tick-seconds 300 \
  --settle-after-seconds 3600
```

The simulator writes:

- `data/agent_arena.db`: agent balances and open/settled bets (SQLite)
- `data/agent_arena_state.json`: dashboard state (leaderboard, ticker, active bets, market view)

The web app exposes this state at `GET /api/arena/state` and displays it under
the `AgentArena` tab in the frontend.

### Deploying AgentArena (Vercel-friendly)

AgentArena can run without a local loop by triggering ticks through an API route
(`POST /api/arena/tick`). The included `vercel.json` also configures a cron
trigger every 5 minutes.

Recommended environment variables:

- `POLYCLAW_ARENA_ADMIN_TOKEN`: shared secret for protected admin routes
  (`/api/arena/tick`, `/api/arena/register`).
- `POLYCLAW_ARENA_CATEGORY`: set to `NBA` for single-category simulation.
- `POLYCLAW_ARENA_LIMIT`: market fetch limit per tick (e.g., `800`).
- `POLYCLAW_ARENA_SETTLE_AFTER_SECONDS`: settlement window (default `3600`).
- `POLYCLAW_ARENA_STARTING_BALANCE`: starting coins per agent (default `1000`).
- `POLYCLAW_ARENA_DB_PATH`: DB file path (for persistent deployments, use a
  managed database integration rather than ephemeral serverless storage).
- `POLYCLAW_ARENA_AGENT_CONFIG`: optional path for in-process agents config.

External agent API flow:

1. Register agent key (admin-protected):
   `POST /api/arena/register` with JSON `{ "agent_name": "MyAgent" }`
2. Submit a decision:
   `POST /api/arena/decision` with header `X-Agent-Key: <api_key>` and JSON:
   `{ "market_id": "...", "side": "YES", "stake": 50 }`
3. Read standings:
   `GET /api/arena/leaderboard`, `GET /api/arena/state`, `GET /api/arena/markets`

### Live Polymarket Run

```bash
python3 run_selector.py --live --limit 1000 --output data/live_selection_output.json --pretty
```

This fetches live open markets from the configured public Polymarket endpoints and writes recommendations to `data/live_selection_output.json`.

Note: live fetch uses paginated `/markets` pulls and also supplements cricket with Polymarket's cricket tag feed, so IPL/other cricket markets are not missed due to broad-feed ordering.

### External Signal Driven Run

```bash
python3 run_selector.py \
  --live \
  --limit 1200 \
  --external-signals data/external_signals.sample.json \
  --output data/live_selection_output_external.json \
  --pretty
```

Use your own signal file in place of `data/external_signals.sample.json`.  
Without an external signal match for a market, the scorer falls back to heuristic fair-value.
If you want external-only picks, add `--require-external`.

External signal file shape:

```json
{
  "signals": [
    {
      "category": "NBA",
      "source": "consensus-odds",
      "market_ref": "2026-nba-champion",
      "match_terms": ["cavaliers", "nba finals"],
      "probability_yes": 0.055,
      "confidence": 0.82,
      "weight": 1.2,
      "timestamp": "2026-04-12T14:30:00Z"
    }
  ]
}
```

## Output Shape

```json
{
  "NBA": [
    {
      "market_id": "nba-1",
      "question": "...",
      "market_url": "https://polymarket.com/event/...",
      "side": "YES",
      "score": 0.73,
      "confidence": 0.78,
      "p_model_yes": 0.64,
      "p_market_yes": 0.61,
      "p_external_yes": 0.62,
      "external_confidence": 0.81,
      "external_sources": ["consensus-odds"],
      "selected_edge": 0.03,
      "expected_value": 0.03,
      "liquidity_score": 0.67,
      "spread_bps": 200.0,
      "event_group": "...",
      "rationale_tags": ["strong-edge", "positive-ev"]
    }
  ]
}
```

## Notes for Next Step (Strategy Planner)

This framework intentionally separates signal generation from execution sizing, so a later strategy planner can consume output and apply:
- stake sizing (Kelly/vol-targeted),
- inventory and risk budgets,
- execution tactics (limit laddering, slippage controls),
- portfolio-level hedging rules.
