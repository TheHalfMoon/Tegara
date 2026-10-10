"""Synthetic GitHub responses: no trust upgrade from mutable/partial evidence."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import unittest

from scripts.release_gate_audit import (
    REQUIRED_CHECKS, EXTERNAL_GATES, audit, main,
)

SHA = "171088bcfdb6cd52cb0cc9fcfb6211b055f5da6e"
OTHER = "a" * 40
APPS = {name: ("github-advanced-security" if name == "CodeQL"
               else "github-actions") for name in REQUIRED_CHECKS}


def fixture(*, missing=None, draft=True, head_changed=False, only_skipped=False,
            wrong_sha=False, wrong_app=False, partial_meta=False,
            second_page_draft=False, incomplete_checks=False):
    state = {"head_requests": 0}

    def response(argv):
        if argv[:2] == ["pr", "view"]:
            state["head_requests"] += 1
            return {
                "number": 34.0 if partial_meta else 34,
                "headRefOid": (
                    OTHER if head_changed and state["head_requests"] == 2 else SHA
                ),
                "baseRefName": "feat/latency-aware-options-replay",
                "isDraft": draft, "state": "OPEN",
            }
        if argv[0] == "api" and "/check-runs?" in argv[1]:
            checks = [] if only_skipped else [
                {
                    "name": name,
                    "status": "queued" if name == missing else "completed",
                    "conclusion": None if name == missing else "success",
                    "head_sha": OTHER if wrong_sha and name == "CodeQL python" else SHA,
                    "app": {"slug": "third-party" if wrong_app and name == "CodeQL"
                            else APPS[name]},
                } for name in REQUIRED_CHECKS
            ]
            return {"total_count": len(checks) + int(incomplete_checks),
                    "check_runs": checks}
        if argv[0] == "api" and "/pulls?" in argv[1]:
            if second_page_draft:
                if "&page=1" in argv[1]:
                    return [
                        {"number": n, "draft": False, "state": "open",
                         "head": {"sha": SHA if n == 34 else f"{n:040x}"}}
                        for n in range(1, 101)
                    ]
                return [{"number": 101, "draft": True, "state": "open",
                         "head": {"sha": OTHER}}]
            return [
                {"number": 34, "draft": draft, "state": "open",
                 "head": {"sha": SHA}},
                {"number": 2, "draft": True, "state": "open",
                 "head": {"sha": OTHER}},
            ]
        raise AssertionError(argv)
    return response


class GateAuditTests(unittest.TestCase):
    def test_complete_pinned_ci_is_not_live_release(self):
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

    def test_pending_check_blocks_ci(self):
        result = audit(invoke=fixture(missing="CodeQL python"))
        self.assertEqual(result["software_ci"], "NOT_VERIFIED_OR_INCOMPLETE")
        self.assertEqual(result["required_checks"]["CodeQL python"], "NOT_PASS")

    def test_no_required_checks_never_counts_skipped_review(self):
        result = audit(invoke=fixture(only_skipped=True))
        self.assertNotEqual(result["software_ci"], "PASS")
        self.assertIn("independent_alibaba_ocr_exact_head_review", result["blockers"])

    def test_changed_head_invalidates_checks(self):
        result = audit(invoke=fixture(head_changed=True))
        self.assertFalse(result["github_head_consistent_during_audit"])
        self.assertIsNone(result["head_sha"])
        self.assertIn("PR_HEAD_CHANGED_RECHECK", result["blockers"])

    def test_commit_pinned_run_must_match_exact_sha(self):
        result = audit(invoke=fixture(wrong_sha=True))
        self.assertEqual(result["required_checks"]["CodeQL python"], "NOT_PASS")
        self.assertNotEqual(result["software_ci"], "PASS")

    def test_check_must_have_expected_authorized_app(self):
        result = audit(invoke=fixture(wrong_app=True))
        self.assertEqual(result["required_checks"]["CodeQL"], "NOT_PASS")

    def test_incomplete_commit_check_page_blocks_ci(self):
        result = audit(invoke=fixture(incomplete_checks=True))
        self.assertEqual(result["software_ci"], "NOT_VERIFIED_OR_INCOMPLETE")
        self.assertIn("COMMIT_CHECK_LIST_INCOMPLETE", result["blockers"])

    def test_paginated_pr_inventory_finds_draft_on_second_page(self):
        result = audit(invoke=fixture(draft=False, second_page_draft=True))
        self.assertEqual(result["open_pull_requests"], 101)
        self.assertEqual(result["open_draft_pull_requests"], 1)
        self.assertIn("OPEN_UNMERGED_DRAFT_STACK", result["blockers"])

    def test_malformed_float_pr_number_does_not_pass_schema(self):
        with self.assertRaisesRegex(ValueError, "malformed"):
            audit(invoke=fixture(partial_meta=True))

    def test_no_target_in_list_does_not_claim_zero_drafts(self):
        def missing_target(argv):
            result = fixture()(argv)
            if argv[0] == "api" and "/pulls?" in argv[1]:
                return [row for row in result if row["number"] != 34]
            return result
        with self.assertRaisesRegex(ValueError, "missing"):
            audit(invoke=missing_target)

    def test_not_draft_is_still_release_blocked(self):
        result = audit(invoke=fixture(draft=False))
        self.assertNotIn("PR_IS_DRAFT", result["blockers"])
        self.assertEqual(result["release_status"], "BLOCKED_RESEARCH_ONLY")

    def test_invalid_repo_path_rejected(self):
        with self.assertRaisesRegex(ValueError, "locator"):
            audit(repo="TheHalfMoon/Tegara;rm -rf ~", invoke=fixture())

    def test_strict_exit_nonzero_despite_green_ci(self):
        out = StringIO()
        with redirect_stdout(out):
            status = main(["--strict"], invoke=fixture())
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(out.getvalue())["software_ci"], "PASS")

    def test_duplicate_required_checks_cannot_pass(self):
        def duplicate(argv):
            value = fixture()(argv)
            if argv[0] == "api" and "/check-runs?" in argv[1]:
                entry = next(row for row in value["check_runs"]
                             if row["name"] == "CodeQL python")
                value["check_runs"].append(entry)
                value["total_count"] += 1
            return value
        result = audit(invoke=duplicate)
        self.assertEqual(result["required_checks"]["CodeQL python"], "NOT_PASS")


if __name__ == "__main__":
    unittest.main()
