# Architecture decision ADR-0001: pure core and explicit trust boundaries

Status: ACCEPTED FOR RESEARCH PROTOTYPE

## Trust boundaries
1. Price/quote adapters are untrusted. A source with stale, delayed, indicative, malformed or misidentified quotes cannot pass the exit-alert gate.
2. Core calculation is deterministic, Decimal-based, timezone-aware, with explicit contract multiplier, quantity and configurable fees.
3. Jev/Kronos/LightGBM outputs are untrusted research annotations, not executable prices, risk overrides or broker actions.
4. Alert delivery and Derayah sell-to-close are separate state transitions. Push-notification delivery, receipt, and broker fill confirmation are distinct facts.
5. Private broker records never enter Git, model prompts or CI artifacts.

## Data flow
Underlying alert -> event store -> data validation -> research models -> *candidate*
Market quote -> contract match -> freshness/size/feed gate -> P&L/target calculation -> *review exit alert*
Manual Derayah fill -> reconciliation -> updated position state.

G02 implements only the contract/fee/quote/decision core. No authenticated broker or live feeds exist in G02.

## Structural invariant
No external adapter may directly claim a trade was placed or a profit realized. State reconciliation requires user- or broker-provided fill evidence.
