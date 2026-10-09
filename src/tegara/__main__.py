"""Offline options target calculator. No broker API, model, or live quotes."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json

from .core import (
    LongOptionPosition, OptionContract, calculate_net_exit_pnl,
    minimum_bid_for_net_profit, minimum_bid_for_net_return_percent,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m tegara",
                                   description="Research-only Tegara target calculator")
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("target", help="Calculate a candidate option-bid exit target")
    p.add_argument("--symbol", required=True)
    p.add_argument("--right", choices=("C", "P"), required=True)
    p.add_argument("--strike", required=True)
    p.add_argument("--expiration", required=True)
    p.add_argument("--multiplier", type=int, default=100)
    p.add_argument("--entry", required=True)
    p.add_argument("--quantity", type=int, required=True)
    p.add_argument("--entry-fee", required=True,
                   help="Actual per-contract buy fee, including applicable taxes")
    p.add_argument("--exit-fee", required=True,
                   help="Expected per-contract sell fee, including applicable taxes")
    goals = p.add_mutually_exclusive_group(required=True)
    goals.add_argument("--net-profit", help="Net USD profit target for all remaining contracts")
    goals.add_argument("--net-return", help="Net return percentage on initial debit")
    p.add_argument("--tick", required=True,
                   help="Explicit valid option-price increment, e.g. 0.01 or 0.05")
    return root


def _dec(value: str) -> Decimal:
    try:
        x = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid Decimal: {value}") from exc
    if not x.is_finite() or x < 0:
        raise ValueError("all monetary/goal amounts must be finite and nonnegative")
    return x


def calculate_target(args: argparse.Namespace) -> dict[str, str]:
    tick = _dec(args.tick)
    if tick <= 0:
        raise ValueError("--tick must be greater than 0")
    contract = OptionContract(
        args.symbol, args.right, _dec(args.strike),
        date.fromisoformat(args.expiration), args.multiplier
    )
    position = LongOptionPosition(
        contract, _dec(args.entry), args.quantity,
        _dec(args.entry_fee), _dec(args.exit_fee)
    )
    target = (
        minimum_bid_for_net_profit(position, _dec(args.net_profit))
        if args.net_profit is not None
        else minimum_bid_for_net_return_percent(position, _dec(args.net_return))
    )
    rounded = (target / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    return {
        "symbol": contract.symbol,
        "right": contract.right,
        "strike": str(contract.strike),
        "expiration": contract.expiration.isoformat(),
        "open_quantity": str(position.quantity),
        "initial_debit_usd": str(position.initial_debit),
        "unrounded_target_bid": str(target),
        "rounded_target_bid": str(rounded),
        "price_tick": str(tick),
        "estimated_net_pnl_at_target_usd": str(calculate_net_exit_pnl(position, rounded)),
        "status": "HYPOTHETICAL_TARGET_ONLY",
        "warning": ("No live quote checked. Trading/broker fees must be supplied "
                    "accurately. Estimated option bid is not a guaranteed fill."),
    }


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(calculate_target(args), indent=2))
        return 0
    except (ValueError, OverflowError) as exc:
        parser().error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
