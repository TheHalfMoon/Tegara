# G03 evidence and remaining gates

Machine: macbook, 2026-10-09. Repository: TheHalfMoon/Tegara. This is a paper-only research prototype.

## Evidence from executed tools

- Python 3.13.5 unittest: 47/47 PASS, including atomic parallel fills, restart deduplication, expiry-date rejection, invalid feed/quote rejection, pending-alert invalidation after a manual fill, explicit-fee and tick-size CLI cases.
- Graft 0.21.1: deterministic build and check PASS (latest pass must be refreshed after final code changes). The optional deep meaning graph was not built.
- Jev 2026.919.0 / model jev-1.13.0: two successful real bounded-choice judgments using synthetic states:
  - Underlying reached target but option quote was indicative: selected 'underlying_only', not 'sell'.
  - One contract was manually closed before delivery of an alert covering three: selected 'invalidate', not 'deliver'.
  These are semantic checks only, NOT formal verification or trade-profit probabilities.
- Signed commit 5909d6303291c94a4bc7c5ff047ace1f93423119: local GIT SSH Good signature and GitHub verification valid; DCO sign-off present. Any successor commit needs its own exact-head verification.
- CI for PR #4 / SHA 5909d63: python-domain PASS on GitHub Actions.

## Review work requiring qualification

- Alibaba OpenCodeReview: prior 3B local model connected but its tool-call roundtrip was unverified. A first review with a 8,000-token budget failed before review (0 tokens, budget exceeded). A subsequent bounded attempt using the same small local model failed after 3m2s with zero tokens/finding results across 3 selected files, session dd6ac78f-d792-4dc7-82c2-2dacf79cd926. A qwen3:4b local pull was attempted but did not result in an installed usable model. OCR review is NOT_PASS; no independent review is claimed. **Independent review status: NOT_PASS** until a meaningful findings report covers the exact PR SHA.
- PStack: upstream architect/interrogate skills consulted; two structures and boundary rationale in PLAN.md. Genuine multi-model panel NOT_RUN; not called PASS.
- TesterArmy e2e: NOT_RUN because no browser UI exists. UI qualification requires live tested browser journeys, not simulated traces.
- Jev: now authenticated and called, but no live broker credentials, position data or option pricing was passed to it.
- Historical parent commits 74bfc39 and aa60199 show GitHub unknown_key despite local Good SSH signatures. New commits are properly verified after setting repository-local Git author identity to the TheHalfMoon profile; no history rewriting is authorized.

## Hard block on live use

No independently qualified live option NBBO feed, exact contract session cutoff, mobile delivery infrastructure, broker integration, or after-cost trading strategy validation exists. The string EXACT_NBBO in test fixtures is not a source authentication mechanism. There is no real option monitoring and no real broker order submission.
