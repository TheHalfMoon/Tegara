"""Offline CSV options-quote ingestion with strict identity and quality diagnostics.

This module cannot establish data licensing or OPRA provenance. No file content
or feed label ever upgrades source trust or authorizes live trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import os
import stat
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import json

from .core import OptionContract, OptionQuote, _money
from .quote_replay import ReplayTick

FIELDS = (
    "observed_at", "symbol", "right", "strike", "expiration", "multiplier",
    "bid", "ask", "bid_size", "ask_size", "feed_type", "source",
)
MAX_CSV_BYTES = 48 * 1024 * 1024
MAX_ROWS = 60000
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class QuoteQuality:
    """Diagnostics only. The word QUALIFIED is deliberately never emitted."""

    rows: int
    symbol: str
    right: str
    expiration: str
    first_at: str
    last_at: str
    max_gap_seconds: str
    zero_bid_rows: int
    insufficient_bid_size_rows: int
    indicative_rows: int
    locked_market_rows: int
    max_spread_percent_of_ask: str
    provenance_status: str = "NOT_INDEPENDENTLY_VERIFIED"
    licensing_status: str = "NOT_INDEPENDENTLY_VERIFIED"
    venue_cutoff_status: str = "NOT_INDEPENDENTLY_VERIFIED"
    live_eligible: bool = False
    csv_sha256: str = ""  # exactly the bytes parsed, never a source license certificate

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _decimal(value: str, name: str) -> Decimal:
    if not value or len(value) > 100:
        raise ValueError(f"{name} missing or too long")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    return _money(number, name)


def _size(value: str, name: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a nonnegative ASCII integer")
    amount = int(value)
    if amount > 100000000:
        raise ValueError(f"{name} exceeds supported bound")
    return amount


def read_option_csv(
    source: str | Path,
    *,
    expected_contract: OptionContract,
    expected_quantity: int,
    last_trading_at: datetime | None = None,
) -> tuple[list[ReplayTick], QuoteQuality]:
    """Read one contract's quotes and return strictly NON-AUTHORITATIVE metrics."""
    if type(expected_quantity) is not int or expected_quantity < 1:
        raise ValueError("expected_quantity must be a positive integer")
    if last_trading_at is not None:
        if (not isinstance(last_trading_at, datetime) or
                last_trading_at.tzinfo is None or
                last_trading_at.utcoffset() is None):
            raise ValueError("last_trading_at must be timezone-aware")
        if last_trading_at.astimezone(NY).date() != expected_contract.expiration:
            raise ValueError("last trading cutoff must be on the NY expiration date")
    path = Path(source)
    if not path.is_file() or path.is_symlink():
        raise ValueError("source must be an existing regular non-symlink CSV")
    # Snapshot bytes once so data-hash evidence and parsed observations are
    # the SAME bytes. O_NOFOLLOW prevents symlink swapping during open.
    if not getattr(os, "O_NOFOLLOW", 0):
        raise ValueError("atomic no-follow CSV opening is unavailable")
    fd = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        with os.fdopen(fd, "rb") as source_file:
            if not stat.S_ISREG(os.fstat(source_file.fileno()).st_mode):
                raise ValueError("source must be a regular file")
            raw = source_file.read(MAX_CSV_BYTES + 1)
    except Exception:
        # fdopen owns/automatically closes the fd once entered.
        raise
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError("CSV is too large for local ingestion limit")
    content_digest = sha256(raw).hexdigest()
    quotes: list[ReplayTick] = []
    previous: datetime | None = None
    max_gap = timedelta(0)
    zero_bid = insufficient_bid = indicative = locked = 0
    biggest_spread = Decimal(0)
    with TextIOWrapper(BytesIO(raw), encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != FIELDS:
            raise ValueError("CSV must have the exact Tegara quote columns in order")
        for i, row in enumerate(reader, start=1):
            if i > MAX_ROWS:
                raise ValueError("CSV exceeds maximum supported row count")
            if None in row or any(v is None or "\x00" in v for v in row.values()):
                raise ValueError(f"row {i} has wrong number of fields or NUL bytes")
            try:
                observed = datetime.fromisoformat(row["observed_at"])
                if observed.tzinfo is None or observed.utcoffset() is None:
                    raise ValueError("timestamp must have UTC offset")
                # Constrain observation to expiration *date* in NYC, not merely
                # the local machine date. Venue time cutoffs remain unverified.
                if observed.astimezone(NY).date() != expected_contract.expiration:
                    raise ValueError("observation is not on the NY expiration date")
                if last_trading_at is not None and observed >= last_trading_at:
                    # This is an exclusive upper bound: a quote stamped
                    # exactly at last-trade cutoff is already non-actionable.
                    raise ValueError("quote is at or after explicit last trading cutoff")
                if previous is not None:
                    if observed <= previous:
                        raise ValueError("out-of-order or repeated timestamp")
                    max_gap = max(max_gap, observed - previous)
                if (row["symbol"], row["right"], row["strike"],
                        row["expiration"], row["multiplier"]) != (
                    expected_contract.symbol,
                    expected_contract.right,
                    str(expected_contract.strike),
                    expected_contract.expiration.isoformat(),
                    str(expected_contract.multiplier),
                ):
                    raise ValueError("contract identity mismatch")
                bid = _decimal(row["bid"], "bid")
                ask = _decimal(row["ask"], "ask")
                bid_size = _size(row["bid_size"], "bid_size")
                ask_size = _size(row["ask_size"], "ask_size")
                feed = row["feed_type"]
                if feed not in ("EXACT_NBBO", "INDICATIVE"):
                    raise ValueError("unrecognized feed type")
                vendor = row["source"]
                if not vendor or len(vendor) > 120 or not vendor.isprintable():
                    raise ValueError("invalid source field")
                quote = OptionQuote(expected_contract, bid, ask, bid_size,
                                    observed, vendor, feed)
                quotes.append(ReplayTick(quote, ask_size))
                if bid == 0:
                    zero_bid += 1
                if bid_size < expected_quantity:
                    insufficient_bid += 1
                if feed != "EXACT_NBBO":
                    indicative += 1
                if ask == bid:
                    locked += 1
                if ask > 0:
                    biggest_spread = max(biggest_spread, (ask - bid) / ask * 100)
                previous = observed
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid quote at row {i}: {exc}") from exc

    if not quotes:
        raise ValueError("CSV contains no quote rows")

    quality = QuoteQuality(
        rows=len(quotes), symbol=expected_contract.symbol,
        right=expected_contract.right,
        expiration=expected_contract.expiration.isoformat(),
        first_at=quotes[0].quote.observed_at.isoformat(),
        last_at=quotes[-1].quote.observed_at.isoformat(),
        max_gap_seconds=str(Decimal(str(max_gap.total_seconds()))),
        zero_bid_rows=zero_bid,
        insufficient_bid_size_rows=insufficient_bid,
        indicative_rows=indicative, locked_market_rows=locked,
        max_spread_percent_of_ask=str(biggest_spread),
        venue_cutoff_status=("USER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED"
                             if last_trading_at is not None
                             else "NOT_INDEPENDENTLY_VERIFIED"),
        csv_sha256=content_digest,
    )
    return quotes, quality


def quality_json(report: QuoteQuality) -> str:
    """Stable offline diagnostic output, never a market-data certification."""
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)
