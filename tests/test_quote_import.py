"""CSV import fail-closed quality tests, exclusively using synthetic fixtures."""
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tegara import OptionContract
from tegara.quote_import import FIELDS, quality_json, read_option_csv


class OfflineQuoteCSVTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.csv"
        self.contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))

    def row(self, second=0, **change):
        r = {
            "observed_at": (datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
                            + timedelta(seconds=second)).isoformat(),
            "symbol": "SPY", "right": "C", "strike": "600",
            "expiration": "2026-10-09", "multiplier": "100",
            "bid": "1.20", "ask": "1.25", "bid_size": "2", "ask_size": "4",
            "feed_type": "EXACT_NBBO",
            "source": "fixture-not-real-opra",
        }
        r.update(change)
        return r

    def write(self, rows, fields=FIELDS):
        with self.path.open("w", newline="", encoding="utf8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    def read(self):
        return read_option_csv(self.path, expected_contract=self.contract,
                               expected_quantity=2)

    def test_good_synthetic_rows_never_certify_live_trading(self):
        self.write([self.row(), self.row(2, bid="1.22", ask="1.30")])
        ticks, result = self.read()
        self.assertEqual(len(ticks), 2)
        self.assertEqual(result.max_gap_seconds, "2.0")
        self.assertFalse(result.live_eligible)
        self.assertEqual(result.provenance_status, "NOT_INDEPENDENTLY_VERIFIED")
        self.assertEqual(result.licensing_status, "NOT_INDEPENDENTLY_VERIFIED")
        self.assertIn('"live_eligible": false', quality_json(result))

    def test_untrusted_indicative_row_never_upgrades_itself(self):
        self.write([self.row(feed_type="INDICATIVE")])
        _, report = self.read()
        self.assertEqual(report.indicative_rows, 1)
        self.assertFalse(report.live_eligible)

    def test_csv_field_cannot_claim_verified_data_by_extra_column(self):
        r = self.row()
        r["opra_licensed"] = "true"
        self.write([r], fields=(*FIELDS, "opra_licensed"))
        with self.assertRaisesRegex(ValueError, "exact Tegara"):
            self.read()

    def test_missing_column_rejected(self):
        r = self.row()
        r.pop("bid_size")
        self.write([r], fields=tuple(f for f in FIELDS if f != "bid_size"))
        with self.assertRaisesRegex(ValueError, "exact Tegara"):
            self.read()

    def test_missing_contract_or_wrong_right_rejected(self):
        self.write([self.row(right="P")])
        with self.assertRaisesRegex(ValueError, "contract identity"):
            self.read()

    def test_wrong_strike_and_expiry_rejected(self):
        for key, value in (("strike", "605"), ("expiration", "2026-10-16")):
            with self.subTest(key=key):
                self.write([self.row(**{key: value})])
                with self.assertRaises(ValueError):
                    self.read()

    def test_repeated_timestamp_rejected(self):
        self.write([self.row(), self.row()])
        with self.assertRaisesRegex(ValueError, "repeated"):
            self.read()

    def test_out_of_order_rejected(self):
        self.write([self.row(2), self.row()])
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            self.read()

    def test_expired_overnight_and_wrong_day_rejected(self):
        self.write([self.row(observed_at="2026-10-10T05:00:00+00:00")])
        with self.assertRaisesRegex(ValueError, "NY expiration"):
            self.read()

    def test_quote_after_user_supplied_last_trading_cutoff_rejected(self):
        self.write([self.row(observed_at="2026-10-10T00:30:00+00:00")])
        cutoff = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "last trading cutoff"):
            read_option_csv(self.path, expected_contract=self.contract,
                            expected_quantity=2, last_trading_at=cutoff)

    def test_quote_exactly_at_user_supplied_cutoff_is_rejected(self):
        # A snapshot at the cutoff is no longer tradable; the boundary
        # must be strictly earlier than last_trading_at.
        cutoff = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
        self.write([self.row(observed_at=cutoff.isoformat())])
        with self.assertRaisesRegex(ValueError, "last trading cutoff"):
            read_option_csv(
                self.path, expected_contract=self.contract,
                expected_quantity=2, last_trading_at=cutoff,
            )

    def test_quote_just_before_cutoff_is_still_accepted_as_unverified(self):
        cutoff = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
        self.write([self.row(
            observed_at=(cutoff - timedelta(microseconds=1)).isoformat()
        )])
        ticks, quality = read_option_csv(
            self.path, expected_contract=self.contract,
            expected_quantity=2, last_trading_at=cutoff,
        )
        self.assertEqual(len(ticks), 1)
        self.assertLess(ticks[0].quote.observed_at, cutoff)
        self.assertFalse(quality.live_eligible)

    def test_cutoff_metadata_never_claims_independent_verification(self):
        self.write([self.row()])
        cutoff = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
        _, report = read_option_csv(self.path, expected_contract=self.contract,
                                    expected_quantity=2, last_trading_at=cutoff)
        self.assertEqual(report.venue_cutoff_status, "USER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED")
        self.assertFalse(report.live_eligible)

    def test_offset_timezone_correctly_mapped_to_ny(self):
        self.write([self.row(observed_at="2026-10-09T10:30:00-04:00")])
        ticks, _ = self.read()
        self.assertEqual(ticks[0].quote.observed_at.utcoffset(), timedelta(hours=-4))

    def test_nan_and_crossed_and_negative_bid_rejected(self):
        for values in ({"bid": "NaN"}, {"bid": "5"}, {"bid": "-0.1"}):
            with self.subTest(values=values):
                self.write([self.row(**values)])
                with self.assertRaisesRegex(ValueError, "invalid quote"):
                    self.read()

    def test_extreme_finite_bid_and_ask_exponents_rejected_cleanly(self):
        for value in ("1e999999999", "1e-999999999",
                      "0e999999999", "1e-19",
                      "1.23456789012345678901234567890"):
            for field in ("bid", "ask"):
                with self.subTest(value=value, field=field):
                    row = self.row(**{field: value})
                    if field == "bid":
                        row["ask"] = value
                    self.write([row])
                    with self.assertRaisesRegex(ValueError, "invalid quote"):
                        self.read()

    def test_broken_size_and_feed_rejected(self):
        for values in ({"bid_size": "-1"}, {"ask_size": "1.0"},
                       {"feed_type": "REALTIME"}, {"bid_size": "NaN"}):
            with self.subTest(values=values):
                self.write([self.row(**values)])
                with self.assertRaisesRegex(ValueError, "invalid quote"):
                    self.read()

    def test_empty_rows_rejected(self):
        self.write([])
        with self.assertRaisesRegex(ValueError, "no quote rows"):
            self.read()

    def test_cli_quote_audit_prints_unverified_status(self):
        self.write([self.row(), self.row(3)])
        p = subprocess.run(
            [sys.executable, "-m", "tegara", "quote-audit",
             "--csv", str(self.path), "--symbol", "SPY", "--right", "C",
             "--strike", "600", "--expiration", "2026-10-09",
             "--quantity", "2", "--last-trading-at", "2026-10-09T20:00:00+00:00"],
            capture_output=True, text=True, env=os.environ,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["venue_cutoff_status"], "USER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED")
        self.assertFalse(result["live_eligible"])

    def test_cli_rejects_cross_contract_input(self):
        self.write([self.row()])
        p = subprocess.run(
            [sys.executable, "-m", "tegara", "quote-audit",
             "--csv", str(self.path), "--symbol", "SPY", "--right", "P",
             "--strike", "600", "--expiration", "2026-10-09",
             "--quantity", "1"],
            capture_output=True, text=True, env=os.environ,
        )
        self.assertNotEqual(p.returncode, 0)

    def test_zero_bid_and_insufficient_size_flags(self):
        self.write([self.row(bid="0", bid_size="0")])
        _, report = self.read()
        self.assertEqual(report.zero_bid_rows, 1)
        self.assertEqual(report.insufficient_bid_size_rows, 1)

    def test_locked_quote_flag(self):
        self.write([self.row(ask="1.20")])
        _, report = self.read()
        self.assertEqual(report.locked_market_rows, 1)

    def test_symlink_denied(self):
        self.write([self.row()])
        link = Path(self.temp.name) / "link.csv"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            read_option_csv(link, expected_contract=self.contract,
                            expected_quantity=1)

    def test_missing_atomic_nofollow_support_fails_closed(self):
        self.write([self.row()])
        # Simulate a platform exposing no usable O_NOFOLLOW flag. The
        # importer must refuse, never silently fall back to O_RDONLY.
        with patch("tegara.quote_import.os.O_NOFOLLOW", 0):
            with self.assertRaisesRegex(ValueError, "no-follow"):
                self.read()

    def test_symlink_swap_at_open_never_reads_other_quote_file(self):
        self.write([self.row()])
        alternate = Path(self.temp.name) / "unrelated.csv"
        alternate.write_bytes(self.path.read_bytes().replace(b"1.20", b"1.22"))
        expected_content = alternate.read_bytes()
        original_open = os.open

        def insert_symlink_at_open(path, flags, *args, **kwargs):
            if Path(path) == self.path:
                self.path.unlink()
                self.path.symlink_to(alternate)
            return original_open(path, flags, *args, **kwargs)

        with patch("tegara.quote_import.os.open",
                   side_effect=insert_symlink_at_open):
            with self.assertRaises(OSError):
                self.read()
        self.assertEqual(alternate.read_bytes(), expected_content)

    def test_missing_timezone_denied(self):
        self.write([self.row(observed_at="2026-10-09T14:30:00")])
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            self.read()

    def test_giant_byte_length_rejected(self):
        with self.path.open("wb") as f:
            f.truncate(48 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "too large"):
            self.read()


if __name__ == "__main__":
    unittest.main()
