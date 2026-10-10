"""Bounded deterministic latency sensitivity across one immutable quote dataset.

Never optimize/select best historical latency for use as a trading signal.
Even fully observed synthetic grid P&L is NOT broker fill or OPRA proof.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import re

from .latency_replay import delayed_long_option
from .quote_replay import ReplayRules, ReplayTick

MAX_AXIS = 4
MAX_SCENARIOS = 16
_GRID = re.compile(r"[1-9][0-9]*(?:,[1-9][0-9]*)*")


def parse_latency_axis(raw: str, *, name: str) -> tuple[int, ...]:
    """Accept strict comma-separated positive whole-second delays, never ranges."""
    if not isinstance(raw, str) or len(raw) > 64 or not _GRID.fullmatch(raw):
        raise ValueError(f"{name} must be strict comma-separated positive integers")
    vals = [int(x) for x in raw.split(",")]
    if any(v > 300 for v in vals):
        raise ValueError(f"{name} requires 1..300 seconds")
    if len(vals) > MAX_AXIS or len(vals) != len(set(vals)):
        raise ValueError(f"{name} requires at most four unique latency values")
    return tuple(sorted(vals))


def latency_sensitivity(
    ticks: list[ReplayTick],
    rules: ReplayRules,
    *,
    entry_decision_at: datetime,
    entry_delays: tuple[int, ...],
    exit_delays: tuple[int, ...],
    allow_hypothetical: bool,
) -> dict:
    """Evaluate fixed delay grid with no selection/tuning and full abstention.

    Results are ordered by latency values, regardless of caller ordering.
    The P&L extrema deliberately remain null if ANY case is missing evidence;
    a partial grid is not proof of an optimistic lower bound on risk.
    """
    if (not entry_delays or not exit_delays or
            len(entry_delays) > MAX_AXIS or len(exit_delays) > MAX_AXIS or
            len(entry_delays) * len(exit_delays) > MAX_SCENARIOS):
        raise ValueError("latency grid must contain 1..16 bounded scenarios")
    for name, axis in (("entry_delays", entry_delays),
                       ("exit_delays", exit_delays)):
        if (any(type(v) is not int or v < 1 or v > 300 for v in axis)
                or len(set(axis)) != len(axis)):
            raise ValueError(f"{name} must contain distinct integer 1..300 seconds")

    rows: list[dict] = []
    values: list[Decimal] = []
    trigger_losses = 0
    for entry_delay in sorted(entry_delays):
        for exit_delay in sorted(exit_delays):
            # For non-opted-in studies avoid running a nominally permissive
            # model; every returned case is explicitly UNQUALIFIED_DATA.
            result = delayed_long_option(
                ticks, rules, entry_decision_at=entry_decision_at,
                entry_latency=timedelta(seconds=entry_delay),
                exit_latency=timedelta(seconds=exit_delay),
                allow_hypothetical=allow_hypothetical,
            )
            money = result.hypothetical_net_pnl
            if money is not None:
                values.append(money)
                if result.outcome.value == "TARGET_EXIT" and money < 0:
                    trigger_losses += 1
            rows.append({
                "entry_delay_seconds": entry_delay,
                "exit_delay_seconds": exit_delay,
                "outcome": result.outcome.value,
                "entry_at": result.entry_at.isoformat() if result.entry_at else None,
                "decision_trigger_at": (
                    result.trigger_at.isoformat() if result.trigger_at else None
                ),
                "exit_at": result.exit_at.isoformat() if result.exit_at else None,
                "hypothetical_net_pnl_usd": str(money) if money is not None else None,
                "note": result.note,
            })
    complete = len(values) == len(rows)
    return {
        "mode": "FIXED_LATENCY_STRESS_GRID",
        "scenario_count": len(rows),
        "scenarios_with_priced_hypothetical_exit": len(values),
        "scenarios_without_exit_pnl": len(rows) - len(values),
        "all_scenarios_priced": complete,
        "target_trigger_then_loss_count": trigger_losses,
        "min_hypothetical_pnl_only_if_complete_usd": (
            str(min(values)) if complete else None
        ),
        "max_hypothetical_pnl_only_if_complete_usd": (
            str(max(values)) if complete else None
        ),
        "scenario_results": rows,
        "selected_optimal_latency": None,
        "source_provenance_independently_verified": False,
        "actual_fill_reconciliation_verified": False,
        "warning": (
            "Fixed research stress grid; missing quotes mean unknown outcomes, "
            "NOT zero PnL. No best-latency selection, real fills, exchange "
            "quote license or forward out-of-sample evidence."
        ),
    }
