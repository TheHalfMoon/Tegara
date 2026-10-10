# Notification Outbox Safety — NT-P01

This is an implementation of local SQLite state transitions only. NO actual
mobile notification provider has been connected or qualified, and NO market
data source, Derayah Global trading account, or live order API is attached.

## Problem addressed

An options bid may briefly meet a target, creating a pending sell-to-close
alert. If the app reconnects late, a naive worker might deliver a stale
signal hours after the quote or after the contract's final trading cutoff.

Tegara now supports a fail-closed revalidation stage:

- revalidation_candidates(position_id, now, max_quote_age, last_trading_at)
  rejects unknown last trading cutoff, invalid timestamps, closed positions
  and stale pending alerts. Older pending rows are permanently invalidated.
- A returned candidate is NOT permission to deliver. A separate worker must
  check a NEW qualified option quote, confirm the position quantity, ensure
  broker and venue eligibility, and then check notice freshness again.
- record_provider_acceptance(notification_id, accepted_at, max_quote_age,
  last_trading_at, provider_accepted) requires an explicitly successful
  provider response. The row state becomes ACCEPTED_BY_PROVIDER, which is
  distinct from an actual user-visible mobile notification or broker fill.
  Failed, stale, expired or invalidated messages cannot be recorded as accepted.
- Transactional database updates ensure that local partial/full close events
  invalidate any still-pending stale-quantity alerts.

This is only one part of a reliable notification pipeline.

## NOT READY

- There is no OPRA-qualified live feed or signed source proof.
- There is no trusted contract-specific last-trading cutoff registry.
- No ntfy, Apple Push, Firebase or broker notification channel is connected.
- There is no device receipt, heartbeat, service recovery, retry/backoff,
  dead-letter mechanism or actual notification latency measurement.
- No broker fill is triggered by transport success.

Use these methods only within an authorized and audited local test process.
Do not expose these ledger functions through an unprotected public endpoint.

## Evidence

- Local unit tests cover fresh, stale, late, future, wrong-cutoff,
  partial-close, closed-position, false provider acknowledgment, and
  provider-accepted-but-not-broker-filled cases.
- TesterArmy e2e continues to cover the existing local-only target UI.
- Any future integration requires independent Alibaba OCR review and actual
  qualified market data before enabling live sell-target notifications.
