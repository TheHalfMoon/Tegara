"""Synthetic date-scoped walk-forward regression tests; no real market data."""
import csv
from hashlib import sha256
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tegara.underlying_research import (
    ResearchParameters, load_bars, load_cutoffs, walk_forward, report_json,
)


class FutureStub:
    """Deterministic fixed close chosen without looking at x/y actual outcomes."""
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        import pandas as pd
        self.calls.append(kwargs)
        return pd.DataFrame(
            {"close": [101.0] * kwargs["pred_len"]},
            index=kwargs["y_timestamp"],
        )


class UnderlyingResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.path = root / "underlying.csv"
        self.cutoff_path = root / "cutoffs.csv"
        self.start = datetime(2026, 10, 7, 13, 30, tzinfo=timezone.utc)
        self.sessions = [self.start + timedelta(days=i) for i in range(3)]
        self.rows = []
        for first in self.sessions:
            for i in range(50):
                self.rows.append({
                    "timestamp": (first + timedelta(minutes=i)).isoformat(),
                    "open": "100.0", "high": "101.2", "low": "99.9",
                    "close": "100.5", "volume": "1000",
                })
        self.cutoffs = [{
            "session_date": first.date().isoformat(),
            "last_trading_at": (first + timedelta(hours=6)).isoformat(),
        } for first in self.sessions]
        self.write()
        self.args = ResearchParameters(1, 32, 3, 7, 1, 12)

    def write(self, *, rows=None, cutoffs=None):
        with self.path.open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=("timestamp", "open", "high", "low", "close", "volume"))
            writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)
        with self.cutoff_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=("session_date", "last_trading_at"))
            writer.writeheader()
            writer.writerows(self.cutoffs if cutoffs is None else cutoffs)

    def parsed(self):
        bars, bar_digest = load_bars(self.path)
        cutoffs, cutoff_digest = load_cutoffs(self.cutoff_path)
        return bars, cutoffs, bar_digest, cutoff_digest

    def command(self, *extra):
        root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, str(root / "scripts/walkforward_research.py"),
             "--csv", str(self.path), "--cutoffs", str(self.cutoff_path),
             "--interval-minutes", "1", "--lookback", "32", "--horizon", "3",
             "--stride", "7", "--holdout-sessions", "1", *extra],
            cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src")},
            capture_output=True, text=True,
        )

    def test_cli_inspect_only_no_model_or_network(self):
        run = self.command("--inspect-only")
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["status"], "OFFLINE_UNDERLYING_AUDIT_ONLY")
        self.assertTrue(report["no_model_was_loaded"])
        self.assertEqual(report["bars"], 150)
        self.assertFalse(report["options_execution_validated"])

    def test_cli_forecast_requires_upstream_checkout(self):
        run = self.command()
        self.assertEqual(run.returncode, 2)
        self.assertIn("--upstream-root required", run.stderr)

    def test_cli_missing_input_dataset_rejected(self):
        self.path.unlink()
        run = self.command("--inspect-only")
        self.assertEqual(run.returncode, 2)

    def test_load_csv_and_content_hash(self):
        bars, cutoffs, b, c = self.parsed()
        self.assertEqual(len(bars), 150)
        self.assertEqual(len(cutoffs), 3)
        self.assertEqual(len(b), 64)
        self.assertEqual(len(c), 64)

    def test_bar_digest_cannot_refer_to_different_parsed_bytes(self):
        original = self.path.read_bytes()
        replacement = original.replace(b",100.5,1000", b",100.8,1000")
        self.assertNotEqual(original, replacement)
        real_open = Path.open
        mutated = []

        def mutate_before_second_path_open(path, *args, **kwargs):
            # The old implementation hashed via read_bytes() and then
            # reopened Path.open(newline="") for its CSV parser.
            if path == self.path and kwargs.get("newline") == "":
                self.path.write_bytes(replacement)
                mutated.append(True)
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", mutate_before_second_path_open):
            bars, digest = load_bars(self.path)
        self.assertEqual(bars[0].close, __import__("decimal").Decimal("100.5"))
        self.assertEqual(digest, sha256(original).hexdigest())
        self.assertFalse(mutated, "loader must not reopen a mutable path")

    def test_cutoff_digest_cannot_refer_to_different_parsed_bytes(self):
        original = self.cutoff_path.read_bytes()
        replacement = original.replace(b"T19:30:00", b"T18:30:00")
        self.assertNotEqual(original, replacement)
        real_open = Path.open
        mutated = []

        def mutate_before_second_path_open(path, *args, **kwargs):
            if path == self.cutoff_path and kwargs.get("newline") == "":
                self.cutoff_path.write_bytes(replacement)
                mutated.append(True)
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", mutate_before_second_path_open):
            cutoffs, digest = load_cutoffs(self.cutoff_path)
        self.assertEqual(len(cutoffs), 3)
        self.assertEqual(digest, sha256(original).hexdigest())
        self.assertFalse(mutated, "cutoff parser must not reopen a mutable path")

    def test_nofollow_csv_rejects_last_moment_symlink_swap(self):
        innocent = self.path.read_bytes()
        alternate = Path(self.tmp.name) / "alternate.csv"
        alternate.write_bytes(innocent.replace(b",100.5,1000", b",100.8,1000"))
        original_os_open = os.open

        def swap_before_os_open(path, flags, *args, **kwargs):
            if path == self.path.name and "dir_fd" in kwargs:
                self.path.unlink()
                self.path.symlink_to(alternate)
            return original_os_open(path, flags, *args, **kwargs)

        with patch("tegara._csv_snapshot.os.open",
                   side_effect=swap_before_os_open):
            with self.assertRaises(OSError):
                load_bars(self.path)
        self.assertEqual(alternate.stat().st_size, len(alternate.read_bytes()))

    def test_intermediate_parent_symlink_rejected_for_bars_and_cutoffs(self):
        redirected = Path(self.tmp.name) / "parent-alias"
        redirected.symlink_to(Path(self.tmp.name), target_is_directory=True)
        bar_bytes = self.path.read_bytes()
        cutoff_bytes = self.cutoff_path.read_bytes()
        with self.assertRaises(OSError):
            load_bars(redirected / self.path.name)
        with self.assertRaises(OSError):
            load_cutoffs(redirected / self.cutoff_path.name)
        self.assertEqual(self.path.read_bytes(), bar_bytes)
        self.assertEqual(self.cutoff_path.read_bytes(), cutoff_bytes)

    def test_parent_component_symlink_swap_during_open_is_refused(self):
        root = Path(self.tmp.name)
        parent = root / "bars-parent"
        parent.mkdir()
        (parent / self.path.name).write_bytes(self.path.read_bytes())
        decoy = root / "decoy"
        decoy.mkdir()
        decoy_bytes = self.path.read_bytes().replace(b"100.5", b"200.5")
        (decoy / self.path.name).write_bytes(decoy_bytes)
        original_open = os.open
        moved = []

        def inject_swap(component, flags, *args, **kwargs):
            if component == parent.name and not moved:
                parent.rename(root / "pinned-real")
                parent.symlink_to(decoy, target_is_directory=True)
                moved.append(True)
            return original_open(component, flags, *args, **kwargs)

        with patch("tegara._csv_snapshot.os.open", side_effect=inject_swap):
            with self.assertRaises(OSError):
                load_bars(parent / self.path.name)
        self.assertTrue(moved)
        self.assertEqual((decoy / self.path.name).read_bytes(), decoy_bytes)

    def test_walk_forward_only_latest_reserved_session_and_baseline(self):
        bars, cutoffs, _, _ = self.parsed()
        stub = FutureStub()
        report = walk_forward(stub, bars, cutoffs, self.args, model_label="stub")
        self.assertEqual(report["status"], "UNDERLYING_RESEARCH_ONLY")
        self.assertEqual(report["test_sessions"], ["2026-10-09"])
        self.assertEqual(report["window_count"], 3)
        self.assertEqual(report["model_mae"], "0.5")
        self.assertEqual(report["persistence_mae"], "0.0")
        self.assertFalse(report["options_pnl_validated"])
        self.assertFalse(report["market_data_rights_independently_verified"])
        self.assertEqual(len(stub.calls), 3)
        for call in stub.calls:
            self.assertEqual(call["df"]["close"].iloc[-1], 100.5)
            self.assertTrue(call["x_timestamp"].iloc[-1] <
                            call["y_timestamp"].iloc[0])
        self.assertIn('"options_pnl_validated": false', report_json(report))

    def test_holdout_two_sessions(self):
        bars, cutoffs, _, _ = self.parsed()
        params = ResearchParameters(1, 32, 3, 7, 2, 12)
        report = walk_forward(FutureStub(), bars, cutoffs, params)
        self.assertEqual(report["test_sessions"], ["2026-10-08", "2026-10-09"])
        self.assertEqual(report["window_count"], 6)

    def test_window_budget_covers_each_heldout_session(self):
        bars, cutoffs, _, _ = self.parsed()
        report = walk_forward(
            FutureStub(), bars, cutoffs,
            ResearchParameters(1, 32, 3, 1, 2, 2),
        )
        self.assertEqual(report["window_count"], 2)
        self.assertEqual(report["windows_by_session"],
                         {"2026-10-08": 1, "2026-10-09": 1})

    def test_window_budget_less_than_holdout_rejected(self):
        bars, cutoffs, _, _ = self.parsed()
        with self.assertRaisesRegex(ValueError, "cover every"):
            walk_forward(
                FutureStub(), bars, cutoffs,
                ResearchParameters(1, 32, 3, 1, 2, 1),
            )

    def test_holdout_without_prior_sessions_denied(self):
        bars, cutoffs, _, _ = self.parsed()
        with self.assertRaisesRegex(ValueError, "earlier session"):
            walk_forward(FutureStub(), bars, cutoffs,
                         ResearchParameters(1, 32, 3, 7, 3))

    def test_session_cutoff_required_for_every_date(self):
        bars, cutoffs, _, _ = self.parsed()
        del cutoffs[date(2026, 10, 8)]
        with self.assertRaisesRegex(ValueError, "exactly"):
            walk_forward(FutureStub(), bars, cutoffs, self.args)

    def test_early_close_rejects_data_after_cutoff(self):
        bars, cutoffs, _, _ = self.parsed()
        cutoffs[date(2026, 10, 9)] = self.sessions[-1] + timedelta(minutes=35)
        with self.assertRaisesRegex(ValueError, "at/after"):
            walk_forward(FutureStub(), bars, cutoffs, self.args)

    def test_missing_bar_inside_session_rejected(self):
        bad = [r for r in self.rows
               if r["timestamp"] != (self.sessions[-1]+timedelta(minutes=30)).isoformat()]
        self.write(rows=bad)
        bars, cutoffs, _, _ = self.parsed()
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            walk_forward(FutureStub(), bars, cutoffs, self.args)

    def test_repeated_bar_rejected_at_csv_parse(self):
        bad = self.rows[:2] + [self.rows[1]] + self.rows[2:]
        self.write(rows=bad)
        with self.assertRaisesRegex(ValueError, "increasing"):
            load_bars(self.path)

    def test_no_offset_rejected(self):
        bad = list(self.rows)
        bad[0] = {**bad[0], "timestamp": "2026-10-07T13:30:00"}
        self.write(rows=bad)
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            load_bars(self.path)

    def test_ohlc_inconsistent_rejected(self):
        bad = list(self.rows)
        bad[0] = {**bad[0], "high": "90"}
        self.write(rows=bad)
        with self.assertRaisesRegex(ValueError, "OHLC"):
            load_bars(self.path)

    def test_nonfinite_value_rejected(self):
        bad = list(self.rows)
        bad[0] = {**bad[0], "volume": "NaN"}
        self.write(rows=bad)
        with self.assertRaisesRegex(ValueError, "finite"):
            load_bars(self.path)

    def test_bad_cutoff_date_rejected(self):
        wrong = list(self.cutoffs)
        wrong[0] = {**wrong[0], "last_trading_at": "2026-10-08T20:00:00+00:00"}
        self.write(cutoffs=wrong)
        with self.assertRaisesRegex(ValueError, "New York session"):
            load_cutoffs(self.cutoff_path)

    def test_duplicate_cutoff_rejected(self):
        self.write(cutoffs=self.cutoffs + self.cutoffs[:1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_cutoffs(self.cutoff_path)

    def test_mismatched_horizon_cannot_be_scored(self):
        bars, cutoffs, _, _ = self.parsed()
        tiny = list(bars)
        tiny.pop()
        with self.assertRaisesRegex(ValueError, "missing/duplicate|increasing"):
            # Removing the last bar is valid; remove from the middle instead.
            tiny.pop(-4)
            walk_forward(FutureStub(), tiny, cutoffs, self.args)

    def test_no_windows_denied(self):
        bars, cutoffs, _, _ = self.parsed()
        with self.assertRaisesRegex(ValueError, "no contiguous windows"):
            walk_forward(FutureStub(), bars, cutoffs,
                         ResearchParameters(1, 49, 3, 5, 1))


if __name__ == "__main__":
    unittest.main()
