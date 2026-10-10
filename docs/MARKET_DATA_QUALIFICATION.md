# Tegara Market Data and Derayah Access Qualification — 2026-10-10

Status: External sources reviewed; account-specific OPRA entitlements, broker
API access, option quote timestamp latency, usage rights, and live delivery
are NOT VERIFIED. This document does not create or purchase subscriptions.

## Free/previously paid assets that Tegara can use NOW

1. Existing TradingView Premium account: chart-based underlying research,
   user-exported OHLCV CSV and on-platform Pine indicator alerts.
2. Existing Derayah Global broker: user can manually inspect its own options
   chain and place orders according to the account's eligible instruments.
   No account credential, API, fill stream or data entitlement is assumed.
3. Local Mac: no paid server required for pinned Kronos-small research CPU
   inference, Python historical replay prototypes, SQLite, and OS demo alerts.
4. Graft, Jev, TesterArmy e2e and deterministic CI for software qualification.

TradingView's help says exported CSV includes chart data currently loaded
in Supercharts and can require scrolling backward to load older bars.
Exported charts are not by default a historical OPRA NBBO tape. This
difference matters for the 0DTE long-option Bid and Ask backtester.
SOURCE: https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/

## Avoid accidentally paying twice

TradingView's current official guidance states that paid plan subscription
fees do NOT automatically cover real-time exchange data entitlements.
SOURCE: https://www.tradingview.com/support/solutions/43000471705-how-to-purchase-additional-market-data/

TradingView supports broker subscription verification for specific
broker/exchange pairs. The official table currently lists Derayah for
TADAWUL only (7-day verification); it does NOT list Derayah as a verified
OPRA source. Another broker's OPRA eligibility does not prove that the
user's Derayah Global account is likewise eligible.
SOURCE: https://www.tradingview.com/support/solutions/43000479666-how-can-i-get-real-time-data-from-exchanges-that-i-have-already-purchased-with-my-broker/

TradingView Options Chain displays, among other fields, best bid/ask,
contract-specific expiration, strike, Greeks and implied volatility.
Its visible quote snapshot is not equivalent to an exportable timestamped
historical quote stream with per-event marketability and exchange rights.
SOURCE: https://www.tradingview.com/support/solutions/43000760837-options-chain-overview/

## Proper option quote research data

Cboe DataShop's Option Quote Intervals is an official possible dataset
for 1-minute or custom intervals. It describes contract NBBO Bid/Ask and
quote size, with optional Greeks/IV. Its 1-minute historical/interval
product is a market data purchase/supply route, NOT a free live OPRA API
or guaranteed zero-cost capability. All use requires license and rights
verification before downloading data into Tegara.
SOURCE: https://datashop.cboe.com/option-quote-intervals

Alternatives may exist but need explicit verification of NBBO semantics,
rate limits, timestamps, symbol mapping, historical availability, minimum
contract size, feed latency, license, monthly cost and legal retention.

## Derayah Global commissions

Derayah's official Global platform lists U.S. stock/ETF and options access
and, currently, an options contract schedule of USD 1.99 per contract for
its listed account categories. Actual applicable fees, VAT, regulatory
charges and individual account plan should be obtained from the user's
broker trade confirmation rather than assumed or hard-coded into a model.
SOURCES:
https://web.derayah.com/en/platforms/derayah-global/
https://web.derayah.com/en/prices-fees/

No authenticated options order API, individual contract quote streaming
endpoint or push execution feed for the user's Derayah Global account was
independently verified. Never invent API credentials or available operations.

## Implementation and staged release gate

Stage A (zero new market data spend): use TradingView OHLCV CSV exports;
run Kronos vs persistence, ORB/VWAP underlying baselines, and safe
research-only Mac target demo with manually entered option Bid.

Stage B (paper options quote qualification): use one legally obtained
OPRA-compatible historical 1-minute quote interval sample with permission
for local storage. Verify trading venue, exact right/strike/expiry/multiplier,
NBBO Bid/Ask size, exchange last trading cutoff, timestamp age, option quote
gap and price tick. Analyze buy-at-Ask/sell-at-Bid net fees, slippage and
missingness. Do NOT purchase data until the user authorizes a specific cost.

Stage C (live research alerts): only after a verified real-time data adapter
and permitted device notification transport are available. Confirm local
latency/heartbeat, delivery logs, broker/manual fill reconciliation and
hours/early closes. Do NOT enable automatic real brokerage order submission.

Stage D (qualification): rolling date-based evaluation and prospective paper
sessions, positive outcome vs a simpler model after costs, and actual
independent Alibaba OpenCodeReview exact-head review.

No release-status downgrade, skipped governance gates or fabricated vendor
capability is permitted simply to advance the project completion percentage.
