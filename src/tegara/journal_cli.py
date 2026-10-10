"""Persistent offline long-option journal CLI.

All positions and fills are manually user-declared. No Derayah API, broker
credentials, actual market feed, or network is used. This CLI never trades.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import sys

from ._sqlite_writable import _canonical_path
from .core import LongOptionPosition, OptionContract, _money
from .position_store import PositionStore


def decimal_value(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal amount") from exc
    return _money(value, "journal amount")


def parsed_time(raw: str) -> datetime:
    try:
        at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timestamp must contain an explicit timezone offset")
    return at


def journal_path(raw: str, *, creation: bool) -> Path:
    """Prevent accidental commit of personal trades or symlink traversal."""
    if raw == ":memory:":
        raise ValueError("journal requires an actual persistent file")
    # Never resolve caller-controlled intermediate symlinks: the guarded
    # PositionStore opener must see and reject those original components.
    path = _canonical_path(raw)
    if path.is_symlink():
        raise ValueError("journal symlink refused")
    repository = Path(__file__).resolve().parents[2]
    if path == repository or repository in path.parents:
        raise ValueError("journal database must be outside Tegara repository")
    if not creation and not path.is_file():
        raise ValueError("journal does not exist; open a position first")
    if path.exists() and not path.is_file():
        raise ValueError("journal must be a regular file")
    return path


def configure() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m tegara.journal_cli",
        description="Offline user-entered options journal; NEVER submits broker orders",
    )
    root.add_argument("--db", required=True,
                      help="Private local SQLite file OUTSIDE the Git repository")
    group = root.add_subparsers(dest="action", required=True)
    opening = group.add_parser("open", help="Manually record one long-option position")
    opening.add_argument("--position-id", required=True)
    opening.add_argument("--symbol", required=True)
    opening.add_argument("--right", choices=["C", "P"], required=True)
    opening.add_argument("--strike", required=True)
    opening.add_argument("--expiration", required=True)
    opening.add_argument("--multiplier", type=int, default=100)
    opening.add_argument("--entry", required=True)
    opening.add_argument("--quantity", required=True, type=int)
    opening.add_argument("--entry-fee", required=True)
    opening.add_argument("--exit-fee", required=True)
    opening.add_argument("--opened-at", required=True)
    opening.add_argument("--record-only", action="store_true")

    closing = group.add_parser("manual-close", help="Record a claimed manual sell fill")
    closing.add_argument("--position-id", required=True)
    closing.add_argument("--fill-id", required=True)
    closing.add_argument("--quantity", required=True, type=int)
    closing.add_argument("--fill-price", required=True)
    closing.add_argument("--fee-per-contract", required=True)
    closing.add_argument("--filled-at", required=True)
    closing.add_argument("--record-only", action="store_true")

    show = group.add_parser("show", help="Show manually recorded position state")
    show.add_argument("--position-id", required=True)
    fills = group.add_parser("fills", help="List manually recorded fill events")
    fills.add_argument("--position-id", required=True)
    return root


def snapshot_dict(snapshot) -> dict:
    return {
        "position_id": snapshot.position_id,
        "contract": {
            "symbol": snapshot.contract.symbol,
            "right": snapshot.contract.right,
            "strike": str(snapshot.contract.strike),
            "expiration": snapshot.contract.expiration.isoformat(),
            "multiplier": snapshot.contract.multiplier,
        },
        "quantity_open": snapshot.open_quantity,
        "quantity_original": snapshot.initial_quantity,
        "entry_price": str(snapshot.entry_price),
        "entry_fee_per_contract": str(snapshot.entry_fee),
        "expected_exit_fee_per_contract": str(snapshot.exit_fee),
        "opened_at": snapshot.opened_at.isoformat(),
        "realized_net_pnl_user_declared_usd": str(snapshot.realized_net_pnl),
        "position_status": snapshot.status.value,
        "evidence_status": "USER_DECLARED_UNVERIFIED",
        "broker_execution_verified": False,
        "market_feed_verified": False,
        "live_trading_enabled": False,
    }


def execute(args: argparse.Namespace) -> dict:
    writing = args.action in ("open", "manual-close")
    path = journal_path(args.db, creation=writing)
    if writing and args.record_only is not True:
        raise ValueError("write requires --record-only; this never executes a broker trade")
    # Parse/validate every open argument before touching the filesystem,
    # so a malformed user command does not create an orphaned journal.
    position = None
    opened_at = None
    if args.action == "open":
        contract = OptionContract(
            args.symbol, args.right, decimal_value(args.strike),
            date.fromisoformat(args.expiration), args.multiplier,
        )
        position = LongOptionPosition(
            contract, decimal_value(args.entry), args.quantity,
            decimal_value(args.entry_fee), decimal_value(args.exit_fee),
        )
        opened_at = parsed_time(args.opened_at)
        if not args.position_id or len(args.position_id) > 128:
            raise ValueError("invalid position identifier")
    with PositionStore(path) as store:
        if args.action == "open":
            snap = store.open(args.position_id, position, opened_at)
            return {"event": "USER_DECLARED_POSITION_OPENED",
                    **snapshot_dict(snap)}
        if args.action == "manual-close":
            snap = store.get(args.position_id)
            updated, fill_net, inserted = store.confirm_manual_close(
                args.position_id, args.fill_id, snap.contract,
                args.quantity, decimal_value(args.fill_price),
                decimal_value(args.fee_per_contract),
                parsed_time(args.filled_at),
            )
            return {
                "event": "USER_DECLARED_MANUAL_FILL",
                "newly_recorded": inserted,
                "manual_fill_net_pnl_usd": str(fill_net),
                "statement_independently_verified": False,
                **snapshot_dict(updated),
            }
        if args.action == "show":
            return {"event": "USER_DECLARED_POSITION_SNAPSHOT",
                    **snapshot_dict(store.get(args.position_id))}
        if args.action == "fills":
            store.get(args.position_id)  # fail for unknown position IDs
            rows = store.db.execute(
                "SELECT fill_id, qty, price, exit_fee, at, net FROM fills "
                "WHERE position_id=? ORDER BY at, fill_id", (args.position_id,)
            ).fetchall()
            return {
                "position_id": args.position_id,
                "events": [{
                    "fill_id": row["fill_id"],
                    "quantity": row["qty"],
                    "price": row["price"],
                    "fee_per_contract": row["exit_fee"],
                    "reported_at": row["at"],
                    "recorded_net_pnl_usd": row["net"],
                } for row in rows],
                "statement_independently_verified": False,
                "broker_execution_verified": False,
                "status": "USER_DECLARED_UNVERIFIED",
            }
    raise ValueError("unsupported journal operation")


def main(argv: list[str] | None = None) -> int:
    args = configure().parse_args(argv)
    try:
        print(json.dumps(execute(args), indent=2, sort_keys=True))
        return 0
    except (ValueError, OverflowError, KeyError, sqlite3.Error, OSError) as exc:
        print(f"Offline journal error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
