# GOV-P01 — Read-only release gate audit

Current intended mode: RESEARCH ONLY. This tool does NOT confer independent
security-review approval, market data licensing, app notification delivery,
a broker fill, or permission to place any live option order.

Tegara has 16 stacked, unmerged draft pull requests as of 2026-10-10.
The topmost PR's green Python/browser/CodeQL status does not make the
earlier standalone PRs safe for live option-sell alerts or qualify the
entire product for release.

## Run from the existing Mac checkout

    cd ~/Projects/Tegara-Research
    PYTHONPATH=src python3 scripts/release_gate_audit.py \
      --repo TheHalfMoon/Tegara --pr 34

For a command that exits nonzero when the release is blocked:

    PYTHONPATH=src python3 scripts/release_gate_audit.py \
      --repo TheHalfMoon/Tegara --pr 34 --strict

Exit codes: 0 = read-only report generated without strict gating,
2 = report generated but strict live-release gate is blocked,
3 = GitHub unavailable/invalid/unstable response (no readiness claim).

The script needs an authenticated gh CLI in the Mac terminal. It performs
only read operations via gh pr view and commit-pinned/paginated gh api queries; it
does NOT inspect, download or log in to Derayah/TradingView, collect options
quotes, purchase products or trigger notifications. It saves no credentials.

## Semantics

- Validate exact types and OPEN state of the PR; fetch its head immediately
  before and after GitHub checks. If its SHA or metadata changes, invalidate
  the claim that CI passed the exact head.
- Read immutable commit-pinned GitHub check runs, not the mutable PR-check
  rollup, and require both the run's own head_sha and known app identity.
  Require completed SUCCESS from python-domain, browser-e2e, CodeQL
  aggregate (GitHub Advanced Security), CodeQL Python and JavaScript.
  A truncated check-run page fails closed.
- Paginate the REST open-PR inventory in complete pages of 100 and validate
  every entry, including each SHA, open state and draft flag; never mistake a
  truncated first page for a complete zero-draft inventory.
- The third-party
  CodeRabbit check is deliberately not proof of code review: it may
  report green while skipping a draft PR.
- Record open draft PR count. Unmerged drafts remain a release blocker
  even when the newest head has green CI.
- Do not elevate native Alibaba OpenCodeReview, OPRA market-data entitlement,
  option last-trade metadata, Pine Editor compilation, phone delivery,
  actual Derayah manual trade reconciliation, or prospective paper trading
  to verified. All are NOT_INDEPENDENTLY_VERIFIED in this read-only tool.
- A PASS software_ci field NEVER changes overall release_status:
  BLOCKED_RESEARCH_ONLY is the only supported result. There is no
  caller-supplied override that can fabricate independent evidence.
- A future transition to live options alerts requires a separately
  reviewed governance change and actual evidence from authoritative
  sources. This report cannot itself approve a live release.

## Test evidence

Synthetic GitHub fixture tests cover stable/unstable heads, pending
checks, per-check mismatched SHAs, unexpected apps, truncated check pages,
second-page draft inventory, skipped third-party review, malformed types,
strict nonzero exit and green CI with missing external evidence.
The integration invocation against GitHub is read-only and can verify
only GitHub state, not vendor/OPRA/broker reality.

This tool prevents a false completion claim; it does not automate a
merge or brokerage execution. Native Alibaba OCR review is a separate,
still-open gate from exact-head CI qualification.
