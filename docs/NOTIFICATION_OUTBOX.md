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
  checks the IMMUTABLE quote expiry and venue cutoff recorded at alert
  creation; caller-supplied longer TTL/cutoff can never extend them.
  The operation atomically moves eligible PENDING notices to CLAIMED,
  returning a one-time claim token. Separate concurrent workers cannot
  claim and send the same notice twice.
- Pending/claimed alerts are invalidated on a newer qualified quote below
  target, insufficient Bid depth, target change, or manual partial/full close.
- A claim is NOT permission to deliver. A separate worker must check a NEW
  genuinely qualified option quote, confirm open quantity and venue
  eligibility, and recheck freshness before sending.
- record_provider_acceptance(notification_id, claim_token, accepted_at,
  max_quote_age, last_trading_at, provider_accepted, provider_receipt_id)
  requires a one-time claim, explicit provider ACK, and event chronology.
  Provider receipt IDs are SHA-256 digested; the ACK timestamp is persisted
  in a separate provider_ack_events table.
- A late provider ACK is preserved as an audit event but does NOT mark an
  obsolete sell prompt as valid. Acceptance means transport provider
  acknowledgement, NEVER phone receipt or broker fill.
- Claimed notices are never automatically re-sent after an uncertain worker
  outcome. This favors duplicate prevention over delivery availability
  until an idempotent remote provider/heartbeat is qualified.
- Transactional database updates ensure that local partial/full close events
  invalidate any still-pending stale-quantity alerts.

This is only one part of a reliable notification pipeline.

## NOT READY

- There is no OPRA-qualified live feed or signed source proof.
- There is no trusted contract-specific last-trading cutoff registry.
- No ntfy, Apple Push, Firebase or broker notification channel is connected.
- There is no device receipt, heartbeat, service recovery, remote provider
  idempotency demonstration, retry/dead-letter service, or actual latency
  measurement. Claimed-but-unacknowledged notices need manual reconciliation.
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
