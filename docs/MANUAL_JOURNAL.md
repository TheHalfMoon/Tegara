# JN-P01 — Offline User-Declared Options Journal

Status: LOCAL RESEARCH LEDGER ONLY. This feature does not log in to Derayah Global, fetch market data, validate brokerage statements, send option orders or operate a live notification service.

The SQLite store uses transactions and deterministic Decimal fee accounting. Records are entirely user-entered and marked USER_DECLARED_UNVERIFIED. Do NOT confuse a journal's "CLOSED" state with a broker-confirmed execution.

## Open a manually recorded long CALL position

The examples below are synthetic, deliberately using a past expiration. Supply actual option multiplier and per-contract fees for any personal study. Keep SQLite files OUTSIDE the Git repo.

    export TEGARA_JOURNAL="$HOME/Private/Tegara/manual_journal.sqlite"

    PYTHONPATH=src python3 -m tegara.journal_cli --db "$TEGARA_JOURNAL" \
      open --position-id example-spy-1 \
      --symbol SPY --right C --strike 600 --expiration 2026-10-09 \
      --entry 1.20 --quantity 3 \
      --entry-fee 2.29 --exit-fee 2.29 \
      --opened-at 2026-10-09T10:30:00-04:00 \
      --record-only

The explicit --record-only flag is mandatory for writes. It is not a real trade authorization.

## Inspect recorded state

    PYTHONPATH=src python3 -m tegara.journal_cli --db "$TEGARA_JOURNAL" \
      show --position-id example-spy-1

Outputs include remaining quantity, manually reported net P&L and broker_execution_verified=false. OPEN means it has not yet been marked closed in this journal, not that the broker confirms a live position.

## Record a claimed manual Sell-to-Close fill

After independently reviewing a broker statement, a user can transcribe a fill; Tegara cannot prove it happened.

    PYTHONPATH=src python3 -m tegara.journal_cli --db "$TEGARA_JOURNAL" \
      manual-close --position-id example-spy-1 \
      --fill-id example-confirmation-001 \
      --quantity 1 --fill-price 1.62 \
      --fee-per-contract 2.29 \
      --filled-at 2026-10-09T10:35:00-04:00 \
      --record-only

Supports partial closes. Repeating the same fill ID with identical details is idempotent; conflicting duplicates fail; overselling recorded open quantity fails. No option order is submitted.

## Inspect manually recorded fills

    PYTHONPATH=src python3 -m tegara.journal_cli --db "$TEGARA_JOURNAL" \
      fills --position-id example-spy-1

No credentials or brokerage account numbers are needed or stored. Do not store sensitive personal identifiers in free-form IDs.

## Privacy and correctness

- Owner-only POSIX file mode (0600) for the local database.
- Direct database symlinks are rejected. Journal paths inside Tegara's Git checkout are disallowed to prevent accidental commits.
- A malformed opening command does not create an empty journal file.
- SQLite is NOT encrypted. Use an encrypted personal directory and appropriate backups.
- Timestamp inputs require explicit timezone offsets.
- Money accounting uses only user-entered fees, quantity, premium and contract multiplier. It does NOT model broker balances, tax, assignment or marketability.
- No broker credentials, web requests, automated trading or live market data.

## Remaining qualifications

GitHub exact-head tests and independent Alibaba OpenCodeReview remain merge gates. A read-only PStack engineering playbook was consulted; independent multi-model reviewers were NOT available and no PStack multi-review result is claimed. Real Derayah statement qualification and 0DTE live quote source proof remain unresolved.
