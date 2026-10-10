"""Synthetic GitHub responses: release readiness must never be invented."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import unittest

from scripts.release_gate_audit import (
    REQUIRED_CHECKS, EXTERNAL_GATES, audit, main,
)

SHA = "cf7de74ffeee986bcbf08e00497ab7dcff74721a"


def fixture(*, missing=None, draft=True, head_changed=False, only_skipped=False):
    state = {"head_requests": 0}

    def response(argv):
        if argv[0:2] == ["pr", "view"]:
            state["head_requests"] += 1
            return {
                "number": 34,
                "headRefOid": ("a" * 40 if head_changed and state["head_requests"] == 2 else SHA),
                "baseRefName": "feat/latency-aware-options-replay",
                "isDraft": draft,
            }
        if argv[0:2] == ["pr", "checks"]:
            checks = [
                {"name": "CodeRabbit", "state": "SUCCESS", "bucket": "pass"}
            ]
            if not only_skipped:
                checks += [
                    {"name": name,
                     "state": "PENDING" if name == missing else "SUCCESS",
                     "bucket": "pending" if name == missing else "pass"}
                    for name in REQUIRED_CHECKS
                ]
            return checks
        if argv[0:2] == ["pr", "list"]:
            return [{"number": 34, "isDraft": draft, "headRefOid": SHA},
                    {"number": 2, "isDraft": True, "headRefOid": "b" * 40}]
        raise AssertionError(argv)
    return response


class GateAuditTests(unittest.TestCase):
    def test_successful_ci_is_not_live_release(self):
        result = audit(invoke=fixture())
        self.assertEqual(result["software_ci"], "PASS")
        self.assertEqual(result["head_sha"], SHA)
        self.assertEqual(result["open_draft_pull_requests"], 2)
        self.assertEqual(result["release_status"], "BLOCKED_RESEARCH_ONLY")
        self.assertFalse(result["live_options_trading_authorized"])
        self.assertFalse(result["phone_options_sell_alert_qualified"])
        self.assertFalse(result["broker_order_submitted"])
        for gate in EXTERNAL_GATES:
            self.assertIn(gate, result["blockers"])
            self.assertEqual(result["native_independent_review_and_external_evidence"][gate],
                             "NOT_INDEPENDENTLY_VERIFIED")

    def test_single_pending_check_blocks_ci(self):
        result = audit(invoke=fixture(missing="CodeQL python"))
        self.assertEqual(result["software_ci"], "NOT_VERIFIED_OR_INCOMPLETE")
        self.assertEqual(result["required_checks"]["CodeQL python"], "NOT_PASS")
        self.assertIn("CI_EXACT_HEAD_NOT_PASS", result["blockers"])

    def test_skipped_coderabbit_check_does_not_count_as_review_or_ci(self):
        result = audit(invoke=fixture(only_skipped=True))
        self.assertNotEqual(result["software_ci"], "PASS")
        self.assertIn("independent_alibaba_ocr_exact_head_review", result["blockers"])

    def test_head_change_during_ci_lookup_invalidates_checks(self):
        result = audit(invoke=fixture(head_changed=True))
        self.assertFalse(result["github_head_consistent_during_audit"])
        self.assertIsNone(result["head_sha"])
        self.assertIn("PR_HEAD_CHANGED_RECHECK", result["blockers"])
        self.assertNotEqual(result["software_ci"], "PASS")

    def test_not_draft_does_not_make_live_ready(self):
        result = audit(invoke=fixture(draft=False))
        self.assertNotIn("PR_IS_DRAFT", result["blockers"])
        self.assertEqual(result["release_status"], "BLOCKED_RESEARCH_ONLY")

    def test_incorrect_repo_locator_rejected(self):
        with self.assertRaisesRegex(ValueError, "locator"):
            audit(repo="TheHalfMoon/Tegara; rm -rf ~", invoke=fixture())

    def test_strict_exits_nonzero_even_when_software_ci_passes(self):
        output = StringIO()
        with redirect_stdout(output):
            status = main(["--strict"], invoke=fixture())
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())["software_ci"], "PASS")

    def test_unknown_check_duplicate_does_not_mask_missing_expected_check(self):
        def duplicate(argv):
            result = fixture()(argv)
            if argv[0:2] == ["pr", "checks"]:
                result.append({"name": "CodeQL python", "state": "SUCCESS", "bucket": "pass"})
            return result
        result = audit(invoke=duplicate)
        self.assertEqual(result["required_checks"]["CodeQL python"], "NOT_PASS")


if __name__ == "__main__":
    unittest.main()
