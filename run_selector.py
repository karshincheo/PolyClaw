from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow zero-install runs: the package lives under src/ (src-layout).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from polyclaw.selector.pipeline import SelectionPipeline  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PolyClaw: Polymarket bet selection framework (top 5 picks per category)."
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/sample_markets.json",
        help="Path to JSON markets payload (ignored when --live is set).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch live markets from Polymarket public APIs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=600,
        help="Maximum number of live markets to fetch when --live is set.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/selection_output.json",
        help="Path to write selection output JSON.",
    )
    parser.add_argument(
        "--external-signals",
        type=str,
        default=None,
        help="Path to external signals JSON used to estimate fair probabilities.",
    )
    parser.add_argument(
        "--require-external",
        action="store_true",
        help="Only allow markets that have matched external signals.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print output JSON to stdout.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    pipeline = SelectionPipeline(
        external_signals_path=args.external_signals,
        require_external_signal=args.require_external,
    )
    if args.live:
        results = pipeline.run_with_public_api(limit=args.limit)
    else:
        results = pipeline.run_from_file(args.input)
    pipeline.write_output(args.output, results)

    if args.pretty:
        print(json.dumps(pipeline.to_output_dict(results), indent=2))


if __name__ == "__main__":
    main()
