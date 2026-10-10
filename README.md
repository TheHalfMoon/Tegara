# Tegara

Tegara is a local-first, evidence-driven research and decision-support system for U.S. listed options, starting with 0DTE SPY and QQQ.

**Status:** early development. Not live trading software. No tested alpha or returns are claimed.

## Intended product
- Analyze underlying prices from TradingView Premium and properly licensed market-data sources.
- Track manually entered Derayah Global long-call/long-put positions.
- Calculate net option-exit targets from actual fills, contract multipliers, and configurable fees.
- Trigger *review sell-to-close* notifications only from validated, timely, contract-matching, executable quotes.
- Compare Kronos forecasts with transparent simple baselines before using any AI output in a decision.
- Record evidence, simulation results, and paper trades. No automated live order placement.

## Priorities
1. Deterministic target/P&L core with comprehensive negative tests.
2. Position-state machine, quote quality gates, and notification audit trail.
3. TradingView Pine Script v6 baseline plus export/alert ingestion.
4. Historical quote adapter qualification and execution-aware backtesting.
5. AI benchmark, paper testing, then manual Derayah workflow.

All engineering artifacts are in English. Financial account credentials and trade records stay private. Never claim that a bid is an executed sell, a forecast is a guaranteed target, or that a backtest demonstrates live profitability.

See AGENTS.md for engineering requirements.

## Running the founding prototype

G02 contains only a pure Python library. There are no network connections or trading orders.

From a checkout:

    PYTHONPATH=src python3 -m unittest discover -s tests -v

The arithmetic requires explicit Decimal values and actual per-contract fees supplied by the caller. An assessment of an option quote is an *indication to review a sell-to-close*, not a fill, a broker order, or a guaranteed price.

Planning details: docs/PLAN.md. Architecture: docs/ARCHITECTURE.md. Quality evidence: docs/QUALITY_EVIDENCE.md.

## Offline target calculator and local position journal (G03 prototype)

- python -m tegara target computes an illustrative target option bid from real user-supplied fees, an explicit contract multiplier and tick increment. It makes no network calls.
- The tegara.position_store.PositionStore maintains a local SQLite journal of manually confirmed fills and a pending review-notification outbox. Merely queuing or delivering a notification does not close a position.
- Code has no live feed, no Derayah connection, no actual push delivery and no authorization for live orders.

See docs/POSITION_MONITOR.md for scope and limitations.


## Local dashboard preview (UI-P01)

This is an **offline hypothetical target calculator**, not a live options
monitor or an execution tool. Nothing is sent to Derayah, TradingView or an
external data API.

Run from this repository:

    PYTHONPATH=src python3 -m tegara.dashboard --port 8765

Open http://127.0.0.1:8765 and supply your position's actual contract
multiplier, fees, target and valid tick increment. A candidate target Bid is
not an actual available market price.

To run the free, model-less TesterArmy browser e2e tests:

    npm ci --no-audit --no-fund
    E2E_TELEMETRY_DISABLED=1 npm run test:e2e -- --workers 1

See docs/LOCAL_DASHBOARD.md for the security and data limitations.

## TradingView Premium baseline scripts (TV-P01, uncompiled prototypes)

The tradingview folder contains Pine v6 underlying-only opening-range/VWAP
research indicator and Strategy Tester scripts. Script compilation in the
TradingView Pine Editor and actual platform alert delivery are unverified.
They contain no option pricing, no Derayah integration and no executable
Sell-to-Close contract signal. The strategy's financial performance is ONLY
the underlying proxy, not option P&L.

See docs/TRADINGVIEW_BASELINE.md for manual compile/verification steps.

## Offline options quote replay (QBT-P01)

Python replay module tegara.quote_replay models entry at an option Ask and exit
at its Bid under user-supplied fees and slippage. It includes deterministic
target, stop, time-exit, insufficient-liquidity and missing-quote states.

**There is no historical options data bundled with Tegara.** All tests use
artificial quotes to verify code behavior, not profitable trading results.
The caller must independently verify historical quote provenance and rights
before making any financial evaluation. See docs/QUOTE_REPLAY.md.

## Historical quote-quality audit (DQ-P01)

Tegara now has an offline single-contract CSV validator. Run a local quote
quality report without network or trading activity:

    PYTHONPATH=src python3 -m tegara quote-audit --csv /path/to/quotes.csv \
      --symbol SPY --right C --strike 600 --expiration 2026-10-09 \
      --multiplier 100 --quantity 1 \
      --last-trading-at 2026-10-09T20:00:00+00:00

The last trading cutoff above is ILLUSTRATIVE and must be independently verified
per contract and venue. The resulting report ALWAYS labels licensing and OPRA
provenance as NOT_INDEPENDENTLY_VERIFIED and live_eligible=false. See
docs/QUOTE_DATA_QUALITY.md. No quote feed or broker access is included.

## Sell notification outbox safety (NT-P01)

The local SQLite position manager includes an explicit pre-delivery
revalidation gate and one-time claim mechanism for pending option target alerts. A pending alert is NOT a
market order and not proof that an option can still be sold at the quoted Bid.
Stale, expired, unknown-cutoff and manually closed alerts are suppressed.

A provider acknowledgment is recorded distinctly as ACCEPTED_BY_PROVIDER;
it does not represent push delivery to a phone, a human reading an alert or a
Derayah broker execution. No notification provider is connected yet.

See docs/NOTIFICATION_OUTBOX.md.

## Research-only Kronos bridge (KR-P01)

The tegara.kronos_bridge module can invoke the official upstream
KronosPredictor on strictly validated 1-minute/5-minute historical underlying
bars and compare its predicted closes to a persistence baseline. Predictions
are NOT options quotes, trading signals, probabilities of profit or actual
Sell-to-Close alerts.

