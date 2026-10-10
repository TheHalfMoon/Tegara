# NT-P02 — Read-only local notification reconciliation

Status: OFFLINE RESEARCH AUDIT ONLY. This is NOT a mobile push
notification provider, an actual Derayah options order, a phone-delivery
receipt or proof of licensed OPRA data.

## Local usage

Run against an EXISTING Tegara private SQLite database that you own:

    PYTHONPATH=src python3 -m tegara.outbox_audit \
      --db /private/path/to/local/positions.sqlite --strict

Optionally scope counts to one exact local position:

    PYTHONPATH=src python3 -m tegara.outbox_audit \
      --db /private/path/to/local/positions.sqlite \
      --position-id YOUR_LOCAL_POSITION_ID --strict

The command only reads a SQLite snapshot; it does not create tables,
files, alerts, positions, claims, notifications, or orders. It does not
call TradingView, Derayah, APNs, OPRA or another network provider.

Exit codes:
- 0: report generated, no unacknowledged historical claims (NOT release ready)
- 2: strict report found at least one claimed notice without an ACK
- 3: database missing, malformed, or unsafe, no readiness claim

Output contains only counts per notification state, expired/unknown
unacknowledged claims, and fixed Boolean disclaimers. Claim tokens,
notice IDs, local position IDs, option prices and provider receipt
digests are not written to the report. Local paths stay on the Mac.

## Critical ambiguity

CLAIMED means a local worker reserved an alert, not that it actually
sent a notification. A claim may later be INVALIDATED when an adverse
quote arrives, the contract cutoff expires, or a manual close occurs.
If it was previously claimed but has no separate provider ACK record,
the remote outcome remains uncertain. The audit counts such
claimed-then-invalidated rows as requiring manual reconciliation.

An ACCEPTED_BY_PROVIDER ACK is only a transport-provider observation,
NOT a verified phone receipt, broker execution or trade. An invalidated
notice with provider ACK is historical evidence, not a current sell
signal. No provider data is authenticated by this local audit.

The tool never retries, dispatches, settles, re-arms or upgrades an
unconfirmed claim. Uncertain sends must never be automatically redelivered.

## Safety limits

- The database file must already exist, be regular and not itself a
  symlink. macOS system directory aliases are canonicalized.
- The existing on-disk SQLite file is opened exactly once using atomic
  O_NOFOLLOW on macOS, its bounded bytes are loaded into an IN-MEMORY
  SQLite database and query_only is enabled. No ledger file is opened
  for SQLite writes or silently switched to a symlink during access.
- Active WAL, SHM and rollback-journal sidecars are refused because
  an on-disk main-file snapshot might omit committed changes. Audit
  only a QUIESCENT offline database, not during ongoing writes.
- Unknown states, missing immutable policy, absent primary/foreign keys,
  malformed provider ACK timestamps or digests, future-dated claim
  timestamps and contradictory ACK status fail closed.
- Max 10,000 outbox rows and 32 MiB on-disk main database; no truncated
  audit passes. Large databases must be handled by an independently
  qualified, resource-bounded read-only workflow, not silently truncated.
- Synthetic tests check DB SHA-256 unchanged, tokens withheld, strict
  nonzero exit and invalidated-but-unacknowledged claims counted.

Live qualification still requires licensed recent options quotes, venue
cutoffs, genuine remote transport, phone receipt latency, broker
reconciliation, prospective paper testing and independent native
Alibaba OpenCodeReview sign-off.
