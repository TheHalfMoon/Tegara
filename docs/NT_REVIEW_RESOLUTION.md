# NT-P01 — Independent delegated-review findings and repairs

Independent read-only reviewer: Codex CLI using actual Alibaba OpenCodeReview
Delegation Mode preview and resolved Python rules.

Original reviewed SHA: ab30625423de043b1141ab82140f329f89036d33.
This was NOT the native Alibaba OCR model-driven review; do not count native OCR PASS.

## Verified findings and repairs

1. HIGH: pending sell prompt remained actionable after latest qualified price
   slipped below target or Bid depth dropped. FIX: invalidate both PENDING and
   CLAIMED messages when an in-order disqualifying quote, target change or
   manual fill updates the position. Added reversal/liquidity/target regression.

2. HIGH: later callers could extend original TTL and last-trading cutoff.
   FIX: store immutable quote_expires_at and contract_cutoff_at in each notice.
   Both claim and provider acceptance enforce the original absolute limits,
   plus any stricter current limits. Old database notices with missing policy
   fields are not eligible.

3. MEDIUM: two send workers could pick the same PENDING row concurrently.
   FIX: BEGIN IMMEDIATE transactional PENDING -> CLAIMED one-time claim-token
   transition, with a separate token check for recording provider acceptance.
   Claimed messages do not auto-retry without external delivery reconciliation.
   Added simultaneous two-worker SQLite test.

4. MEDIUM: provider ACK time/order and receipt were not audited.
   FIX: enforce acceptance chronology against alert creation and claim time,
   persist provider acceptance timestamp and SHA-256 receipt digest, and log
   provider_ack_events independently of whether the alert remains valid.
   A late ACK after an invalidating price change is logged but does not revive
   the pending notification or falsely imply a phone receipt/broker fill.
   Exact-match ACK repetitions are idempotent; conflicting receipts fail.

## Remaining qualification

These tests use synthetic market data and simulated provider ACKs. No actual
OPRA market-data provider, Apple push, ntfy transport, broker order or mobile
receipt was observed. Independent reviewer re-review should target the final
SHA; native Alibaba OCR LLM remains NOT_PASS until independently completed.
