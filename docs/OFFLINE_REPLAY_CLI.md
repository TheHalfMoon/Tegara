# RP-P01 — Offline Option Bid/Ask Research Replay CLI

Status: LOCAL RESEARCH PROTOTYPE ONLY. No broker, account, OPRA vendor,
real exchange, TradingView API, phone notification service, or network is used.
Tegara has not established a profitable 0DTE strategy.

## Purpose

Previously the strict one-contract CSV quote parser and replay engine were
Python-only modules. This stage connects them into one CLI. A report shows
input data quality, options Bid/Ask simulation assumptions, and limitations.

## Synthetic example

    PYTHONPATH=src python3 -m tegara.replay_cli \
      --csv /private/quotes_SPY_20261009_CALL600.csv \
      --symbol SPY --right C --strike 600 \
      --expiration 2026-10-09 --multiplier 100 --quantity 1 \
      --entry-fee 2.29 --exit-fee 2.29 \
      --entry-slippage 0.01 --exit-slippage 0.02 \
      --target-net-profit 35 --max-net-loss 50 \
      --deadline 2026-10-09T14:40:00+00:00 \
      --last-trading-at 2026-10-09T20:00:00+00:00 \
      --max-gap-seconds 300

The cutoff above is ILLUSTRATIVE and NOT independently verified as correct
for that instrument or session. The caller must independently verify actual
venue cutoff and broker fees.

## Default: abstain from numerical P&L

Without extra flags, the CLI checks the CSV and emits:
outcome = UNQUALIFIED_DATA; hypothetical_net_pnl_usd = null.

To deliberately request a synthetic what-if result, append:

    --allow-hypothetical-pnl

This is ONLY consent to model hypothetical outcomes; it is not evidence
of data feed rights, OPRA provenance, marketability, or actual fills.
Even after the flag, any INDICATIVE quote results in UNQUALIFIED_DATA.
Insufficient Ask size, stale-data gaps, and missing exit evidence do not
produce fabricated monetary returns.

## CSV input

Exactly one contract per file. Required header:

    observed_at,symbol,right,strike,expiration,multiplier,bid,ask,bid_size,ask_size,feed_type,source

Observations require timezone-aware timestamps, correct expiration date in
America/New_York, exact contract identity, ascending order, finite
nonnegative Bid/Ask with Bid no greater than Ask, quote sizes, and
recognized feed classifications. Source files are bounded to 48 MiB and
60,000 observations. Input is read as a single bounded snapshot: SHA-256
is calculated on the SAME bytes as those used for parsing. Symlinks and
nonregular files are rejected. An accurate hash is not a market license.

## Output guarantees

- CSV quality metrics: count, timestamp gap, sizes, spreads, indicative rows,
  zero Bid, locked market, and exactly parsed source SHA-256.
- Contract/right/strike/expiry/multiplier and qty, fees, slippage and target.
- Hypothetical result only if explicitly permitted and replay API allows it.
- source_provenance_independently_verified: false
- data_licensing_independently_verified: false
- last_trading_time_independently_verified: false
- broker_execution_verified: false
- phone_notification_sent: false
- options_profitability_validated: false

No file writes, HTTP calls, notifications or broker orders are made.

## Research limitations

Buy is simulated at Ask plus slippage, sell at Bid minus slippage. The
contract multiplier and per-contract entry/exit fee assumptions enter
the simulated P&L. A TARGET_EXIT represents a hypothetical algorithmic
choice, not a proven executable fill.

No queue-priority, order routing latency, partial fills, IV/Greeks,
assignment, exchange specific tick and close rules, OPRA licensing,
real-time quote availability, taxes or broker fee validation are proved.
A single replay is not a prospective paper study or valid options alpha.

Exact-head CI and independent Alibaba OCR review must be completed before
any upstream merge, and new live broker/market integration requires separate
authorization. No subscription or paid service was added.