A CPU smoke test using official Kronos-small on deliberately synthetic bars
passed on an Apple Silicon Mac on 2026-10-10. See docs/KRONOS_BRIDGE.md for
the pinned upstream revision, optional dependencies and caveats.

## TradingView Premium underlying history research (WF-P01)

Tegara can convert a locally exported TradingView-like OHLCV CSV into
completed-bar timestamps and compare Kronos-small with persistence
on chronologically held-out underlying market sessions. Source timestamp
semantics (open vs close) MUST be set explicitly to prevent future-bar leakage.

Read docs/TRADINGVIEW_WALKFORWARD.md for example commands and the explicit
contract/session cutoff CSV requirements. Inspect-only mode never downloads
models, calls a remote service or makes orders. Real option bid/ask backtesting
and historical profitability remain unqualified.

TradingView documents manual chart CSV export at:
https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/
Only chart data actually loaded is exported; this is not an OPRA options
quote feed. Retention rights and actual timestamp semantics require review.

## Local macOS Notification Center research demo (LM-P01)

No live option data feed or phone push is connected. You can preview a
hypothetical option Bid target notice or explicitly send ONE manually entered,
clearly labeled *research demo* notification to the Mac's Notification Center.
The delivery response only means macOS accepted the local process request,
not that a user saw a message or a Derayah options order was filled.

Instructions and opt-in CLI switches: docs/LOCAL_MAC_NOTIFICATIONS.md.

## Synthetic options-target end-to-end regression (SJ-P01)

A zero-network integration test runs an unverified options quote CSV through
the contract data validator, SQLite target crossing, one-time notification
claim, simulated provider acceptance, and separately entered manual fill.
This test never sends a phone/OS notification or places broker orders.
It also checks unverified feeds and missing session cutoffs fail closed.

See docs/SYNTHETIC_JOURNEY.md for exactly what this evidence does/not prove.

## US option market data and broker integration gates

Read docs/MARKET_DATA_QUALIFICATION.md before selecting a data provider.
Existing TradingView Premium and Derayah Global access DO NOT independently
establish exportable live OPRA feed rights. In particular TradingView's
broker-entitlement verification table currently lists Derayah for TADAWUL,
not OPRA. The optional Cboe quote-interval product is a licensed market
data purchase, not a free live price stream. Do not buy any additional
subscription or enable execution without a separate explicit authorization.

## Quote-feed degradation safety (FD-P01)

Newer ineligible quotes for the correct contract now invalidate any old
PENDING/CLAIMED target alerts when the feed is indicative, too delayed,
insufficiently liquid, past the cutoff or cannot provide a valid cutoff.
The position then requires a strictly newer qualified quote before queuing a
new review alert. A wrong-contract or older quote cannot cancel a newer
one. This is still a local synthetic research ledger without real OPRA proof.
See docs/FEED_DEGRADATION_SAFETY.md.

## Additional static security checks (SEC-P01)

Draft PRs also run a separate CodeQL matrix for Python and
JavaScript/TypeScript (security-extended rules). A successful CodeQL scan
does NOT replace the mandatory independent Alibaba OpenCodeReview,
real-world market-data qualification, or options execution testing.
See docs/CODEQL_SECURITY.md.


## Local user-declared options journal (JN-P01)

A separate SQLite terminal tool lets you manually enter a long-option position, inspect its remaining quantity and manually recorded P&L, record a user-declared sell-to-close fill, and list those fill records. Every report is labeled USER_DECLARED_UNVERIFIED and broker_execution_verified=false. Database writes require --record-only and paths inside the repo or symlinked SQLite files are rejected. The tool is offline: it cannot authenticate with Derayah Global, verify trade statements or submit orders.

See docs/MANUAL_JOURNAL.md for complete commands, safety boundaries and test evidence.

## Offline option Bid/Ask replay CLI (RP-P01)

Research-only option quote simulation now combines the existing strict
single-contract CSV data-quality validator with the Ask+slippage entry,
Bid-slippage exit, fees and cutoff-aware replay rules in a reproducible
terminal command. It emits the exact parsed-input SHA-256 and all risk
assumptions while explicitly setting source and rights proof to false.

Default results ABSTAIN from hypothetical monetary P&L. The optional
--allow-hypothetical-pnl flag requests synthetic scenario analysis, NOT
independent vendor license verification, a guaranteed options fill or
real-world profitability. All runs remain offline and do not touch Derayah.

See docs/OFFLINE_REPLAY_CLI.md for commands, input format and limitations.

## Delayed quote execution sensitivity (LT-P01)

The default --execution-mode delayed option quote replay simulates
positive entry/exit latency and demands later quote evidence rather than
reusing the target-trigger Bid as its sell fill. The legacy optimistic
--execution-mode instant-reference requires explicit selection. Missing timely
depth or quotes abstain; a triggered target can still have negative hypothetical
PnL at a later lower Bid. See docs/LATENCY_REPLAY.md. This never places
or confirms a Derayah order and does not verify licensed OPRA data.

## Fixed latency sensitivity stress matrix (LT-P02)

The offline option quote replay also accepts a fixed entry/exit latency
grid (e.g. --entry-latency-grid 1,5,30 and --exit-latency-grid 1,5,30).
It evaluates bounded, deterministically ordered delayed scenarios against
the SAME CSV snapshot without selecting the historically best latency.
Missing fill evidence stays null and suppresses aggregate hypothetical
min/max; the top-level result never pretends to be a single executed fill.
The usual unverified OPRA, data-rights, phone and broker caveats apply.
See docs/LATENCY_SENSITIVITY.md.
