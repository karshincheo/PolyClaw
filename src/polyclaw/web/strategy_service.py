"""
strategy_service.py — connects the legacy SelectionPipeline to the web API.

Runs run_selector.py as a subprocess (keeps the stdlib selector isolated from web deps
between the root legacy module and the installed src/polyclaw package),
caches results for 60 seconds, and enriches each pick with OpenAI commentary.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Project root is 4 levels above this file:
# strategy_service.py → web/ → polyclaw/ → src/ → project root
_PROJECT_ROOT = Path(__file__).parents[3]
_SELECTOR_SCRIPT = _PROJECT_ROOT / "run_selector.py"

_CACHE_TTL = 60.0  # seconds

_cache_lock = threading.Lock()
_cached_picks = None  # type: Optional[List[Dict[str, Any]]]
_cache_time: float = 0.0
_refresh_running = False


def _run_pipeline():
    # type: () -> List[Dict[str, Any]]
    """Invoke run_selector.py --live --pretty and parse the JSON output."""
    result = subprocess.run(
        [sys.executable, str(_SELECTOR_SCRIPT), "--live", "--pretty"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_PROJECT_ROOT),
        timeout=180,
    )
    # decode bytes to str (Python 3.6 subprocess doesn't have text= parameter)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            "Selector script exited {}: {}".format(result.returncode, stderr[-600:])
        )

    raw = json.loads(stdout)  # type: Dict[str, List[Dict]]

    all_picks = []  # type: List[Dict[str, Any]]
    for category, picks in raw.items():
        for pick in picks:
            market_url = pick.get("market_url") or _url_from_slug(pick.get("event_group"))
            all_picks.append(
                {
                    **pick,
                    "category": category,
                    "market_url": market_url,
                    "edge_pct": round(pick.get("selected_edge", 0) * 100, 2),
                    "confidence_pct": round(pick.get("confidence", 0) * 100, 1),
                    "score_pct": round(pick.get("score", 0) * 100, 1),
                    "ai_commentary": None,
                }
            )

    return _top_per_category(all_picks, n=5)


def _top_per_category(picks, n=5):
    # type: (List[Dict[str, Any]], int) -> List[Dict[str, Any]]
    """Return top *n* picks per category, ordered by score within each group."""
    from collections import defaultdict
    by_cat = defaultdict(list)  # type: dict
    for p in picks:
        by_cat[p.get("category", "Unknown")].append(p)
    result = []  # type: List[Dict[str, Any]]
    for cat in by_cat:
        ranked = sorted(by_cat[cat], key=lambda p: p.get("score", 0), reverse=True)
        result.extend(ranked[:n])
    result.sort(key=lambda p: p.get("score", 0), reverse=True)
    return result


def _openai_complete(api_key, prompt):
    # type: (str, str) -> Optional[str]
    """Call OpenAI chat completions via raw HTTP (no openai package required)."""
    import json as _json
    import urllib.request

    payload = _json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 80,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _enrich_with_openai(picks):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Add a GPT-4o-mini commentary sentence to each pick."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.info("OPENAI_API_KEY not set — skipping AI commentary")
        return picks

    for pick in picks:
        try:
            tags = ", ".join(pick.get("rationale_tags") or []) or "no specific signals"
            prompt = (
                f"In 1-2 sentences, explain why '{pick['question']}' is a strong "
                f"{pick['side']} bet with {pick['edge_pct']:.1f}% edge. "
                f"Signals: {tags}. Be specific and direct."
            )
            pick["ai_commentary"] = _openai_complete(api_key, prompt)
        except Exception as exc:
            logger.warning("OpenAI call failed for %s: %s", pick.get("market_id"), exc)

    return picks


def _url_from_slug(slug):
    # type: (Optional[str]) -> Optional[str]
    if not slug:
        return None
    return "https://polymarket.com/event/{}".format(slug)


def _load_from_precomputed():
    # type: () -> List[Dict[str, Any]]
    """Read picks from the most recent pre-computed selection output file."""
    data_dir = _PROJECT_ROOT / "data"
    # prefer the most recently modified live_selection_output file
    candidates = sorted(
        list(data_dir.glob("live_selection_output*.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        candidates = list(data_dir.glob("selection_output*.json"))
    if not candidates:
        raise RuntimeError("No pre-computed selection output found in data/")

    path = candidates[0]
    logger.info("Loading precomputed picks from %s", path.name)
    with open(str(path)) as fh:
        raw = json.load(fh)  # type: Dict[str, List[Dict]]

    all_picks = []  # type: List[Dict[str, Any]]
    for category, picks in raw.items():
        for pick in picks:
            all_picks.append({
                **pick,
                "category": category,
                "market_id": pick.get("market_id", pick.get("id", "")),
                "market_url": pick.get("market_url") or _url_from_slug(pick.get("event_group")),
                "side": pick.get("side", "YES"),
                "score": pick.get("score", 0),
                "edge_pct": round(pick.get("selected_edge", 0) * 100, 2),
                "confidence": pick.get("confidence", 0),
                "confidence_pct": round(pick.get("confidence", 0) * 100, 1),
                "score_pct": round(pick.get("score", 0) * 100, 1),
                "liquidity_score": pick.get("liquidity_score", 0),
                "spread_bps": pick.get("spread_bps", 0),
                "hours_to_resolution": pick.get("hours_to_resolution", None),
                "rationale_tags": pick.get("rationale_tags", []),
                "ai_commentary": None,
            })

    return _top_per_category(all_picks, n=5)


def _do_refresh():
    global _cached_picks, _cache_time, _refresh_running

    with _cache_lock:
        if _refresh_running:
            return
        _refresh_running = True

    try:
        logger.info("Refreshing strategy picks...")
        try:
            picks = _run_pipeline()
        except Exception as exc:
            logger.warning("Live pipeline failed (%s), falling back to precomputed data", exc)
            picks = _load_from_precomputed()
        picks = _enrich_with_openai(picks)
        with _cache_lock:
            _cached_picks = picks
            _cache_time = time.monotonic()
        logger.info("Strategy cache updated: %d picks", len(picks))
    except Exception:
        logger.exception("Strategy refresh failed entirely")
    finally:
        with _cache_lock:
            _refresh_running = False


def get_scored_opportunities():
    # type: () -> List[Dict[str, Any]]
    """Return cached scored picks, triggering a background refresh if stale."""
    with _cache_lock:
        has_cache = _cached_picks is not None
        stale = (time.monotonic() - _cache_time) > _CACHE_TTL

    if not has_cache or stale:
        t = threading.Thread(target=_do_refresh, daemon=True)
        t.start()
        if not has_cache:
            # Block on first load so the endpoint doesn't return empty
            t.join(timeout=120)

    with _cache_lock:
        return list(_cached_picks or [])
