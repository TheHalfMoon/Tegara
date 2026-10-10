"""Offline quote replay for long options; intentionally NOT a live-trading backtest.

Inputs are already obtained/licensed by the caller. Nothing here verifies
OPRA provenance, marketability, order queue, or that a fill actually occurred.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from .core import (
    LongOptionPosition, OptionContract, OptionQuote, calculate_net_exit_pnl
)


class ReplayOutcome(str, Enum):
    UNQUALIFIED_DATA = "UNQUALIFIED_DATA"
    NO_ENTRY_LIQUIDITY = "NO_ENTRY_LIQUIDITY"
    NO_EXIT_EVIDENCE = "NO_EXIT_EVIDENCE"
    TARGET_EXIT = "TARGET_EXIT"
    STOP_EXIT = "STOP_EXIT"
    TIME_EXIT = "TIME_EXIT"


@dataclass(frozen=True)
class ReplayTick:
    quote: OptionQuote
    ask_size: int

    def __post_init__(self) -> None:
        if type(self.ask_size) is not int or self.ask_size < 0:
            raise ValueError("ask_size must be a nonnegative integer")


@dataclass(frozen=True)
class ReplayRules:
    contract: OptionContract
    quantity: int
    entry_fee_per_contract: Decimal
    exit_fee_per_contract: Decimal
    entry_slippage_per_share: Decimal
    exit_slippage_per_share: Decimal
    target_net_profit: Decimal
    maximum_net_loss: Decimal
    deadline: datetime
    max_quote_gap: timedelta
    last_trading_at: datetime | None = None  # externally supplied; never inferred

    def __post_init__(self) -> None:
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        for name in (
            "entry_fee_per_contract", "exit_fee_per_contract",
            "entry_slippage_per_share", "exit_slippage_per_share",
            "target_net_profit", "maximum_net_loss",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be nonnegative finite Decimal")
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            raise ValueError("deadline must be timezone aware")
        if self.max_quote_gap <= timedelta(0):
            raise ValueError("max_quote_gap must be positive")
        if self.last_trading_at is not None:
            if (self.last_trading_at.tzinfo is None or
                    self.last_trading_at.utcoffset() is None):
                raise ValueError("last_trading_at must be timezone aware")
            if self.last_trading_at.astimezone(
                ZoneInfo("America/New_York")
            ).date() != self.contract.expiration:
                raise ValueError("last trading cutoff must match NY expiry date")


@dataclass(frozen=True)
class ReplayResult:
    outcome: ReplayOutcome
    entry_at: datetime | None = None
    exit_at: datetime | None = None
    entry_premium: Decimal | None = None
    exit_premium: Decimal | None = None
    simulated_net_pnl: Decimal | None = None
    note: str = "Simulated bid/ask fills only; no execution or verified market data"


def replay_long_option(
    ticks: list[ReplayTick],
    rules: ReplayRules,
    *,
    provenance_approved: bool = False,
) -> ReplayResult:
    """Replay a *single contract* from a user-supplied dataset.

    No financial performance results are returned unless the caller explicitly
    asserts data provenance approval; that assertion is not independently
    verified here. The result always remains a hypothetical simulation.
    """
    if provenance_approved is not True or rules.last_trading_at is None:
        return ReplayResult(
            ReplayOutcome.UNQUALIFIED_DATA,
            note="Licensed source and contract-specific last trading cutoff are unverified",
        )
    effective_deadline = min(rules.deadline, rules.last_trading_at)
    if not ticks:
        return ReplayResult(ReplayOutcome.NO_ENTRY_LIQUIDITY)
    previous: datetime | None = None
    for tick in ticks:
        quote = tick.quote
        if quote.contract != rules.contract:
            raise ValueError("mixed contract identities in replay")
        if quote.observed_at.astimezone(
            ZoneInfo("America/New_York")
        ).date() != rules.contract.expiration:
            raise ValueError("replay quote must belong to NY expiration date")
        if quote.feed_type != "EXACT_NBBO":
            return ReplayResult(ReplayOutcome.UNQUALIFIED_DATA)
        if previous is not None and quote.observed_at <= previous:
            raise ValueError("timestamps must be strictly increasing")
        previous = quote.observed_at

    first = ticks[0]
    if first.quote.observed_at >= effective_deadline:
        return ReplayResult(ReplayOutcome.NO_ENTRY_LIQUIDITY)
    if first.ask_size < rules.quantity or first.quote.ask <= 0:
        return ReplayResult(ReplayOutcome.NO_ENTRY_LIQUIDITY)
    entry_price = first.quote.ask + rules.entry_slippage_per_share
    position = LongOptionPosition(
        rules.contract, entry_price, rules.quantity,
        rules.entry_fee_per_contract, rules.exit_fee_per_contract
    )

    previous_at = first.quote.observed_at
    last_eligible_exit: ReplayTick | None = None
    for tick in ticks[1:]:
        q = tick.quote
        if q.observed_at - previous_at > rules.max_quote_gap:
            return ReplayResult(
                ReplayOutcome.NO_EXIT_EVIDENCE, first.quote.observed_at,
                entry_premium=entry_price,
                note="Unpriced exposure across a quote-data gap; no profit claim"
            )
        previous_at = q.observed_at
        if q.observed_at > effective_deadline:
            break
        if q.bid_size < rules.quantity or q.bid <= rules.exit_slippage_per_share:
            continue
        exit_price = q.bid - rules.exit_slippage_per_share
        pnl = calculate_net_exit_pnl(position, exit_price)
        last_eligible_exit = tick
        if pnl >= rules.target_net_profit:
            return ReplayResult(ReplayOutcome.TARGET_EXIT, first.quote.observed_at,
                                q.observed_at, entry_price, exit_price, pnl)
        if pnl <= -rules.maximum_net_loss:
            return ReplayResult(ReplayOutcome.STOP_EXIT, first.quote.observed_at,
                                q.observed_at, entry_price, exit_price, pnl)

    if last_eligible_exit is not None:
        last = last_eligible_exit.quote
        if effective_deadline - last.observed_at <= rules.max_quote_gap:
            exit_price = last.bid - rules.exit_slippage_per_share
            pnl = calculate_net_exit_pnl(position, exit_price)
            return ReplayResult(ReplayOutcome.TIME_EXIT, first.quote.observed_at,
                                last.observed_at, entry_price, exit_price, pnl)
    return ReplayResult(
        ReplayOutcome.NO_EXIT_EVIDENCE, first.quote.observed_at,
        entry_premium=entry_price, note="No timely eligible Bid near deadline"
    )
