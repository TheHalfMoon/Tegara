# UI-P01 — Local Target Calculator UI

Status: IMPLEMENTED ON DEVELOPMENT BRANCH, NOT QUALIFIED FOR LIVE OPTIONS TRADING.

## Browser workflow

Run on macOS (Python >=3.11):

    PYTHONPATH=src python3 -m tegara.dashboard --port 8765

Open http://127.0.0.1:8765 in your browser. The app is bound only to the loopback interface and does not contact TradingView, Derayah Global, broker APIs, or market-data feeds.

This prototype calculates a hypothetical option bid target from:
- Explicit symbol, call/put right, strike, expiration, multiplier and quantity.
- User-entered purchase premium and buy/sell fees per contract.
- Net profit in USD or net percentage of entry debit.
- Explicit tick size, with an upward ceiling to ensure threshold sufficiency.

It displays initial debit, theoretical bid threshold, rounded bid target, and estimated net P&L. Example form values are synthetic (not current prices or fees). No position is saved through this UI and no order or actual mobile notification can be generated.

## Security and data boundaries

- The Python HTTP listener binds only to 127.0.0.1. Accepted Host and POST Origin values must match its local port. There is no external network dependency.
- JSON POST payloads are limited to 16 KiB and an exact documented field set. Unknown fields are rejected. Values are parsed using Decimal, not floating-point arithmetic. Tick inputs must be bounded within 0.00000001 to 1000000: extreme decimal exponents can overflow rounding arithmetic. Invalid values return a controlled JSON HTTP 422 rather than disconnecting the request. These are implementation bounds, not exchange-approved contract increments.
- No brokerage credentials, cloud analytics, account numbers, market-quote adapters, trading endpoints, websocket market streams or polling workers exist.
- CSP blocks remote scripts, forms, fonts and images; static CSS/JS is served from the package itself. Requests are never logged with financial details.
- The app has no authentication because it is strictly an offline local prototype. It should not be published or exposed via a public tunnel in its current state.
- The option tick size must be verified against the actual contract/exchange before acting on any estimate.
- A hypothetical calculated bid is not an option's actual market Bid and is not a sell-to-close signal.

## PStack boundary decision

Alternative A: FastAPI + third-party UI dependencies and a mutable broker-facing dashboard early. Pros: flexibility. Cons: increased install surface, hidden authentication expectations, unsafe temptation to add order endpoints.

Alternative B: Python standard-library loopback server with a single pure target calculator endpoint, static UI, strict origin/host checks and no writes. Pros: cheap, deterministic, low attack surface, no paid services or implied brokerage integration. Cons: it is not yet a full portfolio dashboard.

Chosen: B, for UI-P01. Future real position manager will require an authenticated local session and a distinct explicit design for audit, live feed, data licensing, broker reconciliation and reliable notification delivery.

## Verification and release gate

- Python unittest covers input validation, multiplier and fees, local origin/host blocking, health state and absence of an order endpoint.
- TesterArmy e2e (actual Chromium, no AI model) covers research-only banner, calculation accuracy, no live price assertions and invalid form state.
- Run: E2E_TELEMETRY_DISABLED=1 npm run test:e2e -- --workers 1.
- Note: e2e installs Playwright browser binaries on first run (local storage, no paid cloud compute).
- Remaining: exact-head CI, independent Alibaba OCR review, qualified option data and contractual last-trading-time rules. Jev is only an optional bounded semantic check.

No stage involving real capital can be considered completed by this dashboard.
