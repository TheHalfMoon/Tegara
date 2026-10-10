"""Convert user-exported TradingView-like OHLCV to Tegara time-available bars.

No web requests. Conversion never confers a market-data retention license.
An OPEN-timestamped bar is shifted to its *completion* time to avoid
lookahead using the close/high/low before they were observable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import csv
import re
import sys

SOURCE_HEADER = ("time", "open", "high", "low", "close", "Volume")
TARGET_HEADER = ("timestamp", "open", "high", "low", "close", "volume")
MAX_SOURCE = 32 * 1024 * 1024
MAX_ROWS = 60000
_NUMERIC = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")


def parse_timestamp(value: str, *, source_tz: str | None = None) -> datetime:
    if not value:
        raise ValueError("empty candle time")
    try:
        if re.fullmatch(r"[0-9]{10}|[0-9]{13}", value):
            amount = int(value)
            if len(value) == 13:
                amount /= 1000
            return datetime.fromtimestamp(amount, tz=timezone.utc)
        time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if time.tzinfo is None or time.utcoffset() is None:
            if not source_tz:
                raise ValueError("naive timestamp needs explicit source IANA time zone")
            tz = ZoneInfo(source_tz)
            candidate = time.replace(tzinfo=tz)
            # Spring-forward gaps and fall-back repeated hours both exhibit
            # fold differences. Check round-trip validity first to distinguish.
            if candidate.astimezone(timezone.utc).astimezone(tz).replace(
                tzinfo=None
            ) != time:
                raise ValueError("nonexistent DST source timestamp")
            if candidate.replace(fold=1).utcoffset() != candidate.utcoffset():
                raise ValueError("ambiguous DST source timestamp")
            return candidate.astimezone(timezone.utc)
        return time.astimezone(timezone.utc)
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"invalid candle time: {exc}") from exc


def convert_tradingview(
    path: str | Path, *, interval_minutes: int,
    bar_time_is: str, source_timezone: str | None = None,
) -> list[dict[str, str]]:
    """Return Tegara bars with timestamp = data-available/end-of-bar time."""
    if type(interval_minutes) is not int or interval_minutes not in (1, 5):
        raise ValueError("only 1- or 5-minute candles are supported")
    if bar_time_is not in ("open", "close"):
        raise ValueError("bar_time_is must be explicitly set to open or close")
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_SOURCE:
        raise ValueError("input must be ordinary CSV <=32MiB")
    out = []
    prev: datetime | None = None
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        if tuple(reader.fieldnames or ()) != SOURCE_HEADER:
            raise ValueError("source CSV needs exact time,open,high,low,close,Volume header")
        for i, row in enumerate(reader, start=2):
            if len(out) >= MAX_ROWS:
                raise ValueError("source CSV exceeds supported row limit")
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"wrong field count in row {i}")
            try:
                time = parse_timestamp(row["time"], source_tz=source_timezone)
                if bar_time_is == "open":
                    time += timedelta(minutes=interval_minutes)
                if prev is not None and time <= prev:
                    raise ValueError("source time is duplicate or out of order")
                # Numeric precision and OHLC validation live in the target
                # data importer. Refuse strings outside a simple decimal grammar.
                for name in SOURCE_HEADER[1:]:
                    if not _NUMERIC.fullmatch(row[name].strip()):
                        raise ValueError(f"invalid {name} numeric format")
                out.append({
                    "timestamp": time.isoformat(),
                    "open": row["open"].strip(), "high": row["high"].strip(),
                    "low": row["low"].strip(), "close": row["close"].strip(),
                    "volume": row["Volume"].strip()
                })
                prev = time
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid source CSV row {i}: {exc}") from exc
    if not out:
        raise ValueError("no underlying candles")
    return out


def write_converted(path: str | Path, rows: list[dict[str, str]]) -> None:
    dst = Path(path)
    if dst.is_symlink() or dst.exists():
        raise ValueError("destination must not already exist or be symlink")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Create new with restrictive POSIX permissions; never overwrite input.
    import os
    fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Convert exported TradingView candles safely")
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--interval-minutes", type=int, choices=[1, 5], required=True)
    p.add_argument("--bar-time-is", choices=["open", "close"], required=True)
    p.add_argument("--source-timezone",
                   help="Required for naive wall-clock source times; IANA zone name")
    args = p.parse_args(argv)
    try:
        rows = convert_tradingview(
            args.source, interval_minutes=args.interval_minutes,
            bar_time_is=args.bar_time_is, source_timezone=args.source_timezone
        )
        write_converted(args.output, rows)
    except (ValueError, OSError) as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 2
    print(f"Converted {len(rows)} bars to {args.output}. "
          "Timestamps represent the data-available time. "
          "Market-data licensing is NOT independently verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
