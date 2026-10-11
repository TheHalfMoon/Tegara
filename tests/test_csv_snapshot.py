"""Bounded offline CSV snapshots must reject detected concurrent writes."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tegara._csv_snapshot import read_csv_snapshot


class SnapshotStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.csv_path = Path(self.temp.name) / "synthetic.csv"
        self.original = b"timestamp,close\nA,100.5\n"
        self.csv_path.write_bytes(self.original)

    def test_unchanged_regular_source_returns_one_snapshot(self):
        self.assertEqual(read_csv_snapshot(self.csv_path, 1024), self.original)

    def test_preexisting_oversize_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "bounded local input size"):
            read_csv_snapshot(self.csv_path, len(self.original) - 1)

    def _read_while_writer_modifies(self, replacement: bytes) -> None:
        native_fdopen = os.fdopen
        source_path = self.csv_path

        class MidReadMutation:
            def __init__(self, f):
                self.file = f

            def __enter__(self):
                self.file.__enter__()
                return self

            def __exit__(self, *args):
                return self.file.__exit__(*args)

            def fileno(self):
                return self.file.fileno()

            def read(self, n):
                snapshot = self.file.read(n)
                before = source_path.stat()
                # A separate writer changes the *same inode* while it is
                # already open. Force a distinct mtime for deterministic
                # testing even on filesystems with coarser timestamps.
                source_path.write_bytes(replacement)
                os.utime(source_path, ns=(
                    before.st_atime_ns, before.st_mtime_ns + 1_000_000_000,
                ))
                return snapshot

        def fdopen_with_writer(fd, *args, **kwargs):
            return MidReadMutation(native_fdopen(fd, *args, **kwargs))

        with patch("tegara._csv_snapshot.os.fdopen",
                   side_effect=fdopen_with_writer):
            with self.assertRaisesRegex(ValueError, "changed during offline snapshot"):
                read_csv_snapshot(self.csv_path, 1024)
        self.assertEqual(self.csv_path.read_bytes(), replacement)

    def test_same_size_in_place_change_is_not_silently_accepted(self):
        self._read_while_writer_modifies(b"timestamp,close\nA,200.5\n")

    def test_changed_file_size_is_not_silently_accepted(self):
        self._read_while_writer_modifies(
            b"timestamp,close\nA,100.5\nB,200.5\n"
        )


if __name__ == "__main__":
    unittest.main()
