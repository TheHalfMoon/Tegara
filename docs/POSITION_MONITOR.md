# G03 — Position Ledger and Sell-to-Close Alert Outbox

Status: prototype, unqualified for live monitoring.

## Supported local behaviors

- SQLite journal of manually recorded long-option positions.
- Explicit immutable contract identity: symbol, C/P, strike, expiration date, deliverable multiplier.
- Confirm manual sell-to-close fills with separate identifiers, exact Decimal prices and fees; supports partial quantity and idempotent retry.
- Reject cross-contract fills, conflicting duplicate identifiers, oversells and fills before entry.
- Derive realized net P&L only from confirmed manual fills. A push alert does not change position status.
- A qualifying option quote can queue a REVIEW SELL TO CLOSE message; the system does not send messages, enter orders or assume any fill.
- Ignore wrong contracts, stale or future quotes, unqualified feeds, insufficient displayed bid size and expired contracts.
- Prevent duplicate alerts from the same quote; prevent repeat notifications while the market stays above the target.
- Re-arm after a qualified quote crosses below the target, or when the user changes the net-profit target.
- Cancel pending notices after any confirmed partial/full close; reassess remaining quantity against a fresh quote.
- Persist pending notices and replay state across application restarts.
- Transactions use BEGIN IMMEDIATE to prevent concurrent double-close or duplicate-notification races.

## Not yet implemented / safety limits

1. **No OPRA feed adapter** and no independent way to verify a source claiming EXACT_NBBO. This is a trusted-data-interface assumption; only development fixtures are currently used. Do not expose it to untrusted payloads.
2. No market session and exchange-specific last-trading-time calendar for a particular contract. Date-only expired-contract blocking is necessary but insufficient to authorize a sell alert at the end of an expiration session.
3. No option contract tick-size lookup, corporate-action deliverable discovery, quote provenance attestation, or bid-queue size evidence beyond the supplied fields.
4. No delivered mobile alert, Apple/Android notification receipt or end-to-end service heartbeat.
5. No Derayah Global position API, no broker connection, no order placement.
6. No data-led financial backtesting, paper profit qualification or live trade recommendations.
7. Database is protected by local file permissions but **not encrypted**. Users should rely on their operating-system disk encryption and must keep the database and exports out of source control. Never store account numbers or credentials.

## Offline target CLI

From the repository root:

    PYTHONPATH=src python3 -m tegara target \
      --symbol SPY --right C --strike 600 --expiration 2026-10-09 \
      --entry 1.20 --quantity 1 \
      --entry-fee 2.29 --exit-fee 2.29 \
      --net-return 30 --tick 0.05

All example prices, fees and dates are hypothetical. The command prints the rounded *candidate* option bid and estimated net P&L at that price. It does not query the market and must not be interpreted as a quote.

## Verification

    PYTHONPATH=src python3 -m unittest discover -s tests -v
    graft build .
    graft check .

TesterArmy browser e2e tests must be added at the UI milestone. Neither Jev nor Kronos are safety gates for arithmetic or broker fills.

## Remaining G03 qualification gates

- Verify GitHub commit signature on the exact candidate head.
- Run meaningful independent Alibaba OpenCodeReview on the exact diff.
- Add feed adapter provenance, explicit time-of-last-trade policy and notification delivery under separate milestones.
- Document realistic UI flow and run TesterArmy e2e when implemented.
