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
