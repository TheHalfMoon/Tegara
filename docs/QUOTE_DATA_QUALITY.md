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
