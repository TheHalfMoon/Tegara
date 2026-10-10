# SEC-P01 — GitHub CodeQL Static Security Analysis

Tegara uses a GitHub Actions CodeQL security-extended query suite on all
Pull Requests, plus changes to main. The matrix scans Python and
JavaScript/TypeScript. Existing deterministic Python unit tests, the
TesterArmy e2e browser suite, Ruff and Graft remain separate checks.

## Why use this

- Identify potentially vulnerable source-code paths in repository changes.
- Require exact-head CI evidence in each draft PR prior to promotion/merge.
- Preserve local Mac resources; scanning runs on GitHub-hosted CI.
- For this PUBLIC repository, GitHub currently makes CodeQL code scanning
  available without a GitHub Code Security enterprise purchase.
  https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configure-code-scanning

## Security limitations

- CodeQL results can have false positives and false negatives.
- CodeQL is NOT an independent Alibaba OpenCodeReview review or a human
  audit. The OCR governance gate remains OPEN until real review is done.
- CodeQL cannot prove authenticity, timeliness, or licensing of data tagged
  EXACT_NBBO in synthetic source CSVs.
- CodeQL cannot prove options execution, phone alert delivery, broker fill,
  OPRA data rights, profitability, or Pine v6 compilation inside TradingView.
- New external Actions or package sources should be pinned to immutable
  digests in a later separate supply-chain hardening stage.
- Never add secrets, brokerage API tokens or live account data to CI.

## Evidence expected

Two CodeQL analysis jobs (Python and JavaScript/TypeScript) must have
an actual successful GitHub Actions run on the exact candidate head.
The mere presence of .github/workflows/codeql.yml is NOT a PASS.
