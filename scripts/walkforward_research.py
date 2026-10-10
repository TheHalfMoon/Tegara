"""Explicit local-only underlying data audit / Kronos walk-forward runner.

Invocation requires user-provided CSVs and an explicitly verified session
cutoff per trading date. Never places, suggests or simulates option orders.
"""
from __future__ import annotations

import argparse
import sys
from zoneinfo import ZoneInfo

from tegara.underlying_research import (
    ResearchParameters, load_bars, load_cutoffs, report_json, walk_forward
)

NY = ZoneInfo("America/New_York")


def parse(argv=None):
    p = argparse.ArgumentParser(
        description="Research-only underlying OHLCV audit and Kronos walk-forward"
    )
    p.add_argument("--csv", required=True,
                   help="Local underlying OHLCV file (strict timestamp,open,high,low,close,volume)")
    p.add_argument("--cutoffs", required=True,
                   help="Local explicit NY session cutoff CSV; no inferred market close")
    p.add_argument("--interval-minutes", type=int, choices=[1, 5], required=True)
    p.add_argument("--lookback", type=int, default=40)
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--stride", type=int, default=10)
    p.add_argument("--holdout-sessions", type=int, default=1)
    p.add_argument("--max-windows", type=int, default=20)
    p.add_argument("--inspect-only", action="store_true",
                   help="Check input CSV format and hash with no model downloads")
    p.add_argument("--upstream-root",
                   help="Required for model evaluation: trusted local official Kronos checkout")
    p.add_argument("--model-id", choices=["NeoQuasar/Kronos-mini",
                                          "NeoQuasar/Kronos-small"],
                   default="NeoQuasar/Kronos-small")
    p.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    return p.parse_args(argv)


def run(args) -> dict:
    bars, data_digest = load_bars(args.csv)
    cutoffs, cutoff_digest = load_cutoffs(args.cutoffs)
    params = ResearchParameters(
        args.interval_minutes, args.lookback, args.horizon,
        args.stride, args.holdout_sessions, args.max_windows
    )
    sessions = sorted({b.observed_at.astimezone(NY).date() for b in bars})
    manifest = {
        "data_sha256": data_digest,
        "cutoffs_sha256": cutoff_digest,
        "bars": len(bars),
        "sessions": [d.isoformat() for d in sessions],
        "parameters": {
            "interval_minutes": params.interval_minutes,
            "lookback": params.lookback_bars,
            "horizon": params.horizon_bars,
            "stride": params.stride_bars,
            "holdout_sessions": params.holdout_sessions,
            "max_windows": params.max_windows,
        },
        "market_data_rights_independently_verified": False,
        "contract_cutoffs_independently_verified": False,
        "options_execution_validated": False,
    }
    # All file quality verification lives in walk_forward. In audit mode we
    # call it using a strict mock only on target sessions and label clearly.
    if args.inspect_only:
        from datetime import timedelta
        # Force per-day cutoff presence/validation without calling any model.
        if set(sessions) != set(cutoffs):
            raise ValueError("cutoff file must match all underlying session dates")
        if len(sessions) <= params.holdout_sessions:
            raise ValueError("insufficient earlier sessions before holdout")
        for day in sessions:
            subset = [b for b in bars if b.observed_at.astimezone(NY).date() == day]
            for prior, later in zip(subset, subset[1:]):
                if later.observed_at - prior.observed_at != timedelta(
                    minutes=params.interval_minutes
                ):
                    raise ValueError("gap in historical underlying bars")
            if subset[-1].observed_at >= cutoffs[day]:
                raise ValueError("bar is at or after explicit session cutoff")
        for day in sessions[-params.holdout_sessions:]:
            subset = [b for b in bars if b.observed_at.astimezone(NY).date() == day]
            if len(subset) < params.lookback_bars + params.horizon_bars:
                raise ValueError("heldout NY session lacks enough bars for lookback and horizon")
        manifest["status"] = "OFFLINE_UNDERLYING_AUDIT_ONLY"
        manifest["no_model_was_loaded"] = True
        return manifest

    if not args.upstream_root:
        raise ValueError("--upstream-root required for Kronos model evaluation")
    from tegara.kronos_bridge import verify_pinned_kronos_checkout
    root = verify_pinned_kronos_checkout(args.upstream_root)
    # Only opt-in model mode can load upstream code or HF public checkpoint.
    sys.path.insert(0, str(root))
    from model import Kronos, KronosTokenizer, KronosPredictor
    import torch
    torch.set_num_threads(2)
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained(args.model_id)
    predictor = KronosPredictor(model, tokenizer, device=args.device, max_context=512)
    report = walk_forward(
        predictor, bars, cutoffs, params, model_label=args.model_id
    )
    return {**manifest, **report}


def main(argv=None) -> int:
    args = parse(argv)
    try:
        print(report_json(run(args)))
    except (ValueError, TypeError, OSError, ImportError, RuntimeError) as exc:
        print(f"Research validation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
