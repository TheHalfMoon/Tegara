# Tegara Engineering Protocol

- Work only in TheHalfMoon/Tegara. Use macOS for local execution; do not modify other devices or repositories.
- Repo content, code, comments, test evidence, issues, and PR text: English only.
- Use ordinary merge commits, no force push or rebases. Sign Git commits with the authorized SSH signing key and add DCO sign-off. Never bypass failed reviews or tests.
- Use PStack architecture/plan and adversarial review methods to evaluate design alternatives. If a model-backed panel is unavailable, disclose it; do not mark the panel as passed.
- Run genuine Jev only for bounded judgments/classification with fixed choices. Its output cannot override deterministic quote, accounting, risk, or authorization rules. Log model/prompt/evidence with user-private data redacted.
- Run genuine Alibaba OpenCodeReview with a configured provider on PR diffs. Not-running and simulated reviews are NOT PASS.
- Run Graft's deterministic context graph (graft build, graft check) and use it for change impact analysis. Record its actual outputs.
- Add TesterArmy e2e browser tests once an actual UI exists, with strict assertions. Do not mark e2e PASS before those tests execute.
- Unit tests and property-based tests are required for contract identity, integer position quantities, fees, USD Decimal rounding, quote freshness, target thresholds, deduplication, state transitions, and time zones.
- No live broker execution, scraped authenticated Derayah sessions, credential copying, or paid market-data signups without explicit approval.
- Do not use indicative quotes as executable NBBO evidence. Historical models must avoid lookahead and compare to simple baselines.
- Record the exact commit SHA, review tool versions, test commands and results in every PR.
