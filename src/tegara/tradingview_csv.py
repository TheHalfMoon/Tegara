"""Convert user-exported TradingView-like OHLCV to Tegara time-available bars.

No web requests. Conversion never confers a market-data retention license.
An OPEN-timestamped bar is shifted to its *completion* time to avoid
lookahead using the close/high/low before they were observable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ._csv_snapshot import read_csv_snapshot
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import csv
from io import BytesIO, TextIOWrapper
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
    # A bounded source snapshot must refuse parent as well as leaf symlink
    # substitution before any candle parsing or timestamp transformation.
    raw = read_csv_snapshot(path, MAX_SOURCE)
    out = []
    prev: datetime | None = None
    with TextIOWrapper(BytesIO(raw), encoding="utf-8-sig", newline="") as f:
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
    """Publish complete CSV without following symlinked output directories."""
    import os
    import secrets

    dst = Path(path).expanduser()
    if ".." in dst.parts or not dst.name:
        raise ValueError("destination cannot traverse parent components")
    dst = dst.absolute()
    if dst.is_symlink() or dst.exists():
        raise ValueError("destination must not already exist or be symlink")

    # Only canonicalize macOS's verified built-in aliases /var and /tmp.
    if len(dst.parts) > 1 and dst.parts[1] in ("var", "tmp"):
        alias = Path("/") / dst.parts[1]
        canonical = Path("/private") / dst.parts[1]
        if alias.is_symlink() and alias.resolve() == canonical:
            dst = canonical.joinpath(*dst.parts[2:])

    if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
        raise ValueError("atomic no-follow output directory opening unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open("/", directory_flags)
    try:
        for component in dst.parts[1:-1]:
            try:
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            except FileNotFoundError:
                # Preserve the historical ability to create absent parents;
                # each new directory is opened with O_NOFOLLOW before use.
                os.mkdir(component, mode=0o700, dir_fd=parent_fd)
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd

        stage_name = f".{dst.name}.{secrets.token_hex(16)}.partial"
        file_fd = os.open(
            stage_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=parent_fd,
        )
        try:
            with os.fdopen(file_fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TARGET_HEADER)
                writer.writeheader()
                writer.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
            # Both names are resolved against the *same pinned directory*.
            # The hard link publishes atomically without overwriting anyone.
            os.link(stage_name, dst.name, src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.unlink(stage_name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


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
