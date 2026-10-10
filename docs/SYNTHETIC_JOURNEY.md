# SJ-P01 — Synthetic End-to-End Option Target Journey

This is a zero-network integration regression covering the real Tegara module
boundaries. ALL option quotes, provider receipt identifiers and fill IDs used
in the test are fabricated fixtures. No exchange, broker, market data vendor,
notification service or phone is contacted.

## Reproducible test

    PYTHONPATH=src python3 -m unittest discover -s tests \
      -p test_full_synthetic_journey.py -v

The test executes this sequence using a temporary CSV and SQLite database:

1. Create an unverified one-contract SPY Call quote CSV with explicit
   expiry, strike, multiplier, Bid/Ask, size, and New York last-trade cutoff.
2. Validate single-contract identity and return report with live_eligible=false.
3. Open one synthetic long position and feed correctly sequenced option quotes
   through PositionStore.on_quote.
4. Verify the price crossing creates exactly one pending review notification.
5. Atomically claim it with one-time token after exact quote policy checks.
6. Independently format a MANUAL BID / NOT VERIFIED macOS demo notice
   WITHOUT invoking any OS notification or external network process.
7. Record a simulated provider ACK as a separate audit fact, not phone receipt.
8. Reconcile a separately entered synthetic manual Sell-to-Close fill, close
   the position and assert remaining quantity is zero.

Additional negative end-to-end cases verify unqualified INDICATIVE feeds,
unknown last-trade cutoffs, missing quote intervals, and prevention of
notification ACK from being interpreted as a broker order.

This test originally found that the local downstream worktree omitted a later
notification security fix on its parent draft branch. We propagated the
reviewed fix through all dependent branches by signed, normal merge commits
(no rebase, no force push) before accepting the test as PASS.

## Explicit non-claims

- No real option quotes or contracts were independently sourced or licensed.
- No provider ACK was actually sent to an Apple/Android phone.
- No authenticated Derayah Global session was created or used.
- No actual option order, limit execution or fill was placed.
- No model was trained or evaluated for options profitability.

Native Alibaba OpenCodeReview completion, licensed option quotes,
live notifications, broker-reconciled manual trades and forward paper trading
are still separate unresolved acceptance gates.
