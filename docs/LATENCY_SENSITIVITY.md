# LT-P02 — Fixed Latency Sensitivity Grid

Status: OFFLINE SYNTHETIC RESEARCH ONLY. No OPRA rights, Derayah trade,
phone receipt, or actual fill can be inferred from these outputs.

## Why this exists

A single historical assumed order latency may accidentally look profitable.
Tegara can now compare a small fixed set of entry and exit delays against
the same immutable input CSV snapshot. It does not search for an optimal
latency, trade rule, target, or model, and it never treats one quote event as a
verified execution.

## CLI example

    PYTHONPATH=src python3 -m tegara.replay_cli \
      --csv /private/one_contract_quotes.csv \
      --symbol SPY --right C --strike 600 --expiration 2026-10-09 \
      --quantity 1 --entry-fee 2.29 --exit-fee 2.29 \
      --entry-slippage 0.01 --exit-slippage 0.02 \
      --target-net-profit 35 --max-net-loss 50 \
      --entry-not-before 2026-10-09T14:30:00+00:00 \
      --deadline 2026-10-09T14:40:00+00:00 \
      --last-trading-at 2026-10-09T20:00:00+00:00 \
      --max-gap-seconds 30 \
      --entry-latency-grid 1,5,30 \
      --exit-latency-grid 1,5,30 \
      --allow-hypothetical-pnl

This evaluates nine deterministic combinations (three entry delays times
three exit delays). Dates and fees are illustrative, NOT independently
validated session rules or actual Derayah charges.

For an input audit with no hypothetical monetary results, omit the
--allow-hypothetical-pnl flag.

## Safety and interpretation

- Grid mode requires both axes, strictly comma-separated unique whole
  seconds in [1, 300]; maximum four values per axis, 16 total scenarios.
  Axes are sorted deterministically regardless of user order.
- Grid mode refuses simultaneous single-scenario --entry-latency-seconds
  or --exit-latency-seconds inputs instead of silently ignoring them.
  Only the applied grid axes appear in the resulting parameters.
- Grid mode only works with delayed execution, the CLI default.
  instant-reference cannot be silently mixed into a latency grid.
- The full CSV is ingested once with exact-bytes SHA-256 before examining
  delays; each scenario uses the same quote observations, option contract,
  fee assumptions, target, stop, deadlines, and data qualification.
- LATENCY_SENSITIVITY_ONLY is not a trade result. Top-level
  hypothetical_net_pnl_usd is always null in grid mode.
- Individual P&L exists only if a post-entry/post-trigger priced quote is
  available, sized, timely, and explicitly approved for hypothetical replay.
- Missing scenarios are NEVER scored as zero P&L, profit, or an assumed
  closure. scenarios_without_exit_pnl tracks missing observations.
- Aggregate min/max are null whenever any scenario is unpriced, avoiding
  false assurance about unobserved parts of the outcome distribution.
- Even if all scenarios have computed outcomes, min/max are only over the
  specified synthetic latency values, not a distributional bound on actual
  fill price or worst-case loss.
- selected_optimal_latency is always null. This tool does not rank or
  automatically select historical settings, avoiding test-data optimization.
- The EXACT_NBBO label in CSV cannot authenticate OPRA provenance;
  independent source, broker fill, market-data licensing and trading alpha
  qualifications remain false.

## What remains necessary

Independent exact-head Alibaba OpenCodeReview and genuine licensed,
timestamp-available options quote history; actual exchange cutoffs, Derayah
Global fee/filled trade confirmations, measured notification latency,
prospective paper trading over locked sessions, and a reviewed model vs
baseline study remain open.

PStack, Graft and Jev tools can strengthen deterministic review but cannot
certify OPRA rights, actual fills or alpha. No automatic options orders,
vendor purchases or phone pushes are created.
