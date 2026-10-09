# Tegara — Adopted Engineering Plan (v0.1)

## Goal and non-goals

Deliver local-first research, monitoring, paper-trading, and **manual sell-to-close decision support** for U.S. listed 0DTE options through Derayah Global. TradingView Premium is the charting/underlying-alert surface. Do not imply Derayah Global supports a retail options API or TradingView broker execution. Do not scrape broker credentials, automate orders, or create any live sell order.

Start with SPY and QQQ; add NVDA, TSLA, META, and SPCX only after confirming actual listed option expiry dates and quality of contract-level data. Confirm SPCX instrument identity and availability from exchange/broker records, not user-facing chart text alone.

## PStack architectural alternatives

A. Monolithic web server handles chart events, forecasts, quotes, alerts, trades and persistence: short time to demo, but a data-source bug could directly create a false sell alert, and replay becomes fragile.

B. Pure, deterministic domain engine for accounting, contract identity and quote validation; impure adapters handle market data, notifications, AI and dashboards. Event log and replayable fixtures preserve provenance. More initial interface work; easier proof and safer failure behavior.

**Decision: B.** Price/target/quote/position calculations have no dependency on models, network, TradingView, or broker authentication. AI only provides research hypotheses and optional annotation, never overrides validity gates. For the first implementation we isolate the pure domain model before adding I/O.

## Product milestones

G01. Bootstrap, charter, architecture, tool policy and signed-DCO workflow. Exit: clean repo, signed commits, verified identity or documented signature limitation.

G02. Options position target calculator. Exit: fixed explicit contract multiplier, Decimal fees, net-profit target, strong negative tests and unit-test PASS.

G03. Contract-aware quote gate and alert state transitions. Exit: OPRA/qualified feed checking, time freshness, size, duplicate/partial close controls, alert audit; failure is abstention.

G04. TradingView Premium Pine v6 underlying alert baseline and data import. Exit: exported data reproducibility and explicit chart-vs-option limitations.

G05. Historical option quote quality study. Exit: validated rights, fields, timestamp alignment and fill simulation; indicative feed never treated as executable NBBO.

G06. Local UI + TesterArmy e2e against real journeys (new position, target hit, stale quote, partial exit, app restart, offline). Exit: real browser runs with assertions on the exact branch.

G07. Kronos/LightGBM benchmark and Jev bounded semantic tasks. Exit: walk-forward, independently held-out evaluation, no calibrated-probability claims without calibration.

G08. Paper-trading qualification. Exit: after-cost metrics, quote/alert latency and missed/dropped alert evidence. Launching with real capital is outside this roadmap and requires a separate explicit decision.

## Market-data policy

A quote is contract-specific: root, right, strike, expiration, multiplier, bid/ask, sizes, source, timestamp, venue/feed quality. A chart reaching the target price of the underlying NEVER becomes an executable option-bid sell alert. No bid, no validated contract, no sufficiently fresh quote, no alert. Use bid as a conservative sell reference, subject to size and venue liquidity; it is not a fill guarantee.

Historical tests model spread, market impact, queue/fill uncertainty, commissions, exchange fees, timezone and corporate-action adjustment. Price forecasts are not live orders and there is no promise of profitability.

## Tool / evidence gates

- **PStack:** use the upstream Cursor skills (architect, interrogate, reviews) as engineering process. Record two competing structures, failure modes, and chosen interfaces. Agent multi-model panels are NOT_RUN until genuinely invoked.
- **Jev:** true bounded judgment calls for classification of events or plan consistency only, with traceable state, options, model/provider and result. If authentication or network is absent, record NOT_RUN rather than simulate; never use it for financial formula correctness.
- **Alibaba OpenCodeReview:** run real review for the exact candidate diff via configured provider; include report and actionable findings. NOT_RUN is not PASS.
- **Graft:** build/check deterministic code graph and use it to trace impact of future changes; do not confuse it with a PR review substitute.
- **TesterArmy e2e:** test actual UI journeys on a built UI, not invented successful assertions. Pin engine versions and disable telemetry where appropriate.
- **Unit/property tests:** all arithmetic, stale timestamps, contract mismatches, zero quantity, malformed money, wrong feed, and fees.
- **Git governance:** signed-DCO commits, normal merge commits, no force-push/rebase, exact-head CI and tracked evidence.

## Required interfaces (G02/G03 sketch)

- OptionContract(contract identity, right, strike, expiry, deliverable multiplier)
- LongOptionPosition(contract, entry fill, open quantity, per-contract entry/exit fees)
- OptionQuote(contract identity, bid, ask, size, time, source, feed type)
- calculate_net_exit_pnl(position, bid)
- minimum_bid_for_net_profit(position, goal)
- assess_profit_target(position, quote, now, max_age, goal)

G02 contains only pure functions. G03 adds event-sourced alert dispatch and position revisions; the exit signal is always "REVIEW SELL TO CLOSE" until the person executes and reconciles with Derayah.

## Independent engineering risks / blockers

- Exact options quotes may require separately licensed OPRA feed; TradingView Premium is not a guaranteed option NBBO API.
- Derayah Global US-options API access is not verified; broker execution remains manual.
- Only a few months of SPCX trading history may be available; do not claim it covers full regime variability.
- Mac sleep/connection loss disables local monitoring; external phone notification is best effort, not broker-side stop protection.
- The repo begins empty; CI and reviewer setup are first-class, not assumed.
