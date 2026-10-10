"""Adversarial on-disk SQLite path tests; no broker or live data involved."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tegara.position_store import PositionStore


@unittest.skipUnless(os.name == "posix", "no-follow tests require POSIX")
class SQLiteWritableSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parent = self.root / "private"
        self.path = self.parent / "journal.db"
        self.victim = self.root / "unrelated.sqlite"
        with closing(sqlite3.connect(self.victim)) as db:
            db.execute("CREATE TABLE unrelated (note TEXT NOT NULL)")
            db.execute("INSERT INTO unrelated VALUES ('untouched')")
            db.commit()
        self.victim.chmod(0o640)
        self.digest = hashlib.sha256(self.victim.read_bytes()).hexdigest()
        self.mode = self.victim.stat().st_mode & 0o777

    def assert_victim_untouched(self):
        self.assertEqual(hashlib.sha256(self.victim.read_bytes()).hexdigest(),
                         self.digest)
        self.assertEqual(self.victim.stat().st_mode & 0o777, self.mode)
        with closing(sqlite3.connect(self.victim)) as db:
            self.assertEqual(db.execute(
                "SELECT note FROM unrelated").fetchone(), ("untouched",))
            self.assertEqual(db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall(), [("unrelated",)])

    def test_new_file_created_with_private_mode(self):
        with PositionStore(self.path):
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)
        self.assert_victim_untouched()

    def test_existing_symlink_denied_without_touching_target(self):
        self.parent.mkdir(mode=0o700)
        self.path.symlink_to(self.victim)
        with self.assertRaisesRegex(ValueError, "symlinks are refused"):
            PositionStore(self.path)
        self.assert_victim_untouched()

    def test_intermediate_directory_symlink_denied(self):
        real = self.root / "elsewhere"
        real.mkdir()
        self.parent.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks are refused"):
            PositionStore(self.path)
        self.assertFalse((real / "journal.db").exists())
        self.assert_victim_untouched()

    def test_world_writable_immediate_parent_denied(self):
        self.parent.mkdir(mode=0o700)
        self.parent.chmod(0o777)
        try:
            with self.assertRaisesRegex(ValueError, "owner-only"):
                PositionStore(self.path)
        finally:
            self.parent.chmod(0o700)
        self.assertFalse(self.path.exists())
        self.assert_victim_untouched()

    def test_world_readable_immediate_parent_denied(self):
        self.parent.mkdir(mode=0o700)
        self.parent.chmod(0o755)
        try:
            with self.assertRaisesRegex(ValueError, "owner-only"):
                PositionStore(self.path)
        finally:
            self.parent.chmod(0o700)
        self.assertFalse(self.path.exists())
        self.assert_victim_untouched()

    def test_symlink_injected_at_sqlite_connect_denied_before_schema(self):
        real_connect = sqlite3.connect

        def swap_then_connect(database_uri, **kwargs):
            self.path.unlink()
            self.path.symlink_to(self.victim)
            return real_connect(database_uri, **kwargs)

        with patch("tegara._sqlite_writable.sqlite3.connect",
                   side_effect=swap_then_connect):
            with self.assertRaisesRegex(ValueError, "changed during open|private regular file"):
                PositionStore(self.path)
        self.assert_victim_untouched()

    def test_symlink_injected_before_fchmod_does_not_chmod_target(self):
        original_fchmod = os.fchmod

        def swap_then_fchmod(fd, mode):
            self.path.unlink()
            self.path.symlink_to(self.victim)
            return original_fchmod(fd, mode)

        with patch("tegara._sqlite_writable.os.fchmod",
                   side_effect=swap_then_fchmod):
            with self.assertRaisesRegex(ValueError, "changed during open|private regular file"):
                PositionStore(self.path)
        self.assert_victim_untouched()

    def test_parent_swapped_at_sqlite_connect_denied_before_schema(self):
        target_dir = self.root / "target-dir"
        target_dir.mkdir()
        target_db = target_dir / self.path.name
        target_db.write_bytes(self.victim.read_bytes())
        initial = hashlib.sha256(target_db.read_bytes()).digest()
        real_connect = sqlite3.connect

        def swap_parent_then_connect(database_uri, **kwargs):
            self.parent.rename(self.root / "detached-private")
            self.parent.symlink_to(target_dir, target_is_directory=True)
            return real_connect(database_uri, **kwargs)

        with patch("tegara._sqlite_writable.sqlite3.connect",
                   side_effect=swap_parent_then_connect):
            with self.assertRaisesRegex(ValueError, "changed during open"):
                PositionStore(self.path)
        self.assertEqual(hashlib.sha256(target_db.read_bytes()).digest(), initial)
        self.assert_victim_untouched()

    def test_hardlink_to_unrelated_file_rejected_before_chmod(self):
        self.parent.mkdir(mode=0o700)
        os.link(self.victim, self.path)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            PositionStore(self.path)
        self.assert_victim_untouched()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs unavailable")
    def test_fifo_rejected_without_blocking(self):
        self.parent.mkdir(mode=0o700)
        os.mkfifo(self.path)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            PositionStore(self.path)
        self.assert_victim_untouched()

    def test_symlink_injected_at_exclusive_creation_is_not_followed(self):
        original_open = os.open

        def swap_at_create(path, flags, *args, **kwargs):
            if path == self.path.name and flags & os.O_EXCL:
                self.path.symlink_to(self.victim)
            return original_open(path, flags, *args, **kwargs)

        with patch("tegara._sqlite_writable.os.open",
                   side_effect=swap_at_create):
            with self.assertRaisesRegex(ValueError, "symlinks are refused"):
                PositionStore(self.path)
        self.assert_victim_untouched()

    def test_memory_database_remains_available(self):
        with PositionStore() as store:
            self.assertEqual(store.db.execute("SELECT 1").fetchone()[0], 1)
        self.assert_victim_untouched()


if __name__ == "__main__":
    unittest.main()
