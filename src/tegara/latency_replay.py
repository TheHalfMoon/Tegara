"""Conservative delayed-quote long-option replay for offline research only.

A trigger quote CANNOT also act as the hypothetical execution quote. Entry
and exit prices are read only after explicit bounded delays. No queue
priority, actual execution, OPRA provenance, or profitability is verified.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .core import LongOptionPosition, calculate_net_exit_pnl
from .quote_replay import ReplayOutcome, ReplayRules, ReplayTick


@dataclass(frozen=True)
class DelayedReplay:
    outcome: ReplayOutcome
    entry_decision_at: datetime
    entry_at: datetime | None = None
    trigger_at: datetime | None = None
    exit_at: datetime | None = None
    entry_premium: Decimal | None = None
    exit_premium: Decimal | None = None
    hypothetical_net_pnl: Decimal | None = None
    note: str = "DELAYED_SYNTHETIC_RESEARCH_ONLY"


def delayed_long_option(
    ticks: list[ReplayTick],
    rules: ReplayRules,
    *,
    entry_decision_at: datetime,
    entry_latency: timedelta,
    exit_latency: timedelta,
    allow_hypothetical: bool = False,
) -> DelayedReplay:
    """Model hypothetical *market* exits at first post-delay executable quote.

    If a target/stop trigger cannot be followed by promptly recorded
    eligible Bid evidence, abstain from monetary results. No time exit or
    counterfactual broker fill is silently assumed.
    """
    if not isinstance(entry_decision_at, datetime) or (
        entry_decision_at.tzinfo is None or entry_decision_at.utcoffset() is None
    ):
        raise ValueError("entry_decision_at must be timezone aware")
    for name, latency in (("entry_latency", entry_latency), ("exit_latency", exit_latency)):
        if not isinstance(latency, timedelta) or not timedelta(0) < latency <= timedelta(minutes=5):
            raise ValueError(f"{name} must be 1ns..5min positive timedelta")
    ny = ZoneInfo("America/New_York")
    if entry_decision_at.astimezone(ny).date() != rules.contract.expiration:
        raise ValueError("entry decision must be on NY expiration date")
    if rules.last_trading_at is None or allow_hypothetical is not True:
        return DelayedReplay(
            ReplayOutcome.UNQUALIFIED_DATA, entry_decision_at,
            note="Hypothetical replay disabled or last trading cutoff missing",
        )
    cutoff = min(rules.deadline, rules.last_trading_at)
    if entry_decision_at >= cutoff:
        return DelayedReplay(
            ReplayOutcome.NO_ENTRY_EVIDENCE, entry_decision_at,
            note="Entry decision is at or after explicit cutoff",
        )

    previous: datetime | None = None
    for tick in ticks:
        q = tick.quote
        if q.contract != rules.contract:
            raise ValueError("mixed option contract identities")
        if q.observed_at.astimezone(ny).date() != rules.contract.expiration:
            raise ValueError("quote date mismatches NY expiry session")
        if previous is not None and q.observed_at <= previous:
            raise ValueError("quote timestamps must strictly increase")
        if q.feed_type != "EXACT_NBBO":
            return DelayedReplay(
                ReplayOutcome.UNQUALIFIED_DATA, entry_decision_at,
                note="Indicative quote cannot be execution evidence",
            )
        previous = q.observed_at

    # The user's decision cannot retroactively buy at an earlier ask.
    start_at = entry_decision_at + entry_latency
    if start_at >= cutoff:
        return DelayedReplay(
            ReplayOutcome.NO_ENTRY_EVIDENCE, entry_decision_at,
            note="Entry latency extends beyond venue cutoff",
        )
    entry_idx = next(
        (i for i, tick in enumerate(ticks)
         if start_at <= tick.quote.observed_at < cutoff), None
    )
    if entry_idx is None:
        return DelayedReplay(
            ReplayOutcome.NO_ENTRY_EVIDENCE, entry_decision_at,
            note="No eligible post-latency Ask event",
        )
    entry_tick = ticks[entry_idx]
    if entry_tick.quote.observed_at - start_at > rules.max_quote_gap:
        return DelayedReplay(
            ReplayOutcome.NO_ENTRY_EVIDENCE, entry_decision_at,
            note="First eligible Ask event was too late",
        )
    if entry_tick.ask_size < rules.quantity or entry_tick.quote.ask <= 0:
        return DelayedReplay(
            ReplayOutcome.NO_ENTRY_LIQUIDITY, entry_decision_at,
            note="First post-latency Ask has insufficient displayed size",
        )
    entry_price = entry_tick.quote.ask + rules.entry_slippage_per_share
    position = LongOptionPosition(
        rules.contract, entry_price, rules.quantity,
        rules.entry_fee_per_contract, rules.exit_fee_per_contract,
    )
    entry_at = entry_tick.quote.observed_at
    previous_at = entry_at
    for i in range(entry_idx + 1, len(ticks)):
        tick = ticks[i]
        q = tick.quote
        if q.observed_at - previous_at > rules.max_quote_gap:
            return DelayedReplay(
                ReplayOutcome.NO_EXIT_EVIDENCE, entry_decision_at,
                entry_at=entry_at, entry_premium=entry_price,
                note="Unpriced exposure through a quote gap",
            )
        previous_at = q.observed_at
        if q.observed_at >= cutoff:
            break
        if q.bid_size < rules.quantity or q.bid <= rules.exit_slippage_per_share:
            continue
        mark_pnl = calculate_net_exit_pnl(
            position, q.bid - rules.exit_slippage_per_share
        )
        if mark_pnl >= rules.target_net_profit:
            reason = ReplayOutcome.TARGET_EXIT
        elif mark_pnl <= -rules.maximum_net_loss:
            reason = ReplayOutcome.STOP_EXIT
        else:
            continue

        # Trigger quote is only evidence to decide. Execution quote must be
        # LATER, after a nonzero delay; if absent, profit remains unknown.
        fill_not_before = q.observed_at + exit_latency
        if fill_not_before >= cutoff:
            return DelayedReplay(
                ReplayOutcome.NO_EXIT_EVIDENCE, entry_decision_at,
                entry_at=entry_at, trigger_at=q.observed_at,
                entry_premium=entry_price,
                note="Exit latency crosses deadline",
            )
        fill_idx = next(
            (j for j in range(i + 1, len(ticks))
             if fill_not_before <= ticks[j].quote.observed_at < cutoff), None
        )
        if fill_idx is None:
            return DelayedReplay(
                ReplayOutcome.NO_EXIT_EVIDENCE, entry_decision_at,
                entry_at=entry_at, trigger_at=q.observed_at,
                entry_premium=entry_price,
                note="No observed post-trigger Bid event",
            )
        fill = ticks[fill_idx].quote
        if (
            fill.observed_at - fill_not_before > rules.max_quote_gap
            or any(
                ticks[k].quote.observed_at - ticks[k - 1].quote.observed_at
                > rules.max_quote_gap
                for k in range(i + 1, fill_idx + 1)
            )
            or fill.bid_size < rules.quantity
            or fill.bid <= rules.exit_slippage_per_share
        ):
            return DelayedReplay(
                ReplayOutcome.NO_EXIT_EVIDENCE, entry_decision_at,
                entry_at=entry_at, trigger_at=q.observed_at,
                entry_premium=entry_price,
                note="Missing sufficiently fresh and sized Bid for delayed fill",
            )
        exit_price = fill.bid - rules.exit_slippage_per_share
        # The post-delay outcome may NOT meet the triggering profit/stop.
        realized_hypothetical = calculate_net_exit_pnl(position, exit_price)
        return DelayedReplay(
            reason, entry_decision_at,
            entry_at=entry_at, trigger_at=q.observed_at,
            exit_at=fill.observed_at, entry_premium=entry_price,
            exit_premium=exit_price,
            hypothetical_net_pnl=realized_hypothetical,
            note="Trigger type, NOT a guaranteed net result or actual fill",
        )
    return DelayedReplay(
        ReplayOutcome.NO_EXIT_EVIDENCE, entry_decision_at,
        entry_at=entry_at, entry_premium=entry_price,
        note="No target/stop trigger with independently eligible later fill",
    )
