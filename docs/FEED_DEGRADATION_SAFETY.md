# FD-P01 — Quote Feed Degradation Invalidates Pending Review Alerts

Status: deterministic synthetic ledger-safety improvement. This does not
license any market data, deliver a phone notification or execute an order.

## Previously unsafe pending-alert scenario

A currently pending option Sell-to-Close research alert might have crossed
the target at timestamp T0. If a newer same-contract quote arrived at T1 but
was non-NBBO / delayed beyond the permitted quote-age policy / lacked Bid
depth / could not be confirmed against a session cutoff, the ledger previously
could keep the old pending notice queued until its original TTL.

The later quote is NOT proof that the original target remains actionable.
A new and independently qualified quote is required to reassess the target.

## New fail-closed transitions

If the event pertains to the same contract and has an observation timestamp
strictly newer than the monitor's previous observation:

- INSUFFICIENT_SIZE -> INVALIDATED_LIQUIDITY
- UNQUALIFIED_FEED -> INVALIDATED_FEED_DEGRADED
- STALE_QUOTE -> INVALIDATED_FEED_STALE
- CUTOFF_UNVERIFIED -> INVALIDATED_CUTOFF_UNKNOWN
- AFTER_CUTOFF or EXPIRED -> INVALIDATED_AFTER_CUTOFF
- WRONG_TRADING_DATE -> INVALIDATED_TRADING_DATE

The transaction invalidates PENDING and CLAIMED notices and sets the monitor
to armed for the next strictly newer quote. It records the invalidating
timestamp to prevent replay of already-observed messages.

A *different contract* or an older same-contract message does not invalidate
an otherwise current alert. Previous provider ACK events remain auditable
and cannot revive an invalidated notice.

The source field and feed_type label alone cannot prove real OPRA vendor
provenance, exchange licensing or executable market depth. All current tests
use synthetic quote fixtures. The work has no live-market transport.

## Evidence

- Test degraded feed after a valid target and assert original pending cancel
  plus re-qualification required for a newer observation.
- Test degraded feed after one-time claim with simulated late provider ACK;
  old state remains invalid and is not reported timely.
- Test older and wrong-contract messages cannot cancel a newer alert.
- Test stale quote transport and unknown cutoff fail closed.
- Existing full synthetic integration, TesterArmy e2e, Kronos bridge and
  replay tests must remain green before merge.

Governance remains independent OCR exact-head review + CI + genuinely licensed
real options data for any live release.
