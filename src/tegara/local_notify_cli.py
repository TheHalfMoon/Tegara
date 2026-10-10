"""Explicit opt-in LOCAL macOS research notification demo, never a live alert."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import sys

from .core import LongOptionPosition, OptionContract
from .local_notify import make_manual_demo_notice, show_macos_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline demo target notification for manual option BID input"
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--right", choices=["C", "P"], required=True)
    parser.add_argument("--strike", required=True)
    parser.add_argument("--expiration", required=True)
    parser.add_argument("--multiplier", type=int, default=100)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--quantity", type=int, required=True)
    parser.add_argument("--entry-fee", required=True)
    parser.add_argument("--exit-fee", required=True)
    parser.add_argument("--net-profit", required=True)
    parser.add_argument("--manual-bid", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--send-local", action="store_true")
    parser.add_argument("--acknowledge-demo-only", action="store_true",
                        help="Required for a local OS demo; never authorizes live option trading")
    args = parser.parse_args(argv)
    try:
        def dec(x: str) -> Decimal:
            return Decimal(x)
        contract = OptionContract(
            args.symbol, args.right, dec(args.strike),
            date.fromisoformat(args.expiration), args.multiplier
        )
        position = LongOptionPosition(
            contract, dec(args.entry), args.quantity,
            dec(args.entry_fee), dec(args.exit_fee),
        )
        notice = make_manual_demo_notice(
            position, manual_bid=dec(args.manual_bid),
            target_net_profit=dec(args.net_profit)
        )
        if args.send_local and args.acknowledge_demo_only is not True:
            raise ValueError("--acknowledge-demo-only is required for --send-local")
        local_request_accepted = False
        if args.send_local:
            local_request_accepted = show_macos_demo(
                notice, explicitly_authorized=args.acknowledge_demo_only
            )
        print(json.dumps({
            "status": notice.status,
            "title": notice.title, "body": notice.body,
            "local_os_request_accepted": local_request_accepted,
            "phone_delivery_verified": notice.phone_delivery_verified,
            "market_data_verified": notice.market_data_verified,
            "broker_execution_verified": notice.broker_execution_verified,
        }, indent=2, sort_keys=True))
        return 0
    except (ValueError, InvalidOperation, RuntimeError, OverflowError) as exc:
        print(f"Notification research demo failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
