# LM-P01 — Local macOS Notification Demonstration

Status: LOCAL DEMONSTRATION ONLY. The deterministic option target formula
may be exercised without any external feed. There is no actual Sell-to-Close
real-time notification feature, phone push, trading API or order execution.

This module is intentionally separate from the position outbox and cannot
mark real provider/phone delivery or broker fills.

## Preview a manual target message (no OS popup)

From the Tegara repository root:

    PYTHONPATH=src python3 -m tegara.local_notify_cli \
      --symbol SPY --right C --strike 600 \
      --expiration 2026-10-09 --multiplier 100 \
      --entry 1.20 --quantity 1 --entry-fee 2 --exit-fee 2 \
      --net-profit 30 --manual-bid 2.00 --preview

The dates/quotes are illustrative and are NOT actual market prices.
Amounts are user-provided and must be adjusted to actual contract
deliverables, fees and any applicable order/exchange rules.

## Explicitly test Notification Center on your Mac

    PYTHONPATH=src python3 -m tegara.local_notify_cli \
      --symbol SPY --right C --strike 600 \
      --expiration 2026-10-09 --multiplier 100 \
      --entry 1.20 --quantity 1 --entry-fee 2 --exit-fee 2 \
      --net-profit 30 --manual-bid 2.00 \
      --send-local --acknowledge-demo-only

One explicit command requests one local macOS Notification Center popup
using AppleScript. It does NOT poll a market feed or run in the background.
A successful osascript process exit only shows local request acceptance;
Focus modes and macOS notification permissions may prevent visible display.
The program therefore always outputs:

- market_data_verified: false
- phone_delivery_verified: false
- broker_execution_verified: false
- status: LOCAL_MANUAL_RESEARCH_DEMO

It never reports the user took action or the position was sold.

## Safety architecture

- Fixed AppleScript program with user text only in process argv; no
  shell interpolation or remote URL, token or credential access.
- A local OS request requires both --send-local and the explicit
  --acknowledge-demo-only marker.
- Invalid Decimal values (negative, NaN, Infinity), malformed contract
  identities and unlabelled "sell now" messages are rejected.
- Unit tests fake the AppleScript process and NEVER trigger real OS popups.
- No automatic retry, macOS background daemon, ntfy/Telegram/Apple Push
  integration or public endpoint is created.

## Actual target sell alert prerequisites

To convert this demo into real option quote alerts, Tegara requires:
licensed contract-specific live NBBO quality data with feed proof, correct
contract identity, bid depth, quote age, source market last trading time,
a fail-closed notification engine with real provider acknowledgements and
device receipt tests, and user-led Derayah sell-to-close execution. These
remain unqualified and outside this feature.
