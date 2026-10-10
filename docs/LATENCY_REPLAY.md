# LT-P01 — Delayed Option Execution Research

Status: SYNTHETIC OFFLINE RESEARCH ONLY.

This stage addresses a major problem in 0DTE options simulation: an alert
trigger at quoted Bid Q(T) is NOT evidence that an order executes at Q(T).
Prices can move during decision latency, transmission and quote updates.

## Two modes

The CLI now DEFAULTS to delayed mode for any explicitly requested
hypothetical PnL. The old optimistic instant-reference mode is available
ONLY with --execution-mode instant-reference, as an explicitly selected
counterfactual with same-quote hypothetical exit decisions. That
optimistic reference is NOT proof of executable prices.

Delayed mode takes an explicit entry decision time, positive entry/exit
delays, and only uses later option quotes:

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
      --execution-mode delayed \
      --entry-latency-seconds 1 \
      --exit-latency-seconds 2 \
      --allow-hypothetical-pnl

Dates, prices and cutoff are ILLUSTRATIVE. Actual contract last-trading
time and applicable commissions are NOT independently verified.

Without --allow-hypothetical-pnl both models return UNQUALIFIED_DATA
and null PnL.

## Delayed model

- Entry fill candidate = first eligible Ask observed at/after
  entry-decision time PLUS entry latency.
- If earliest eligible Ask is late, at/after cutoff, or lacks size,
  return NO_ENTRY_EVIDENCE or NO_ENTRY_LIQUIDITY.
- After entry, a later Bid triggers target or stop threshold.
- The trigger quote CANNOT be the exit price. A further Bid must be
  observed at/after trigger time PLUS exit latency.
- The next post-latency quote needs sufficient displayed Bid size,
  nonzero net sale price, no excessive quote gap and must precede cutoff.
- Any unavailable execution evidence returns NO_EXIT_EVIDENCE, not
  an invented profit.
- TARGET_EXIT or STOP_EXIT identify the trigger reason, NOT a guaranteed
  minimum/maximum result. The later simulated Bid can produce a much
  smaller profit or even a LOSS after a target trigger.
- decision_trigger_at and exit_at are separately reported.
- No time-based exit is silently created where no order/quote evidence
  exists. Missing targets or insufficient evidence produce no PnL.
- Latency CLI accepts whole seconds 1..300. The pure Python kernel
  accepts positive timedeltas up to five minutes.
- Contract, quote feed, timestamps, sizes and cutoff remain validated.

The replay is NOT an order-book queue/market impact/fill simulator. Sizes
are displayed values, not a guarantee of depth persistence or order fill.

## Evidence and outstanding release gates

Synthetic regression cases cover trigger-to-loss, stop trigger with worse
fill, missing later Bid, zero size, quote gaps, invalid times, cutoff
crossing and data-source qualification failures.

EXACT_NBBO is only a user-supplied CSV label, not proof of OPRA provenance.
No actual source license, exchange last-trading time, Derayah execution
latency, mobile delivery receipt or live option alpha was verified.

Mandatory native Alibaba OCR exact-head review, independently licensed
timestamp-accurate option data, real trade statement reconciliation,
actual observed latency distributions and prospective paper evaluation
remain required before a live release. No automated brokerage order is
created or submitted by this research model.
