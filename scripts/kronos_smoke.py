"""Opt-in Kronos upstream smoke test on SYNTHETIC underlying OHLCV only.

Requires a separately acquired official Kronos checkout and public model
weights. It does NOT connect to any broker, order API or real market feed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

from tegara.kronos_bridge import MarketBar, create_window, forecast_underlying


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic local Kronos inference smoke test")
    parser.add_argument("--upstream-root", required=True,
                        help="Trusted local clone of shiyu-coder/Kronos")
    parser.add_argument("--model-id", default="NeoQuasar/Kronos-small",
                        choices=["NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-small"])
    parser.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    args = parser.parse_args()
    root = Path(args.upstream_root).expanduser().resolve()
    if not (root / "model" / "__init__.py").is_file():
        parser.error("upstream-root must contain the official Kronos model package")

    sys.path.insert(0, str(root))
    from model import Kronos, KronosTokenizer, KronosPredictor
    import torch

    torch.set_num_threads(2)
    print("Loading openly available upstream Kronos model; checkpoint download "
          "may occur on first use. Model input is synthetic.")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained(args.model_id)
    predictor = KronosPredictor(model, tokenizer, device=args.device, max_context=512)
    t0 = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
    bars = [
        MarketBar(
            t0 + timedelta(minutes=i),
            Decimal("100") + Decimal(i % 3) * Decimal("0.01"),
            Decimal("100.2") + Decimal(i % 3) * Decimal("0.01"),
            Decimal("99.8"), Decimal("100.1"), Decimal("1000") + i,
        )
        for i in range(40)
    ]
    window = create_window(
        bars, horizon_bars=3, interval=timedelta(minutes=1),
        last_trading_at=t0 + timedelta(hours=6),
    )
    result = forecast_underlying(
        predictor, window, model_label=args.model_id, sample_count=1
    )
    print("STATUS", result.status)
    print("SYNTHETIC_INPUT", True)
    print("FUTURE_UNDERLYING_CLOSES", [str(x) for x in result.predicted_closes])
    print("OPTIONS_PNL_VALIDATED", result.options_pnl_validated)
    print("TRADE_ORDER_CREATED", result.trade_order_created)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
