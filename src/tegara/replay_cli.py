"""Offline research-only options quote CSV replay command.

This is not a market-data authenticator. User permission to run a hypothetical
simulation is NOT independent evidence of feed/OPRA license or executed fills.
No persistent writes, remote calls, notifications, or brokerage actions.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import sys

from .core import OptionContract
from .latency_replay import DelayedReplay, delayed_long_option
from .latency_sensitivity import latency_sensitivity, parse_latency_axis
from .quote_import import read_option_csv
from .quote_replay import ReplayOutcome, ReplayResult, ReplayRules, replay_long_option

STATUS = "RESEARCH_ONLY_NOT_LIVE_TRADING"


def decimal_amount(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("amount must be a valid Decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("amount must be finite and nonnegative")
    if result > Decimal("1000000000"):
        raise ValueError("amount exceeds supported research bound")
    return result


def aware_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone offset")
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tegara.replay_cli",
        description="Offline hypothetical option quote replay; no executed trade claims",
    )
    p.add_argument("--csv", required=True, help="One-contract CSV; source not uploaded")
    p.add_argument("--symbol", required=True)
    p.add_argument("--right", choices=("C", "P"), required=True)
    p.add_argument("--strike", required=True)
    p.add_argument("--expiration", required=True)
    p.add_argument("--multiplier", type=int, default=100)
    p.add_argument("--quantity", type=int, required=True)
    p.add_argument("--entry-fee", required=True)
    p.add_argument("--exit-fee", required=True)
    p.add_argument("--entry-slippage", required=True,
                   help="Premium per deliverable share, added to Ask")
    p.add_argument("--exit-slippage", required=True,
                   help="Premium per deliverable share, subtracted from Bid")
    p.add_argument("--target-net-profit", required=True,
                   help="Total hypothetical USD net profit over all contracts")
    p.add_argument("--max-net-loss", required=True,
                   help="Total hypothetical USD loss threshold; not guaranteed stop fill")
    p.add_argument("--entry-not-before", required=True,
                   help="Earliest permitted entry decision timestamp, with UTC offset")
    p.add_argument("--deadline", required=True, help="Time with UTC offset")
    p.add_argument("--last-trading-at", required=True,
                   help="Contract-specific cutoff; user-provided, NOT venue verified")
    p.add_argument("--max-gap-seconds", required=True, type=int)
    p.add_argument("--execution-mode", choices=("instant-reference", "delayed"),
                   default="delayed",
                   help="Delayed (default) uses later quote; instant-reference is optimistic and opt-in")
    p.add_argument("--entry-latency-seconds", type=int, default=1)
    p.add_argument("--exit-latency-seconds", type=int, default=1)
    p.add_argument("--entry-latency-grid",
                   help="Research sensitivity axis: e.g. 1,5,30 (max 4 distinct)")
    p.add_argument("--exit-latency-grid",
                   help="Research sensitivity axis: e.g. 1,5,30 (max 4 distinct)")
    p.add_argument("--allow-hypothetical-pnl", action="store_true",
                   help="Explicitly request research P&L; NOT source/license certification")
    return p


def evaluate(args: argparse.Namespace) -> dict:
    contract = OptionContract(
        args.symbol, args.right, decimal_amount(args.strike),
        date.fromisoformat(args.expiration), args.multiplier,
    )
    if type(args.max_gap_seconds) is not int or not 1 <= args.max_gap_seconds <= 3600:
        raise ValueError("max-gap-seconds must be between 1 and 3600")
    for field in ("entry_latency_seconds", "exit_latency_seconds"):
        value = getattr(args, field)
        if type(value) is not int or not 1 <= value <= 300:
            raise ValueError(f"{field} must be an integer from 1 to 300")
    grid_requested = (args.entry_latency_grid is not None
                      or args.exit_latency_grid is not None)
    entry_axis = exit_axis = None
    if grid_requested:
        if (args.entry_latency_grid is None or
                args.exit_latency_grid is None):
            raise ValueError("both latency grid axes are required")
        if args.execution_mode != "delayed":
            raise ValueError("latency grid requires delayed execution mode")
        entry_axis = parse_latency_axis(
            args.entry_latency_grid, name="entry-latency-grid"
        )
        exit_axis = parse_latency_axis(
            args.exit_latency_grid, name="exit-latency-grid"
        )
    deadline = aware_time(args.deadline)
    cutoff = aware_time(args.last_trading_at)
    entry_not_before = aware_time(args.entry_not_before)
    if entry_not_before >= min(deadline, cutoff):
        raise ValueError("entry-not-before must be earlier than both exit deadline and cutoff")
    rules = ReplayRules(
        contract=contract,
        quantity=args.quantity,
        entry_fee_per_contract=decimal_amount(args.entry_fee),
        exit_fee_per_contract=decimal_amount(args.exit_fee),
        entry_slippage_per_share=decimal_amount(args.entry_slippage),
        exit_slippage_per_share=decimal_amount(args.exit_slippage),
        target_net_profit=decimal_amount(args.target_net_profit),
        maximum_net_loss=decimal_amount(args.max_net_loss),
        deadline=deadline,
        max_quote_gap=timedelta(seconds=args.max_gap_seconds),
        last_trading_at=cutoff,
    )
    ticks, quality = read_option_csv(
        args.csv, expected_contract=contract,
        expected_quantity=args.quantity,
        last_trading_at=cutoff,
    )
    # The name of the library parameter dates from an earlier prototype.
    # Even when true, the flag is ONLY user consent to inspect hypothetical
    # P&L, never proof of license, NBBO provenance or executable liquidity.
    allow_hypothetical = args.allow_hypothetical_pnl is True
    # Always preserve full-file quality diagnostics, but only offer quote
    # observations known at/after the user's explicit entry decision.
    available = [
        tick for tick in ticks if tick.quote.observed_at >= entry_not_before
    ]
    first = available[0] if available else None
    stress_grid = None
    if grid_requested:
        stress_grid = latency_sensitivity(
            ticks, rules, entry_decision_at=entry_not_before,
            entry_delays=entry_axis, exit_delays=exit_axis,
            allow_hypothetical=allow_hypothetical,
        )
        # A stress grid is not a single putative trade/fill and MUST NOT
        # have a scalar P&L masquerading as one.
        result = DelayedReplay(
            ReplayOutcome.UNQUALIFIED_DATA, entry_not_before,
            note="Fixed latency sensitivity grid, not one executed trade",
        )
    elif args.execution_mode == "delayed":
        result = delayed_long_option(
            ticks, rules, entry_decision_at=entry_not_before,
            entry_latency=timedelta(seconds=args.entry_latency_seconds),
            exit_latency=timedelta(seconds=args.exit_latency_seconds),
            allow_hypothetical=allow_hypothetical,
        )
    elif first is None or (
        first.quote.observed_at - entry_not_before > rules.max_quote_gap
    ):
        result = ReplayResult(
            ReplayOutcome.NO_ENTRY_EVIDENCE,
            note="No promptly observable quote after the explicit entry start",
        )
    else:
        result = replay_long_option(
            available, rules, provenance_approved=allow_hypothetical,
        )
    # Abstention due to unapproved data takes precedence even when there
    # were no entry quotes in the requested window.
    if not allow_hypothetical:
        result = (
            DelayedReplay(
                ReplayOutcome.UNQUALIFIED_DATA, entry_not_before,
                note="Hypothetical monetary replay was not explicitly requested",
            ) if args.execution_mode == "delayed"
            else ReplayResult(
                ReplayOutcome.UNQUALIFIED_DATA,
                note="Hypothetical monetary replay was not explicitly requested",
            )
        )
    is_delayed = args.execution_mode == "delayed"
    money = (result.hypothetical_net_pnl if is_delayed
             else result.simulated_net_pnl)
    return {
        "status": STATUS,
        "input_quality": quality.as_dict(),
        "contract": {
            "symbol": contract.symbol,
            "right": contract.right,
            "strike": str(contract.strike),
            "expiration": contract.expiration.isoformat(),
            "multiplier": contract.multiplier,
            "quantity": args.quantity,
        },
        "parameters": {
            "entry_fee_per_contract_usd": str(rules.entry_fee_per_contract),
            "exit_fee_per_contract_usd": str(rules.exit_fee_per_contract),
            "entry_slippage_per_share": str(rules.entry_slippage_per_share),
            "exit_slippage_per_share": str(rules.exit_slippage_per_share),
            "target_net_profit_usd": str(rules.target_net_profit),
            "maximum_net_loss_usd": str(rules.maximum_net_loss),
            "entry_not_before": entry_not_before.isoformat(),
            "deadline": deadline.isoformat(),
            "last_trading_at_user_supplied": cutoff.isoformat(),
            "max_gap_seconds": args.max_gap_seconds,
            "execution_mode": args.execution_mode,
            "entry_latency_seconds": (args.entry_latency_seconds
                                      if is_delayed else None),
            "exit_latency_seconds": (args.exit_latency_seconds
                                     if is_delayed else None),
        },
        "hypothetical_pnl_requested": allow_hypothetical,
        "decision_trigger_at": (
            result.trigger_at.isoformat() if is_delayed and result.trigger_at else None
        ),
        "outcome": (
            "LATENCY_SENSITIVITY_ONLY"
            if grid_requested and allow_hypothetical else result.outcome.value
        ),
        "latency_sensitivity": stress_grid,
        "entry_at": result.entry_at.isoformat() if result.entry_at else None,
        "exit_at": result.exit_at.isoformat() if result.exit_at else None,
        "entry_premium": str(result.entry_premium) if result.entry_premium is not None else None,
        "exit_premium": str(result.exit_premium) if result.exit_premium is not None else None,
        "hypothetical_net_pnl_usd": str(money) if money is not None else None,
        "outcome_note": result.note,
        "source_provenance_independently_verified": False,
        "data_licensing_independently_verified": False,
        "last_trading_time_independently_verified": False,
        "broker_execution_verified": False,
        "phone_notification_sent": False,
        "options_profitability_validated": False,
        "warning": (
            "Hypothetical quotes only: source/feed labels and a manual cutoff "
            "do not prove OPRA licensing, NBBO validity, actual order fills, "
            "broker commissions, or ability to exit at the simulated price. "
            "Instant-reference replays use same-event hypothetical triggers; "
            "delayed replays still do not prove queue priority or execution."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(evaluate(args), sort_keys=True, indent=2))
        return 0
    except (ValueError, TypeError, OSError, OverflowError,
            csv.Error, UnicodeError) as exc:
        print(f"Offline research replay rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
