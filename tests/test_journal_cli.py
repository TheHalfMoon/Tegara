"""Offline user-declared journal CLI regression, synthetic and no broker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ManualJournalCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "paper-trades.sqlite"
        self.root = Path(__file__).resolve().parents[1]

    def cli(self, *args, db=None):
        return subprocess.run(
            [sys.executable, "-m", "tegara.journal_cli",
             "--db", str(self.db if db is None else db), *args],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(self.root / "src")},
            capture_output=True, text=True,
        )

    def open_args(self, *extra):
        return (
            "open", "--position-id", "p-001",
            "--symbol", "SPY", "--right", "C",
            "--strike", "600", "--expiration", "2026-10-09",
            "--entry", "1.20", "--quantity", "3",
            "--entry-fee", "2.29", "--exit-fee", "2.29",
            "--opened-at", "2026-10-09T10:30:00-04:00",
            *extra,
        )

    def close_args(self, *extra):
        return (
            "manual-close", "--position-id", "p-001",
            "--fill-id", "manual-confirm-01",
            "--quantity", "1", "--fill-price", "1.62",
            "--fee-per-contract", "2.29",
            "--filled-at", "2026-10-09T10:35:00-04:00",
            *extra,
        )

    def create(self):
        p = self.cli(*self.open_args("--record-only"))
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_journey_record_open_partial_close_and_repeat_idempotent(self):
        start = self.create()
        self.assertEqual(start["quantity_open"], 3)
        self.assertEqual(start["position_status"], "OPEN")
        self.assertFalse(start["broker_execution_verified"])
        after = self.cli(*self.close_args("--record-only"))
        self.assertEqual(after.returncode, 0, after.stderr)
        result = json.loads(after.stdout)
        self.assertTrue(result["newly_recorded"])
        self.assertEqual(result["quantity_open"], 2)
        self.assertEqual(result["position_status"], "PARTIALLY_CLOSED")
        self.assertEqual(result["manual_fill_net_pnl_usd"], "37.42")
        again = self.cli(*self.close_args("--record-only"))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(json.loads(again.stdout)["newly_recorded"])
        snapshot = self.cli("show", "--position-id", "p-001")
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        self.assertEqual(json.loads(snapshot.stdout)["realized_net_pnl_user_declared_usd"],
                         "37.42")
        fills = self.cli("fills", "--position-id", "p-001")
        self.assertEqual(len(json.loads(fills.stdout)["events"]), 1)
        self.assertFalse(json.loads(fills.stdout)["broker_execution_verified"])

    def test_writes_require_manual_record_flag(self):
        p = self.cli(*self.open_args())
        self.assertEqual(p.returncode, 2)
        self.assertFalse(self.db.exists())
        self.assertIn("--record-only", p.stderr)
        self.create()
        failed = self.cli(*self.close_args())
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(self.cli(
            "show", "--position-id", "p-001"
        ).stdout)["quantity_open"], 3)

    def test_nonexistent_read_never_creates_empty_database(self):
        p = self.cli("show", "--position-id", "missing")
        self.assertEqual(p.returncode, 2)
        self.assertFalse(self.db.exists())
        self.assertIn("does not exist", p.stderr)

    def test_duplicate_position_cannot_overwrite(self):
        self.create()
        again = self.cli(*self.open_args("--record-only"))
        self.assertNotEqual(again.returncode, 0)
        snap = json.loads(self.cli("show", "--position-id", "p-001").stdout)
        self.assertEqual(snap["quantity_open"], 3)

    def test_oversell_denied_no_mutation(self):
        self.create()
        args = list(self.close_args("--record-only"))
        args[args.index("--quantity") + 1] = "4"
        self.assertNotEqual(self.cli(*args).returncode, 0)
        self.assertEqual(json.loads(self.cli(
            "show", "--position-id", "p-001"
        ).stdout)["quantity_open"], 3)

    def test_conflicting_duplicate_fill_denied(self):
        self.create()
        self.assertEqual(self.cli(*self.close_args("--record-only")).returncode, 0)
        args = list(self.close_args("--record-only"))
        args[args.index("--fill-price") + 1] = "1.90"
        self.assertNotEqual(self.cli(*args).returncode, 0)
        self.assertEqual(len(json.loads(self.cli(
            "fills", "--position-id", "p-001"
        ).stdout)["events"]), 1)

    def test_incorrect_ordered_timestamps_denied(self):
        self.create()
        args = list(self.close_args("--record-only"))
        args[args.index("--filled-at") + 1] = "2026-10-09T10:29:00-04:00"
        self.assertEqual(self.cli(*args).returncode, 2)

    def test_naive_timestamp_denied(self):
        self.create()
        # A naïve timestamp on a different position is rejected.
        args = list(self.open_args("--record-only"))
        args[args.index("--position-id") + 1] = "p-naive"
        args[args.index("--opened-at") + 1] = "2026-10-09T10:30:00"
        self.assertEqual(self.cli(*args).returncode, 2)

    def test_invalid_amounts_denied(self):
        for value in ("NaN", "Infinity", "-1", "bad"):
            with self.subTest(value=value):
                args = list(self.open_args("--record-only"))
                args[args.index("--entry") + 1] = value
                self.assertEqual(self.cli(*args).returncode, 2)
        self.assertFalse(self.db.exists())

    def test_database_outside_repo_required(self):
        inside = self.root / "test-journal-do-not-create.sqlite"
        p = self.cli(*self.open_args("--record-only"), db=inside)
        self.assertEqual(p.returncode, 2)
        self.assertFalse(inside.exists())
        self.assertIn("outside Tegara repository", p.stderr)

    def test_symlink_database_refused(self):
        existing = Path(self.tmp.name) / "not-trades.txt"
        existing.write_text("do not touch")
        link = Path(self.tmp.name) / "redirect.sqlite"
        link.symlink_to(existing)
        p = self.cli(*self.open_args("--record-only"), db=link)
        self.assertEqual(p.returncode, 2)
        self.assertIn("symlink", p.stderr)
        self.assertEqual(existing.read_text(), "do not touch")

    def test_private_database_file_permissions(self):
        self.create()
        if os.name == "posix":
            self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_human_fill_does_not_claim_trading_proof(self):
        self.create()
        p = self.cli(*self.close_args("--record-only"))
        out = json.loads(p.stdout)
        self.assertFalse(out["broker_execution_verified"])
        self.assertFalse(out["statement_independently_verified"])
        self.assertEqual(out["evidence_status"], "USER_DECLARED_UNVERIFIED")
        self.assertFalse(out["live_trading_enabled"])


if __name__ == "__main__":
    unittest.main()
