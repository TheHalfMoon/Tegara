# DQ-P01 — Historical Options Quote CSV Qualification

Status: offline importer implemented. NO source is automatically certified as OPRA, executable, or licensed. No live integration exists. All tests use synthetic fixture rows.

## Research purpose

Tegara validates time-indexed historical quotes for exactly one option
contract and produces a diagnostic report. This is a prerequisite for
options research, not a source of market or broker fill evidence.

Run:

    PYTHONPATH=src python3 -m tegara quote-audit \
      --csv /absolute/path/to/quotes.csv \
      --symbol SPY --right C --strike 600 --expiration 2026-10-09 \
      --multiplier 100 --quantity 1 \
      --last-trading-at 2026-10-09T20:00:00+00:00

The last-trading-at value above is an EXAMPLE only, not a guaranteed
exchange cutoff for SPY, XSP, SPX, SPCX or any other instrument.
Supply a contract-specific value verified from exchange/broker documentation.

## Exact CSV header

    observed_at,symbol,right,strike,expiration,multiplier,bid,ask,bid_size,ask_size,feed_type,source

Example synthetic row (not a market observation):

    2026-10-09T14:30:00+00:00,SPY,C,600,2026-10-09,100,1.20,1.25,2,3,INDICATIVE,synthetic-fixture

One file represents exactly one strike, right, expiration and contract
multiplier with strictly increasing timezone-aware timestamps.
Price fields are decimals; size fields are nonnegative integers.
Observations must be on the NY expiration date. When a caller supplies
last-trading-at, quotes after it are invalid.

Files must be ordinary local files, not symlinks. Inputs are limited to
48MiB and 60,000 observations per scan. Data files must never enter Git.
The importer walks all directory components through pinned, descriptor-
relative O_DIRECTORY/O_NOFOLLOW opens, then opens the CSV leaf with
O_NOFOLLOW. Missing atomic no-follow support fails closed instead of
falling back to a pathname-following read. Only macOS's fixed /var and
/tmp aliases to /private are normalized. Tests refuse parent-directory
symlinks and last-moment symlink substitution. The snapshot remains
research-only and cannot attest licensed market-data provenance or
prevent malicious modification by the same user of an already-open file.

## Report fields

The report describes counts, first/last time, largest data gap,
zero Bid rows, insufficient Bid size, indicative feed counts,
locked quotes, and largest spread percent of Ask.

Crucial permanent fields:

- provenance_status: NOT_INDEPENDENTLY_VERIFIED
- licensing_status: NOT_INDEPENDENTLY_VERIFIED
- venue_cutoff_status: NOT_INDEPENDENTLY_VERIFIED or USER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED
- live_eligible: false

Merely writing EXACT_NBBO or OPRA in a CSV, choosing a source name, or
supplying a cutoff cannot elevate these fields to verified. True qualification
requires independent vendor/feed and rights checks; access does not
automatically imply rights to archive and reuse licensed market data.

## Test gates and limitations

- Strict schema, contract identity, time ordering, finite decimals,
  bid <= ask, sizes, source field, and expiration/cutoff gates.
- The separate replay prototype requires an externally supplied cutoff;
  without it the simulator returns UNQUALIFIED_DATA, not numeric P&L.
- Cost estimates require actual Derayah contract fees. No proof of
  marketability, order fill, dataset rights, or forecast performance.

No external vendor is contacted, no Derayah account is accessed,
and no real Bid-based sell notification is triggered by this importer.

## Extreme Decimal Input Handling

All user-provided Decimal amounts pass a finite, nonnegative and bounded
representation gate before option quote arithmetic. It rejects finite but
pathological values like 1e999999999 and 1e-999999999 (and extreme
zero exponent representations) that could overflow the normal Decimal
context during spread or P&L calculations. The common gate limits amount
to 1e12, exponent to [-18, +12] and significant-digit count to 28.
The replay CLI additionally limits input research amounts to 1e9.
The research domain rejects option contract multipliers and position
quantities above 1,000,000. These conservative bounds are application
resource and precision safeguards, NOT exchange lot/tick standards.

Invalid input must fail closed with a validation error, without silently
producing a price, journal record, alert or brokerage order. The tests use
synthetic intentionally adversarial CSV values; they are not real OPRA data.

## ASCII contract identity guard

The market symbol/root must consist of 1..16 ASCII uppercase characters
(A-Z, 0-9, and internally separating dots), e.g. SPY, QQQ, SPY1,
or BRK.B. Unicode lookalikes such as full-width characters, Cyrillic
letters that resemble Latin ticker characters, invisible whitespace, or
long roots are rejected BEFORE any contract-specific target, replay, or
local journal operation. This guards accidental identity confusion and
visual spoofing, not vendor-backed OCC symbology authentication.
Actual adjusted option deliverables and OCC contract class validity
still require independent vendor/clearing data.

## Exclusive options last-trade cutoff

A user-supplied last_trading_at is an EXCLUSIVE bound for options quote
observations: timestamps at or after that moment are invalid for a
hypothetical option exit. A timestamp just before the cutoff can pass the
local format/time rules but is STILL not independent OPRA provenance, a
broker-executable fill, or a verified contract last-trading time.

The legacy instant-reference research replay has the same exclusive
cutoff and exit deadline semantics. An option target or stop must never be
marked hypothetically executed on a same-event quote exactly at the cutoff.
The latency-aware model already requires strictly pre-cutoff later Bid
observations. Exchange holiday, early-close and contract-specific
eligibility remain external verification gates.
