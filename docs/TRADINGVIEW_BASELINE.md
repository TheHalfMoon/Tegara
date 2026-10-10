# TradingView Premium — Underlying Research Baseline (TV-P01)

**Status: source drafted, static safety checks PASS. Native TradingView Pine Compiler and actual alert delivery NOT_TESTED.** The scripts are not production-ready and must not be represented as qualified options signals.

## Scope

TradingView Premium supplies charts, Pine v6 script editor, technical alerts and its Strategy Tester. The two scripts in the tradingview folder do only the following:

1. Indicator: SPY/QQQ U.S. regular-session Opening Range (09:30–09:45 New York), session VWAP, EMA 9/21, and a basic relative-volume filter on underlying price.
2. The indicator emits bullish/bearish **underlying research candidate** alert conditions with simple symbol, time and close fields. It does not know any option strike, expiry, option Bid, implied volatility, or position state.
3. Strategy: executes a long/short position on the *underlying* in the TradingView broker emulator with an ORB reference exit and an end-of-session exit. Its P&L is NOT the P&L of a call or put and cannot validate an actual options strategy.
4. Both scripts use explicit America/New_York time-zone/session boundaries and confirmed bars, and avoid request.security() or future data pulls.

## Install and inspect in TradingView Premium

- Open the intended underlying's 1-minute or 5-minute chart in TradingView Premium.
- Open Pine Editor; paste and save tegara_underlying_indicator.pine. **Compile and fix any compiler errors before use.**
- Create two alerts using its conditions: "Tegara Bullish Underlying Candidate" and "Tegara Bearish Underlying Candidate". Configure Once Per Bar Close and TradingView mobile push. No option contract target is implied by these alerts.
- In a separate chart or layout, paste and compile tegara_underlying_strategy.pine. Open Strategy Tester / Deep Backtesting to compare underlying timing hypotheses over distinct sample periods. Set realistic underlying commissions and understand that this does not model Derayah options execution.
- Do not treat the strategy as tradable before inspecting the full fill log and confirming session boundaries (including the U.S. DST change).
- If a chart is at >5m timeframe, the scripts intentionally disqualify signals via a supported-timeframe filter.
- Do not connect TradingView script alerts to an authenticated brokerage session.

## Next stage / qualification

- Run the native Pine compiler and save screenshots/version metadata as evidence.
- Verify actual bar-close alerts on SPY/QQQ with time-zone/day rollover and session open/close (including half-day holidays, where needed).
- Export session data with trading rights and evaluate model baseline outside the TradingView UI.
- Options strikes, market Bid/Ask, fees, actual OPRA feed, instrument expiry availability and sell-to-close decision are all handled by a **future** separate qualified data adapter and deterministic risk engine.
- No direct integration with Derayah Global US options is assumed.
- TesterArmy e2e in this repository tests only Tegara's own browser UI, not the TradingView cloud UI or proprietary Pine compiler.

References:
- https://www.tradingview.com/pine-script-docs/concepts/sessions/
- https://www.tradingview.com/pine-script-docs/concepts/bar-states/
- https://www.tradingview.com/pine-script-docs/concepts/alerts/
- https://www.tradingview.com/pine-script-docs/concepts/strategies/
