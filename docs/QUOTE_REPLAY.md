# QBT-P01 — Offline Quote Replay (Research Only)

This module is a minimal deterministic **simulation** for a single long U.S. options contract. It does not obtain market data, place or fill broker orders, qualify exchange licensing, or prove that an option was executable at historical prices.

## Input contract

A calling process supplies a time-ordered list of contract-specific OptionQuote objects plus quoted ask size, an explicit replay rule, and a separate provenance_approved assertion. No replay result includes monetary P&L unless the caller opts into that assertion. This is not a certification system: the caller must separately verify dataset origin, licensing rights, actual contract identity, timestamps, feed semantics and quotes. Tests use synthetic fixtures even when the assertion is set true.

The replay uses **Ask + modeled slippage** at entry and **Bid - modeled slippage** at the eventual exit. It includes buy/sell per-contract fees and the contract deliverable multiplier. The default response never claims an actual fill. Results use labels TARGET_EXIT, STOP_EXIT, TIME_EXIT, UNQUALIFIED_DATA, NO_ENTRY_LIQUIDITY or NO_EXIT_EVIDENCE.

## Conservative missingness behavior

- A wrong contract or non-monotonic timestamp is invalid input.
- Missing OPRA-quality feed label and absent approval prevent numeric P&L.
- Insufficient ask size at the first observation prevents entry.
- Bid size below open quantity prevents a modeled exit. For a
  **discretionary TIME_EXIT**, a newer pre-cutoff quote without sufficient
  Bid size (or whose Bid cannot cover modeled sell slippage) also invalidates
  the *older* time-exit candidate. A subsequent eligible quote may restore
  a candidate. The model must not retroactively book a positive exit from a
  stale earlier Bid when the latest observed pre-cutoff depth disappeared.
- A gap longer than the configured maximum ends the replay without a claimed exit or P&L.
- If the last usable quote is too far from the requested exit deadline, the replay returns NO_EXIT_EVIDENCE, not a fabricated closing value.
- A result that has a simulated P&L is still strictly a synthetic **hypothetical bid/ask fill**, not evidence that a real Limit or Market order would have filled.

## Known limitations before any trading study

No OPRA license/data adapter, order book/queue simulation, spreads under stress, early assignment, exercise, exchange-specific last trading times, half days, IV / Gamma / Theta repricing, model prediction, signal-selection rules, option eligibility screening or real Derayah commissions are automatically included.

Most critically: do not train Kronos on a study sample that includes future bars relative to any simulated trade. This module does not train or evaluate AI models.

## Evaluation plan

1. Verify a licensed historical options quote source and written retention/export rights.
2. Define exact contract identifiers, feed qualification, event-time availability, delays and time zones.
3. Compare a simple TradingView underlying timing baseline to matched option contracts including transaction costs.
4. Run date-based walk-forward with a clean locked holdout.
5. Reject any strategy showing alpha only under unrealistic fills, missingness assumptions, or data leakage.

The synthetic test suite is an API implementation check, not a performance study.

## DQ-P01 cutoff/data tightening

ReplayRules now accepts last_trading_at, a required timezone-aware explicit
time on the contract's NY expiration date. Without it, replay returns
UNQUALIFIED_DATA (no monetary result), even when the caller asserts data
provenance. The effective exit deadline never extends past last_trading_at.
Quotes from a different NY expiration session are rejected. This cutoff is
provided by the caller and remains independently unverified; the tool does
not infer the correct last trading time for SPY, SPX, XSP, SPCX or other roots.

Use quote-audit to inspect user-provided CSV quality before replay. Neither
tool certifies licensing, marketability or realized fills.
