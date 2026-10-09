# Quality evidence for founding PR

Date: 2026-10-09, machine: macbook (macOS 26.6.2).

- Python 3.13.5: 17 domain tests passed using PYTHONPATH=src python3 -m unittest discover -s tests -v.
- Graft v0.21.1: deterministic graph built, 37 nodes and 76 edges; graph check OK. Concept/meaning layer NOT_BUILT, not falsely called PASS.
- Jev v2026.919.0: bounded architecture judgment attempted, but tool returned "Not logged in; run jev login". Result NOT_RUN / AUTH_REQUIRED. No verdict fabricated.
- Alibaba OpenCodeReview v1.12.13: local Ollama provider test reached qwen2.5-coder:3b, connection OK; tool-call round-trip unverified. Full code review status must be recorded separately after it finishes, if it succeeds.
- TesterArmy e2e: NOT_APPLICABLE to G02 (no UI exists yet). Required for G06 before declaring UI ready.
- PStack: upstream Cursor architect and interrogate methodologies read. Competing architectures and selected decision recorded in PLAN.md. Parallel multi-model review panel NOT_RUN; no model-based endorsement claimed.
- Git: local SSH signing and DCO used; initial GitHub remote commit verification returned unknown_key despite local key verification. Remote verification is a separate unresolved gate, not to be mislabeled verified.

## Review completion rule

No PR may be described as fully qualified or automatically merged unless exact-head CI, full independent OCR findings, required tests, signing and governance checks are confirmed; all nonruns and blockers must remain visible.
