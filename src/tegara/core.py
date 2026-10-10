"""Deterministic calculations and qualified exit-alert assessment.

No market connectivity, forecast, broker order submission or push delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo


def _money(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be a finite, nonnegative Decimal")
    return value


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    right: str  # C or P
    strike: Decimal
    expiration: date
    multiplier: int = 100

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper() or not self.symbol.replace(".", "").isalnum():
            raise ValueError("symbol must be a canonical uppercase ticker")
        if self.right not in ("C", "P"):
            raise ValueError("right must be C or P")
        _money(self.strike, "strike")
        if self.strike <= 0:
            raise ValueError("strike must be positive")
        if not isinstance(self.expiration, date) or isinstance(self.expiration, datetime):
            raise ValueError("expiration must be a date")
        if type(self.multiplier) is not int or self.multiplier <= 0:
            raise ValueError("multiplier must be a positive integer")


@dataclass(frozen=True)
class LongOptionPosition:
    contract: OptionContract
    entry_price: Decimal
    quantity: int
    entry_fee_per_contract: Decimal = Decimal("0")
    exit_fee_per_contract: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _money(self.entry_price, "entry_price")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        _money(self.entry_fee_per_contract, "entry_fee_per_contract")
        _money(self.exit_fee_per_contract, "exit_fee_per_contract")

    @property
    def initial_debit(self) -> Decimal:
        return (
            self.entry_price * self.contract.multiplier * self.quantity
            + self.entry_fee_per_contract * self.quantity
        )


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: Decimal
    ask: Decimal
    bid_size: int
    observed_at: datetime
    source: str
    feed_type: str  # EXACT_NBBO or other, unqualified data rejected

    def __post_init__(self) -> None:
        _money(self.bid, "bid")
        _money(self.ask, "ask")
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        if type(self.bid_size) is not int or self.bid_size < 0:
            raise ValueError("bid_size must be a nonnegative integer")
        if not self.source:
            raise ValueError("source must be set")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone aware")


def calculate_net_exit_pnl(position: LongOptionPosition, bid: Decimal) -> Decimal:
    """Conservative *estimate*, not a guaranteed executable fill."""
    _money(bid, "bid")
    return (
        (bid - position.entry_price) * position.contract.multiplier * position.quantity
        - position.quantity * (position.entry_fee_per_contract + position.exit_fee_per_contract)
    )


def minimum_bid_for_net_profit(position: LongOptionPosition, target_net_profit: Decimal) -> Decimal:
    """Exact algebraic threshold; downstream adapters must apply real tick rules."""
    _money(target_net_profit, "target_net_profit")
    fees = position.quantity * (position.entry_fee_per_contract + position.exit_fee_per_contract)
    return position.entry_price + (target_net_profit + fees) / (
        position.contract.multiplier * position.quantity
    )


def minimum_bid_for_net_return_percent(position: LongOptionPosition, return_percent: Decimal) -> Decimal:
    """Return measured against entry cash outlay including buy-side fees."""
    _money(return_percent, "return_percent")
    net_goal = position.initial_debit * return_percent / Decimal("100")
    return minimum_bid_for_net_profit(position, net_goal)


class AlertStatus(str, Enum):
    REVIEW_SELL_TO_CLOSE = "REVIEW_SELL_TO_CLOSE"
    TARGET_NOT_REACHED = "TARGET_NOT_REACHED"
    SUPPRESSED_CONTRACT_MISMATCH = "SUPPRESSED_CONTRACT_MISMATCH"
    SUPPRESSED_UNQUALIFIED_FEED = "SUPPRESSED_UNQUALIFIED_FEED"
    SUPPRESSED_STALE_QUOTE = "SUPPRESSED_STALE_QUOTE"
    SUPPRESSED_INSUFFICIENT_SIZE = "SUPPRESSED_INSUFFICIENT_SIZE"
    SUPPRESSED_EXPIRED = "SUPPRESSED_EXPIRED"
    SUPPRESSED_CUTOFF_UNVERIFIED = "SUPPRESSED_CUTOFF_UNVERIFIED"
    SUPPRESSED_AFTER_CUTOFF = "SUPPRESSED_AFTER_CUTOFF"
    SUPPRESSED_WRONG_TRADING_DATE = "SUPPRESSED_WRONG_TRADING_DATE"


@dataclass(frozen=True)
class AlertAssessment:
    status: AlertStatus
    bid_target: Decimal
    estimated_net_pnl: Decimal | None = None


def assess_profit_target(
    position: LongOptionPosition,
    quote: OptionQuote,
    now: datetime,
    max_quote_age: timedelta,
    target_net_profit: Decimal,
    last_trading_at: datetime | None = None,
) -> AlertAssessment:
    """Only classify a validated quote; never submit a trade or send a push."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    if not isinstance(max_quote_age, timedelta) or max_quote_age <= timedelta(0):
        raise ValueError("max_quote_age must be positive")
    target = minimum_bid_for_net_profit(position, target_net_profit)
    if quote.contract != position.contract:
        return AlertAssessment(AlertStatus.SUPPRESSED_CONTRACT_MISMATCH, target)
    ny = ZoneInfo("America/New_York")
    if (now.astimezone(ny).date() > position.contract.expiration
            or quote.observed_at.astimezone(ny).date() > position.contract.expiration):
        return AlertAssessment(AlertStatus.SUPPRESSED_EXPIRED, target)
    if last_trading_at is None:
        return AlertAssessment(AlertStatus.SUPPRESSED_CUTOFF_UNVERIFIED, target)
    if (not isinstance(last_trading_at, datetime) or
            last_trading_at.tzinfo is None or
            last_trading_at.utcoffset() is None or
            last_trading_at.astimezone(ny).date() != position.contract.expiration):
        raise ValueError("last_trading_at must be timezone-aware and on NY expiration date")
    if (now >= last_trading_at or quote.observed_at >= last_trading_at):
        return AlertAssessment(AlertStatus.SUPPRESSED_AFTER_CUTOFF, target)
    if quote.observed_at.astimezone(ny).date() != position.contract.expiration:
        return AlertAssessment(AlertStatus.SUPPRESSED_WRONG_TRADING_DATE, target)
    if quote.feed_type != "EXACT_NBBO":
        return AlertAssessment(AlertStatus.SUPPRESSED_UNQUALIFIED_FEED, target)
    age = now - quote.observed_at
    if age < timedelta(0) or age > max_quote_age:
        return AlertAssessment(AlertStatus.SUPPRESSED_STALE_QUOTE, target)
    if quote.bid_size < position.quantity or quote.bid == 0:
        return AlertAssessment(AlertStatus.SUPPRESSED_INSUFFICIENT_SIZE, target)
    pnl = calculate_net_exit_pnl(position, quote.bid)
    return AlertAssessment(
        AlertStatus.REVIEW_SELL_TO_CLOSE if pnl >= target_net_profit else AlertStatus.TARGET_NOT_REACHED,
        target,
        pnl,
    )
