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
