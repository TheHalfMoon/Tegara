"""Synthetic end-to-end option CSV replay CLI tests. Never uses real trades."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tegara.quote_import import FIELDS


class OfflineReplayCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "synthetic.csv"
        self.root = Path(__file__).resolve().parents[1]
        self.t0 = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
        self.rows = [self.row(), self.row(seconds=180, bid="1.62", ask="1.65")]
        self.write()

    def row(self, *, seconds=0, bid="1.18", ask="1.20", feed="EXACT_NBBO"):
        return {
            "observed_at": (self.t0 + timedelta(seconds=seconds)).isoformat(),
            "symbol": "SPY", "right": "C", "strike": "600",
            "expiration": "2026-10-09", "multiplier": "100",
            "bid": bid, "ask": ask, "bid_size": "5", "ask_size": "5",
            "feed_type": feed, "source": "SYNTHETIC_NOT_OPRA",
        }

    def write(self):
        with self.csv.open("w", newline="", encoding="utf-8") as fp:
            out = csv.DictWriter(fp, fieldnames=FIELDS)
            out.writeheader()
            out.writerows(self.rows)

    def cmd(self, *additional):
        return [
            sys.executable, "-m", "tegara.replay_cli",
            "--csv", str(self.csv),
            "--symbol", "SPY", "--right", "C",
            "--strike", "600", "--expiration", "2026-10-09",
            "--multiplier", "100", "--quantity", "1",
            "--entry-fee", "2.29", "--exit-fee", "2.29",
            "--entry-slippage", "0", "--exit-slippage", "0",
            "--target-net-profit", "35", "--max-net-loss", "50",
            "--entry-not-before", "2026-10-09T14:30:00+00:00",
            "--deadline", "2026-10-09T14:40:00+00:00",
            "--last-trading-at", "2026-10-09T20:00:00+00:00",
            "--max-gap-seconds", "300",
            *additional,
        ]

    def run_cmd(self, *additional, change=None):
        args = self.cmd(*additional)
        if change:
            for name, value in change.items():
                args[args.index(name) + 1] = value
        return subprocess.run(
            args, capture_output=True, text=True, cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(self.root / "src")},
        )

    def test_default_abstains_without_pnl(self):
        result = self.run_cmd()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "UNQUALIFIED_DATA")
        self.assertIsNone(out["hypothetical_net_pnl_usd"])
        self.assertFalse(out["hypothetical_pnl_requested"])
        self.assertEqual(out["parameters"]["execution_mode"], "delayed")
        self.assertFalse(out["broker_execution_verified"])
        self.assertFalse(out["phone_notification_sent"])
        self.assertFalse(out["source_provenance_independently_verified"])
        self.assertFalse(out["input_quality"]["live_eligible"])

    def test_sha256_matches_exact_parsed_csv(self):
        result = self.run_cmd()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["input_quality"]["csv_sha256"],
                         sha256(self.csv.read_bytes()).hexdigest())

    def test_explicit_hypothetical_replay_models_ask_bid_fees(self):
        # Old zero-latency reference is intentionally never the default.
        result = self.run_cmd("--allow-hypothetical-pnl",
                              "--execution-mode", "instant-reference")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "TARGET_EXIT")
        self.assertEqual(out["entry_premium"], "1.20")
        self.assertEqual(out["exit_premium"], "1.62")
        self.assertEqual(out["hypothetical_net_pnl_usd"], "37.42")
        self.assertTrue(out["hypothetical_pnl_requested"])
        self.assertEqual(out["status"], "RESEARCH_ONLY_NOT_LIVE_TRADING")
        self.assertFalse(out["options_profitability_validated"])

    def test_delayed_mode_is_default_with_research_optin(self):
        # A CSV only containing T0 and T+180s quotes cannot create a
        # plausible 1-second delayed executable trade at T0.
        p = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["parameters"]["execution_mode"], "delayed")
        self.assertEqual(out["outcome"], "NO_EXIT_EVIDENCE")
        self.assertIsNone(out["hypothetical_net_pnl_usd"])

    def test_delayed_default_price_crash_after_trigger_is_loss(self):
        self.rows = [
            self.row(seconds=0, bid="1.18", ask="1.20"),
            self.row(seconds=1, bid="1.19", ask="1.25"),
            self.row(seconds=2, bid="1.65", ask="1.69"),
            self.row(seconds=3, bid="0.80", ask="0.85"),
        ]
        self.write()
        p = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["parameters"]["execution_mode"], "delayed")
        self.assertEqual(out["outcome"], "TARGET_EXIT")
        self.assertEqual(out["hypothetical_net_pnl_usd"], "-49.58")

    def test_explicit_flag_does_not_upgrade_indicative_feed(self):
        self.rows[1]["feed_type"] = "INDICATIVE"
        self.write()
        out = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads(out.stdout)
        self.assertEqual(report["outcome"], "UNQUALIFIED_DATA")
        self.assertIsNone(report["hypothetical_net_pnl_usd"])
        self.assertEqual(report["input_quality"]["indicative_rows"], 1)

    def test_large_gap_abstains_even_when_research_requested(self):
        # Ensure an actual post-latency Ask exists so the failure is in
        # exit evidence rather than accidentally in entry evidence.
        self.rows = [
            self.row(seconds=1, bid="1.18", ask="1.20"),
            self.row(seconds=540, bid="1.62", ask="1.65"),
        ]
        self.write()
        result = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "NO_EXIT_EVIDENCE")
        self.assertIsNone(json.loads(result.stdout)["hypothetical_net_pnl_usd"])

    def test_explicit_entry_window_excludes_earlier_quotes(self):
        result = self.run_cmd("--allow-hypothetical-pnl",
                              change={"--entry-not-before": "2026-10-09T14:31:00+00:00"})
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "NO_EXIT_EVIDENCE")
        self.assertEqual(out["entry_premium"], "1.65")
        self.assertIsNone(out["hypothetical_net_pnl_usd"])

    def test_no_prompt_entry_quote_abstains(self):
        result = self.run_cmd("--allow-hypothetical-pnl",
                              change={"--entry-not-before": "2026-10-09T14:39:00+00:00"})
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "NO_ENTRY_EVIDENCE")
        self.assertIsNone(out["hypothetical_net_pnl_usd"])

    def test_entry_after_deadline_is_rejected(self):
        result = self.run_cmd("--allow-hypothetical-pnl",
                              change={"--entry-not-before": "2026-10-09T14:41:00+00:00"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("earlier than", result.stderr)

    def test_missing_ask_liquidity_abstains(self):
        # The first eligible *post-delay* quote has no ask depth.
        self.rows = [self.row(seconds=1, bid="1.18", ask="1.20")]
        self.rows[0]["ask_size"] = "0"
        self.write()
        result = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "NO_ENTRY_LIQUIDITY")

    def test_wrong_contract_is_an_error_not_a_trade(self):
        result = self.run_cmd("--allow-hypothetical-pnl",
                              change={"--right": "P"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("contract identity", result.stderr)
        self.assertFalse(result.stdout)

    def test_missing_cutoff_timestamp_timezone_denied(self):
        result = self.run_cmd(change={
            "--last-trading-at": "2026-10-09T20:00:00"
        })
        self.assertEqual(result.returncode, 2)
        self.assertIn("explicit timezone", result.stderr)

    def test_nonfinite_monetary_amount_denied(self):
        for item in ("NaN", "Infinity", "-2", "1e100"):
            with self.subTest(item=item):
                result = self.run_cmd(change={"--entry-fee": item})
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)

    def test_max_gap_bound_denied(self):
        for gap in ("0", "-5", "3601"):
            with self.subTest(gap=gap):
                result = self.run_cmd(change={"--max-gap-seconds": gap})
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)

    def test_csv_no_rights_claim_even_with_literal_feed_label(self):
        self.rows[0]["source"] = "OPRA_REALTIME_LICENSED_100_PERCENT"
        self.write()
        result = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertFalse(out["data_licensing_independently_verified"])
        self.assertEqual(out["input_quality"]["licensing_status"],
                         "NOT_INDEPENDENTLY_VERIFIED")

    def test_symlinked_input_is_denied(self):
        link = Path(self.tmp.name) / "source-link.csv"
        link.symlink_to(self.csv)
        result = self.run_cmd(change={"--csv": str(link)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-symlink", result.stderr)

    def test_named_pipe_source_rejected_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs not available on this OS")
        pipe = Path(self.tmp.name) / "spoofed.csv"
        os.mkfifo(pipe)
        args = self.cmd()
        args[args.index("--csv") + 1] = str(pipe)
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=3,
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(self.root / "src")},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-symlink", result.stderr)

    def test_delayed_cli_uses_later_bid_not_trigger_bid(self):
        self.rows = [
            self.row(seconds=0, bid="1.18", ask="1.20"),
            self.row(seconds=1, bid="1.19", ask="1.25"),
            self.row(seconds=2, bid="1.65", ask="1.69"),
            self.row(seconds=3, bid="0.80", ask="0.85"),
        ]
        self.write()
        p = self.run_cmd("--allow-hypothetical-pnl",
                         "--execution-mode", "delayed")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["outcome"], "TARGET_EXIT")
        self.assertEqual(r["entry_premium"], "1.25")
        self.assertEqual(r["exit_premium"], "0.80")
        self.assertEqual(r["hypothetical_net_pnl_usd"], "-49.58")
        self.assertEqual(r["decision_trigger_at"], (
            self.t0 + timedelta(seconds=2)).isoformat())
        self.assertFalse(r["broker_execution_verified"])

    def test_delayed_cli_missing_post_trigger_bid_abstains(self):
        self.rows = [
            self.row(seconds=1, bid="1.18", ask="1.20"),
            self.row(seconds=2, bid="1.65", ask="1.69"),
        ]
        self.write()
        p = self.run_cmd("--allow-hypothetical-pnl",
                         "--execution-mode", "delayed")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["outcome"], "NO_EXIT_EVIDENCE")
        self.assertIsNone(r["hypothetical_net_pnl_usd"])

    def test_delayed_cli_default_abstains_without_optin(self):
        p = self.run_cmd("--execution-mode", "delayed")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["outcome"], "UNQUALIFIED_DATA")
        self.assertIsNone(r["hypothetical_net_pnl_usd"])

    def test_delayed_cli_rejects_zero_and_excessive_delay(self):
        for field in ("--entry-latency-seconds", "--exit-latency-seconds"):
            for amount in ("0", "-1", "301"):
                with self.subTest(field=field, amount=amount):
                    p = self.run_cmd("--allow-hypothetical-pnl",
                                     "--execution-mode", "delayed",
                                     field, amount)
                    self.assertEqual(p.returncode, 2)

    def test_late_first_quote_relative_to_delay_abstains(self):
        self.rows = [
            self.row(seconds=120, bid="1.18", ask="1.20"),
        ]
        self.write()
        p = self.run_cmd("--allow-hypothetical-pnl",
                         "--execution-mode", "delayed",
                         "--entry-latency-seconds", "1",
                         change={"--max-gap-seconds": "5"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["outcome"], "NO_ENTRY_EVIDENCE")

    def grid_data(self):
        self.rows = [
            self.row(seconds=0, bid="1.18", ask="1.20"),
            self.row(seconds=1, bid="1.19", ask="1.25"),
            self.row(seconds=2, bid="1.65", ask="1.69"),
            self.row(seconds=3, bid="0.80", ask="0.85"),
            self.row(seconds=4, bid="1.55", ask="1.60"),
        ]
        self.write()

    def test_grid_cli_reuses_one_dataset_and_has_no_scalar_profit(self):
        self.grid_data()
        p = self.run_cmd("--allow-hypothetical-pnl",
                         "--entry-latency-grid", "4,1",
                         "--exit-latency-grid", "2,1")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["outcome"], "LATENCY_SENSITIVITY_ONLY")
        self.assertIsNone(r["hypothetical_net_pnl_usd"])
        self.assertEqual(r["parameters"]["execution_mode"], "delayed")
        matrix = r["latency_sensitivity"]
        self.assertEqual(matrix["scenario_count"], 4)
        self.assertEqual(matrix["scenarios_with_priced_hypothetical_exit"], 2)
        self.assertEqual(matrix["scenarios_without_exit_pnl"], 2)
        self.assertIsNone(matrix["min_hypothetical_pnl_only_if_complete_usd"])
        self.assertEqual(matrix["scenario_results"][0]["hypothetical_net_pnl_usd"],
                         "-49.58")
        self.assertIsNone(matrix["selected_optimal_latency"])
        self.assertFalse(r["source_provenance_independently_verified"])
        self.assertEqual(r["input_quality"]["csv_sha256"],
                         sha256(self.csv.read_bytes()).hexdigest())

    def test_grid_reports_only_actually_applied_latency_settings(self):
        self.grid_data()
        p = self.run_cmd("--allow-hypothetical-pnl",
                         "--entry-latency-grid", "4,1",
                         "--exit-latency-grid", "2,1")
        self.assertEqual(p.returncode, 0, p.stderr)
        params = json.loads(p.stdout)["parameters"]
        self.assertIsNone(params["entry_latency_seconds"])
        self.assertIsNone(params["exit_latency_seconds"])
        self.assertEqual(params["entry_latency_grid"], [1, 4])
        self.assertEqual(params["exit_latency_grid"], [1, 2])

    def test_grid_rejects_ambiguous_single_latency_overrides(self):
        self.grid_data()
        for extra in (("--entry-latency-seconds", "1"),
                      ("--exit-latency-seconds", "5")):
            with self.subTest(extra=extra):
                p = self.run_cmd("--allow-hypothetical-pnl",
                                 "--entry-latency-grid", "1,4",
                                 "--exit-latency-grid", "1,2", *extra)
                self.assertEqual(p.returncode, 2)
                self.assertIn("cannot accompany a grid", p.stderr)
                self.assertFalse(p.stdout)

    def test_grid_cli_no_optin_never_reports_profit(self):
        self.grid_data()
        p = self.run_cmd("--entry-latency-grid", "1,4",
                         "--exit-latency-grid", "1,2")
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads(p.stdout)
        self.assertEqual(report["outcome"], "UNQUALIFIED_DATA")
        self.assertIsNone(report["hypothetical_net_pnl_usd"])
        self.assertEqual(
            report["latency_sensitivity"]["scenarios_with_priced_hypothetical_exit"], 0
        )
        self.assertTrue(all(scenario["hypothetical_net_pnl_usd"] is None
                            for scenario in report["latency_sensitivity"]["scenario_results"]))

    def test_grid_cli_requires_two_axes_and_delayed_mode(self):
        self.grid_data()
        for extra in (
            ("--entry-latency-grid", "1,2"),
            ("--exit-latency-grid", "1,2"),
            ("--entry-latency-grid", "1", "--exit-latency-grid", "1",
             "--execution-mode", "instant-reference"),
        ):
            with self.subTest(extra=extra):
                p = self.run_cmd("--allow-hypothetical-pnl", *extra)
                self.assertEqual(p.returncode, 2)
                self.assertFalse(p.stdout)

    def test_grid_cli_rejects_unbounded_or_malformed_axes(self):
        self.grid_data()
        for axis in ("0,1", "1,1", "1, 2", "1,2,3,4,5",
                     "1e2", "1,301", ",1", "1,"):
            with self.subTest(axis=axis):
                p = self.run_cmd("--allow-hypothetical-pnl",
                                 "--entry-latency-grid", axis,
                                 "--exit-latency-grid", "1")
                self.assertEqual(p.returncode, 2)
                self.assertFalse(p.stdout)

    def test_grid_cli_indicative_quote_denies_priced_exit(self):
        self.grid_data()
        self.rows[2]["feed_type"] = "INDICATIVE"
        self.write()
        p = self.run_cmd("--allow-hypothetical-pnl",
                         "--entry-latency-grid", "1",
                         "--exit-latency-grid", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["latency_sensitivity"]["scenario_results"][0]["outcome"],
                         "UNQUALIFIED_DATA")
        self.assertIsNone(r["latency_sensitivity"]["scenario_results"][0][
            "hypothetical_net_pnl_usd"])

    def test_grid_cli_parses_csv_only_once(self):
        from unittest.mock import patch
        from tegara import replay_cli
        from tegara.quote_import import read_option_csv
        self.grid_data()
        command = self.cmd("--allow-hypothetical-pnl",
                           "--entry-latency-grid", "1,4",
                           "--exit-latency-grid", "1,2")
        with patch("tegara.replay_cli.read_option_csv", wraps=read_option_csv) as read:
            report = replay_cli.evaluate(replay_cli.parser().parse_args(command[3:]))
            self.assertEqual(read.call_count, 1)
            self.assertEqual(report["latency_sensitivity"]["scenario_count"], 4)

    def test_tool_does_not_write_or_modify_source(self):
        before = self.csv.read_bytes()
        result = self.run_cmd("--allow-hypothetical-pnl")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.csv.read_bytes())
        self.assertEqual(set(Path(self.tmp.name).iterdir()), {self.csv})


if __name__ == "__main__":
    unittest.main()
