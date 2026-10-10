"""Offline underlying OHLCV walk-forward research with provenance-preserving reports.

This is NOT an options strategy backtest. The model is invoked only with past
bars, and outcomes are scored after the future observations are available.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import os
from pathlib import Path
import stat
from typing import Any
from zoneinfo import ZoneInfo
import csv
import json

from .kronos_bridge import (
    MarketBar, create_window, forecast_underlying, score_forecast,
)

NY = ZoneInfo("America/New_York")
HEADER = ("timestamp", "open", "high", "low", "close", "volume")
MAX_BYTES = 32 * 1024 * 1024
MAX_BARS = 60000


@dataclass(frozen=True)
class ResearchParameters:
    interval_minutes: int
    lookback_bars: int
    horizon_bars: int
    stride_bars: int
    holdout_sessions: int
    max_windows: int = 120

    def __post_init__(self) -> None:
        if self.interval_minutes not in (1, 5):
            raise ValueError("only 1m/5m underlying research is supported")
        for name in ("lookback_bars", "horizon_bars", "stride_bars",
                     "holdout_sessions", "max_windows"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be positive integer")
        if not 32 <= self.lookback_bars <= 512:
            raise ValueError("lookback_bars must be 32..512")
        if self.horizon_bars > 60:
            raise ValueError("horizon_bars must be <=60")
        if self.max_windows > 1000:
            raise ValueError("max_windows cannot exceed 1000")


def _load_strict_csv(path: str | Path, header: tuple[str, ...]) -> tuple[list[dict], str]:
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError("CSV must be an existing non-symlink file <=32 MiB")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("no-follow CSV opening unavailable")
    # Read one bounded descriptor snapshot; parse and hash IDENTICAL bytes.
    # Never hash by pathname and then reopen a different file for parsing.
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW
                 | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("CSV must be a regular file")
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("CSV exceeds 32 MiB")
    digest = sha256(raw).hexdigest()
    with TextIOWrapper(BytesIO(raw), encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, strict=True)
        if tuple(reader.fieldnames or ()) != header:
            raise ValueError(f"CSV must have exact header: {','.join(header)}")
        rows = []
        for row in reader:
            if len(rows) >= MAX_BARS:
                raise ValueError("CSV exceeds maximum bar count")
            if (None in row or any(v is None or "\x00" in v for v in row.values())):
                raise ValueError("CSV has extra, missing or NUL fields")
            rows.append(row)
    if not rows:
        raise ValueError("CSV is empty")
    return rows, digest


def load_bars(path: str | Path) -> tuple[list[MarketBar], str]:
    rows, digest = _load_strict_csv(path, HEADER)
    bars: list[MarketBar] = []
    for line, row in enumerate(rows, start=2):
        try:
            timestamp = datetime.fromisoformat(row["timestamp"])
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("timestamp must include UTC offset")
            if not row["timestamp"].strip():
                raise ValueError("timestamp missing")
            numeric = {}
            for field in HEADER[1:]:
                if not row[field] or len(row[field]) > 64:
                    raise ValueError("numeric field missing/too long")
                numeric[field] = Decimal(row[field])
            bar = MarketBar(timestamp, **numeric)
            if bars and timestamp <= bars[-1].observed_at:
                raise ValueError("timestamps must be increasing, no repeats")
            bars.append(bar)
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise ValueError(f"invalid OHLCV at CSV line {line}: {exc}") from exc
    return bars, digest


def load_cutoffs(path: str | Path) -> tuple[dict[date, datetime], str]:
    rows, digest = _load_strict_csv(path, ("session_date", "last_trading_at"))
    cutoffs: dict[date, datetime] = {}
    for line, row in enumerate(rows, start=2):
        try:
            session_day = date.fromisoformat(row["session_date"])
            cutoff = datetime.fromisoformat(row["last_trading_at"])
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError("cutoff must be timezone aware")
            if cutoff.astimezone(NY).date() != session_day:
                raise ValueError("cutoff is not on its New York session date")
            if session_day in cutoffs:
                raise ValueError("duplicate session cutoff")
            cutoffs[session_day] = cutoff
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid cutoff at line {line}: {exc}") from exc
    return cutoffs, digest


def walk_forward(
    predictor: Any,
    bars: list[MarketBar],
    cutoffs: dict[date, datetime],
    params: ResearchParameters,
    *,
    model_label: str = "Kronos-research-unverified",
) -> dict:
    """Compare locked forecasts to persistence on the last N session dates.

    Evaluation dates are reserved by chronology, not shuffled. No training
    occurs in this module and no result is called out-of-sample *training*
    performance; prior model pretraining contamination remains unknown.
    """
    groups: dict[date, list[MarketBar]] = {}
    prev = None
    for b in bars:
        if prev is not None and b.observed_at <= prev:
            raise ValueError("bar timestamps must be strictly increasing")
        prev = b.observed_at
        day = b.observed_at.astimezone(NY).date()
        groups.setdefault(day, []).append(b)
    dates = sorted(groups)
    if len(dates) <= params.holdout_sessions:
        raise ValueError("need at least one earlier session before held-out dates")
    if params.max_windows < params.holdout_sessions:
        raise ValueError("max_windows must cover every reserved holdout session")
    if set(cutoffs) != set(dates):
        raise ValueError("cutoff file must cover exactly the dataset's NY dates")
    for day, series in groups.items():
        cutoff = cutoffs[day]
        interval = timedelta(minutes=params.interval_minutes)
        previous = None
        for bar in series:
            if bar.observed_at >= cutoff:
                raise ValueError(f"bar for {day} is at/after supplied session cutoff")
            if previous is not None and bar.observed_at - previous != interval:
                raise ValueError(f"missing/duplicate/out-of-order intraday bar on {day}")
            previous = bar.observed_at

    selected = dates[-params.holdout_sessions:]
    scores: list[dict[str, str | int | bool]] = []
    for day_index, day in enumerate(selected):
        series = groups[day]
        begin = params.lookback_bars
        stop = len(series) - params.horizon_bars + 1
        candidates = list(range(begin, stop, params.stride_bars))
        # Distribute global evaluation budget across held-out dates, so
        # a limit cannot silently evaluate only the earliest holdout day.
        quota = (params.max_windows // len(selected)
                 + (1 if day_index < params.max_windows % len(selected) else 0))
        if not candidates:
            raise ValueError(f"held-out session {day} has no contiguous windows")
        if len(candidates) > quota:
            # Deterministic evenly-spaced subsampling, no target labels used.
            if quota == 1:
                candidates = [candidates[len(candidates) // 2]]
            else:
                candidates = [candidates[i * (len(candidates)-1) // (quota-1)]
                              for i in range(quota)]
        for end_idx in candidates:
            prior = series[end_idx - params.lookback_bars:end_idx]
            future = series[end_idx:end_idx + params.horizon_bars]
            window = create_window(
                prior, horizon_bars=params.horizon_bars,
                interval=timedelta(minutes=params.interval_minutes),
                last_trading_at=cutoffs[day],
            )
            actual = {bar.observed_at: bar.close for bar in future}
            if set(actual) != set(window.prediction_times):
                raise ValueError("future observations do not align with forecast")
            forecast = forecast_underlying(predictor, window, model_label=model_label)
            scored = score_forecast(forecast, actual)
            scores.append({
                "session": day.isoformat(),
                "as_of": prior[-1].observed_at.isoformat(),
                "horizon_end": future[-1].observed_at.isoformat(),
                "model_mae": str(scored.model_mae),
                "persistence_mae": str(scored.persistence_mae),
                "direction_correct": scored.terminal_direction_correct,
            })
    if not scores:
        raise ValueError("no evaluation windows; dataset lacks enough contiguous bars")
    count = Decimal(len(scores))
    model_avg = sum((Decimal(s["model_mae"]) for s in scores), Decimal(0)) / count
    base_avg = sum((Decimal(s["persistence_mae"]) for s in scores), Decimal(0)) / count
    wins = sum(1 for s in scores if s["direction_correct"])
    return {
        "status": "UNDERLYING_RESEARCH_ONLY",
        "model_label": model_label,
        "test_sessions": [d.isoformat() for d in selected],
        "window_count": len(scores),
        "windows_by_session": {
            d.isoformat(): sum(s["session"] == d.isoformat() for s in scores)
            for d in selected
        },
        "model_mae": str(model_avg),
        "persistence_mae": str(base_avg),
        "terminal_direction_correct_fraction": str(Decimal(wins) / count),
        "windows": scores,
        "options_pnl_validated": False,
        "trade_execution_validated": False,
        "market_data_rights_independently_verified": False,
        "cutoff_metadata_independently_verified": False,
        "warning": ("Cannot infer options trading alpha from underlying price MAE. "
                    "This is not a live quote or forward trading study."),
    }


def report_json(result: dict) -> str:
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True)
