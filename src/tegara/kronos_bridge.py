"""Opt-in Kronos forecasting bridge for *underlying prices only*.

No pretrained model, network, historical market data or broker credentials are
bundled. Predictions never become option quotes or execution decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def nonnegative_decimal(value: Decimal, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite nonnegative Decimal")
    if positive and value == 0:
        raise ValueError(f"{name} must be strictly positive")
    if value > Decimal("1e15"):
        raise ValueError(f"{name} exceeds practical bounded input")
    return value


@dataclass(frozen=True)
class MarketBar:
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if (not isinstance(self.observed_at, datetime) or
                self.observed_at.tzinfo is None or
                self.observed_at.utcoffset() is None):
            raise ValueError("bar timestamp must be timezone aware")
        for field in ("open", "high", "low", "close"):
            nonnegative_decimal(getattr(self, field), field, positive=True)
        nonnegative_decimal(self.volume, "volume")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC high/low inconsistent with open/close")


@dataclass(frozen=True)
class ForecastWindow:
    bars: tuple[MarketBar, ...]
    interval: timedelta
    last_trading_at: datetime
    prediction_times: tuple[datetime, ...]

    @property
    def lookback(self) -> int:
        return len(self.bars)


@dataclass(frozen=True)
class UnderlyingForecast:
    model_label: str
    predicted_closes: tuple[Decimal, ...]
    prediction_times: tuple[datetime, ...]
    baseline_closes: tuple[Decimal, ...]
    reference_close: Decimal
    status: str = "UNDERLYING_RESEARCH_ONLY"
    options_pnl_validated: bool = False
    trade_order_created: bool = False


def create_window(
    bars: Sequence[MarketBar], *,
    horizon_bars: int, interval: timedelta, last_trading_at: datetime,
) -> ForecastWindow:
    """Fail closed on lookahead, session cutoffs and missing intraday bars."""
    if len(bars) < 32 or len(bars) > 512:
        raise ValueError("context must contain 32..512 complete intraday bars")
    if type(horizon_bars) is not int or horizon_bars < 1 or horizon_bars > 60:
        raise ValueError("horizon_bars must be a positive integer no more than 60")
    if interval not in (timedelta(minutes=1), timedelta(minutes=5)):
        raise ValueError("interval must be 1 or 5 minutes")
    if (not isinstance(last_trading_at, datetime) or
            last_trading_at.tzinfo is None or
            last_trading_at.utcoffset() is None):
        raise ValueError("explicit session cutoff must be timezone aware")

    day = bars[0].observed_at.astimezone(NY).date()
    if last_trading_at.astimezone(NY).date() != day:
        raise ValueError("forecast cutoff must fall on same NY trading date")

    previous = None
    for bar in bars:
        if bar.observed_at.astimezone(NY).date() != day:
            raise ValueError("context crosses NY session date")
        if bar.observed_at >= last_trading_at:
            raise ValueError("context includes data after session cutoff")
        if previous is not None and bar.observed_at - previous != interval:
            raise ValueError("context has gap/duplicate/reordered timestamp")
        previous = bar.observed_at
    future = tuple(bars[-1].observed_at + interval * i
                   for i in range(1, horizon_bars + 1))
    if future[-1] >= last_trading_at:
        raise ValueError("forecast horizon reaches or exceeds session cutoff")
    return ForecastWindow(tuple(bars), interval, last_trading_at, future)


def forecast_underlying(
    predictor: Any,
    window: ForecastWindow,
    *,
    model_label: str = "Kronos-unverified",
    sample_count: int = 1,
) -> UnderlyingForecast:
    """Run an injected KronosPredictor-compatible object.

    pandas is an optional runtime dependency of the real predictor. If the
    upstream model, checkpoint or pandas is missing, fail without forecasts.
    """
    if not isinstance(model_label, str) or not model_label.strip():
        raise ValueError("model_label must not be empty")
    if type(sample_count) is not int or sample_count < 1 or sample_count > 16:
        raise ValueError("sample_count out of supported range")
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for Kronos forecast inference") from exc
    frame = pd.DataFrame(
        {
            "open": [float(b.open) for b in window.bars],
            "high": [float(b.high) for b in window.bars],
            "low": [float(b.low) for b in window.bars],
            "close": [float(b.close) for b in window.bars],
            "volume": [float(b.volume) for b in window.bars],
        }
    )
    x_ts = pd.Series([b.observed_at for b in window.bars])
    y_ts = pd.Series(list(window.prediction_times))
    output = predictor.predict(
        df=frame, x_timestamp=x_ts, y_timestamp=y_ts,
        pred_len=len(window.prediction_times),
        T=1.0, top_p=0.9, sample_count=sample_count,
    )
    if "close" not in output.columns or len(output) != len(y_ts):
        raise ValueError("Kronos prediction has missing close or incorrect horizon")
    if list(output.index) != list(y_ts):
        raise ValueError("Kronos returned mismatched forecast timestamps")
    predicted: list[Decimal] = []
    for value in output["close"]:
        # Convert via repr/str (not Decimal(float)); nonfinite and <=0 fail.
        number = Decimal(str(value))
        nonnegative_decimal(number, "predicted close", positive=True)
        predicted.append(number)
    baseline = tuple(window.bars[-1].close for _ in window.prediction_times)
    return UnderlyingForecast(model_label, tuple(predicted),
                              window.prediction_times, baseline,
                              window.bars[-1].close)


@dataclass(frozen=True)
class ForecastScore:
    """Ex-post price forecast diagnostics; NOT option profitability."""

    model_mae: Decimal
    persistence_mae: Decimal
    terminal_direction_correct: bool
    sample_size: int
    note: str = "Post-event underlying-price scoring; no option fills or trading alpha"


def score_forecast(
    forecast: UnderlyingForecast,
    actual_closes: dict[datetime, Decimal],
) -> ForecastScore:
    """Compare locked prior forecast to truly later underlying closes.

    This must be invoked only AFTER the future observed bars are complete.
    """
    expected = set(forecast.prediction_times)
    if set(actual_closes) != expected or len(actual_closes) != len(expected):
        raise ValueError("actual outcome timestamps must match forecast horizon exactly")
    actual = []
    for ts in forecast.prediction_times:
        actual.append(nonnegative_decimal(actual_closes[ts], "actual close", positive=True))
    n = Decimal(len(actual))
    model_mae = sum(
        (abs(p - y) for p, y in zip(forecast.predicted_closes, actual)), Decimal(0)
    ) / n
    baseline_mae = sum(
        (abs(p - y) for p, y in zip(forecast.baseline_closes, actual)), Decimal(0)
    ) / n
    forecast_move = forecast.predicted_closes[-1] - forecast.reference_close
    actual_move = actual[-1] - forecast.reference_close
    direction_correct = (
        (forecast_move > 0 and actual_move > 0) or
        (forecast_move < 0 and actual_move < 0) or
        (forecast_move == 0 and actual_move == 0)
    )
    return ForecastScore(model_mae, baseline_mae, direction_correct, len(actual))
