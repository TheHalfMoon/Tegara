"""Pinned checkout integrity tests without downloading third-party models."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tegara.kronos_bridge import PINNED_KRONOS_UPSTREAM, verify_pinned_kronos_checkout


class PinningTests(TestCase):
    def setUp(self):
        self.work = TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        (self.root / "model").mkdir()
        (self.root / "model" / "__init__.py").write_text("", encoding="utf-8")

    def fake_git(self, *, head=PINNED_KRONOS_UPSTREAM, status="", failure=False):
        def runner(args, **kwargs):
            if failure:
                return SimpleNamespace(returncode=1, stdout="", stderr="fail")
            return SimpleNamespace(
                returncode=0,
                stdout=head if args[1:] == ["rev-parse", "HEAD"] else status,
                stderr="",
            )
        return runner

    def test_valid_pinned_and_clean_checkout_only(self):
        with patch("tegara.kronos_bridge.subprocess.run", self.fake_git()):
            self.assertEqual(verify_pinned_kronos_checkout(self.root), self.root.resolve())

    def test_wrong_commit_denied(self):
        with patch("tegara.kronos_bridge.subprocess.run",
                   self.fake_git(head="0" * 40)):
            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_pinned_kronos_checkout(self.root)

    def test_dirty_model_package_denied(self):
        with patch("tegara.kronos_bridge.subprocess.run",
                   self.fake_git(status="?? model/malware.py")):
            with self.assertRaisesRegex(ValueError, "modified/untracked"):
                verify_pinned_kronos_checkout(self.root)

    def test_uninspectable_checkout_denied(self):
        with patch("tegara.kronos_bridge.subprocess.run",
                   self.fake_git(failure=True)):
            with self.assertRaisesRegex(ValueError, "cannot inspect"):
                verify_pinned_kronos_checkout(self.root)

    def test_missing_model_package_denied(self):
        (self.root / "model" / "__init__.py").unlink()
        with self.assertRaisesRegex(ValueError, "missing official"):
            verify_pinned_kronos_checkout(self.root)
