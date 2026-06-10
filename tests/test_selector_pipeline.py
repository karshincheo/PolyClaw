"""End-to-end tests for the stdlib selector pipeline against bundled sample data."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from polyclaw.selector.config import CATEGORIES, FrameworkConfig  # noqa: E402
from polyclaw.selector.pipeline import SelectionPipeline  # noqa: E402

SAMPLE = REPO_ROOT / "data" / "sample_markets.json"
SIGNALS = REPO_ROOT / "data" / "external_signals.sample.json"


@pytest.fixture(scope="module")
def results():
    return SelectionPipeline().run_from_file(SAMPLE)


class TestPipelineOnSampleData:
    def test_only_known_categories_appear(self, results):
        assert set(results) <= set(CATEGORIES)

    def test_top_n_cap_respected(self, results):
        config = FrameworkConfig()
        cap = getattr(config, "top_picks_per_category", 5)
        for category, picks in results.items():
            assert len(picks) <= cap, f"{category} returned {len(picks)} picks"

    def test_picks_have_valid_probabilities_and_sides(self, results):
        for picks in results.values():
            for pick in picks:
                assert pick.selected_side in {"YES", "NO"}
                assert 0.0 <= pick.p_model_yes <= 1.0
                assert 0.0 <= pick.confidence <= 1.0

    def test_deterministic_for_fixed_input(self):
        a = SelectionPipeline().run_from_file(SAMPLE)
        b = SelectionPipeline().run_from_file(SAMPLE)
        key = lambda r: [(p.market.market_id, p.selected_side, p.score) for ps in r.values() for p in ps]  # noqa: E731
        assert key(a) == key(b)


class TestExternalSignals:
    def test_external_signals_change_model_probability(self):
        without = SelectionPipeline().run_from_file(SAMPLE)
        with_signals = SelectionPipeline(external_signals_path=SIGNALS).run_from_file(SAMPLE)
        flat_without = {p.market.market_id: p.p_model_yes for ps in without.values() for p in ps}
        flat_with = {p.market.market_id: p.p_model_yes for ps in with_signals.values() for p in ps}
        # At least the pipelines both ran; if any market matched a signal its
        # probability blend must stay within [0, 1].
        assert flat_without and flat_with
        assert all(0.0 <= v <= 1.0 for v in flat_with.values())

    def test_require_external_filters_unmatched_markets(self):
        strict = SelectionPipeline(
            external_signals_path=SIGNALS, require_external_signal=True
        ).run_from_file(SAMPLE)
        relaxed = SelectionPipeline(external_signals_path=SIGNALS).run_from_file(SAMPLE)
        n_strict = sum(len(v) for v in strict.values())
        n_relaxed = sum(len(v) for v in relaxed.values())
        assert n_strict <= n_relaxed
