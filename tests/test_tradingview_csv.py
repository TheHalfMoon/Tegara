"""TradingView-like CSV conversion tests, especially candle availability time."""
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tegara.tradingview_csv import (
    convert_tradingview, parse_timestamp, write_converted
)
from tegara.underlying_research import load_bars


class TradingViewCSVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "chart.csv"
        self.dest = Path(self.tmp.name) / "normalized.csv"
        self.rows = [
            {"time": "1791552600", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "Volume": "2400"},
            {"time": "1791552660", "open": "100.5", "high": "101.2",
             "low": "100", "close": "101", "Volume": "1800"},
        ]
        self.write()

    def write(self, *, rows=None, header=None):
        fields = header or ("time", "open", "high", "low", "close", "Volume")
        with self.source.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)

    def test_open_bar_time_is_shifted_to_data_availability(self):
        rows = convert_tradingview(
            self.source, interval_minutes=1, bar_time_is="open")
        original = parse_timestamp(self.rows[0]["time"])
        shifted = datetime.fromisoformat(rows[0]["timestamp"])
        self.assertEqual((shifted - original).total_seconds(), 60)
        write_converted(self.dest, rows)
        parsed, _ = load_bars(self.dest)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].close, __import__("decimal").Decimal("100.5"))

    def test_closed_bar_time_is_not_shifted(self):
        rows = convert_tradingview(
            self.source, interval_minutes=1, bar_time_is="close")
        self.assertEqual(
            rows[0]["timestamp"], parse_timestamp(self.rows[0]["time"]).isoformat()
        )

    def test_missing_bar_semantics_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicitly"):
            convert_tradingview(
                self.source, interval_minutes=1, bar_time_is="unknown"
            )

    def test_milliseconds_timestamp(self):
        self.assertEqual(
            parse_timestamp("1791552600000"),
            parse_timestamp("1791552600")
        )

    def test_offset_iso_timestamp(self):
        self.assertEqual(
            parse_timestamp("2026-10-09T09:30:00-04:00"),
            datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
        )

    def test_naive_timestamp_without_source_timezone_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit source"):
            parse_timestamp("2026-10-09T09:30:00")

    def test_explicit_source_timezone_converts_ny(self):
        self.assertEqual(
            parse_timestamp("2026-10-09T09:30:00", source_tz="America/New_York"),
            datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc),
        )

    def test_ambiguous_dst_fall_back_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambiguous DST"):
            parse_timestamp("2026-11-01T01:30:00", source_tz="America/New_York")

    def test_nonexistent_dst_spring_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonexistent DST"):
            parse_timestamp("2026-03-08T02:30:00", source_tz="America/New_York")

    def test_repeated_bar_time_rejected(self):
        self.write(rows=[self.rows[0], self.rows[0]])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            convert_tradingview(self.source, interval_minutes=1, bar_time_is="open")

    def test_wrong_chart_column_structure_rejected(self):
        self.write(header=("time", "open", "high", "low", "close", "Volume", "custom"),
                   rows=[{**self.rows[0], "custom": "1"}])
        with self.assertRaisesRegex(ValueError, "exact"):
            convert_tradingview(self.source, interval_minutes=1, bar_time_is="open")

    def test_invalid_numeric_rejected(self):
        self.write(rows=[{**self.rows[0], "close": "NaN"}])
        with self.assertRaisesRegex(ValueError, "invalid close"):
            convert_tradingview(self.source, interval_minutes=1, bar_time_is="open")

    def test_existing_file_not_overwritten(self):
        self.dest.write_text("sensitive source")
        rows = convert_tradingview(self.source, interval_minutes=1, bar_time_is="open")
        with self.assertRaisesRegex(ValueError, "must not already exist"):
            write_converted(self.dest, rows)
        self.assertEqual(self.dest.read_text(), "sensitive source")

    def test_mid_write_failure_does_not_publish_partial_csv(self):
        valid = convert_tradingview(
            self.source, interval_minutes=1, bar_time_is="open"
        )[0]
        with self.assertRaisesRegex(ValueError, "fields not in fieldnames"):
            write_converted(self.dest, [valid, {"unexpected": "invalid"}])
        self.assertFalse(self.dest.exists())
        self.assertEqual(
            sorted(p.name for p in self.dest.parent.iterdir()),
            [self.source.name],
        )

    def test_late_destination_claim_never_overwrites_existing_file(self):
        rows = convert_tradingview(
            self.source, interval_minutes=1, bar_time_is="open"
        )
        actual_link = os.link

        def compete(source, target):
            self.dest.write_text("other writer's data")
            return actual_link(source, target)

        with patch("os.link", side_effect=compete):
            with self.assertRaises(FileExistsError):
                write_converted(self.dest, rows)
        self.assertEqual(self.dest.read_text(), "other writer's data")
        self.assertEqual(
            sorted(p.name for p in self.dest.parent.iterdir()),
            sorted([self.source.name, self.dest.name]),
        )

    def test_output_private_permissions(self):
        rows = convert_tradingview(self.source, interval_minutes=1, bar_time_is="open")
        write_converted(self.dest, rows)
        if os.name == "posix":
            self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)

    def test_checked_source_cannot_be_reopened_as_different_csv(self):
        original = self.source.read_bytes()
        alternate = Path(self.tmp.name) / "alternate-source.csv"
        # Use a separately constructed valid alternate, not malformed OHLC.
        alternate.write_text(
            "time,open,high,low,close,Volume\n"
            "1791552600,200,202,199,200.5,2400\n"
            "1791552660,200.5,203,200,201,1800\n"
        )
        self.assertNotEqual(original, alternate.read_bytes())
        old_open = Path.open
        switched = []

        def swap_before_second_path_open(path, *args, **kwargs):
            if path == self.source and kwargs.get("encoding") == "utf-8-sig":
                self.source.unlink()
                self.source.symlink_to(alternate)
                switched.append(True)
            return old_open(path, *args, **kwargs)

        with patch.object(Path, "open", swap_before_second_path_open):
            rows = convert_tradingview(
                self.source, interval_minutes=1, bar_time_is="open"
            )
        self.assertEqual(rows[0]["close"], "100.5")
        self.assertFalse(switched, "CSV importer must not reopen checked path")

    def test_source_symlink_swap_at_descriptor_open_is_rejected(self):
        alternate = Path(self.tmp.name) / "other.csv"
        alternate.write_bytes(self.source.read_bytes())
        original_open = os.open

        def switch_to_link(path, flags, *args, **kwargs):
            if path == self.source.name and "dir_fd" in kwargs:
                self.source.unlink()
                self.source.symlink_to(alternate)
            return original_open(path, flags, *args, **kwargs)

        with patch("tegara._csv_snapshot.os.open", side_effect=switch_to_link):
            with self.assertRaises(OSError):
                convert_tradingview(
                    self.source, interval_minutes=1, bar_time_is="open"
                )
        self.assertTrue(alternate.is_file())

    def test_parent_symlink_refused_before_tradingview_parsing(self):
        alias = Path(self.tmp.name) / "redirected-parent"
        alias.symlink_to(Path(self.tmp.name), target_is_directory=True)
        raw = self.source.read_bytes()
        with self.assertRaises(OSError):
            convert_tradingview(
                alias / self.source.name, interval_minutes=1, bar_time_is="open"
            )
        self.assertEqual(self.source.read_bytes(), raw)

    def test_symlink_input_rejected(self):
        link = Path(self.tmp.name) / "link.csv"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "regular"):
            convert_tradingview(link, interval_minutes=1, bar_time_is="open")


if __name__ == "__main__":
    unittest.main()
