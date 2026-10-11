"""Local-only SQLite proof that audit never claims, resends or leaks tokens."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tegara import LongOptionPosition, OptionContract, OptionQuote
from tegara.outbox_audit import read_outbox_audit
from tegara.position_store import PositionStore


class ReadOnlyOutboxAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "positions.sqlite"
        self.t = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
        self.c = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.store = PositionStore(self.path)
        self.addCleanup(self.store.close)
        self.store.open(
            "position-secret", LongOptionPosition(
                self.c, D("1.20"), 1, D("2.29"), D("2.29"),
            ), self.t - timedelta(minutes=5),
        )
        self.cutoff = self.t + timedelta(hours=5)

    def queue(self):
        q = OptionQuote(self.c, D("2.00"), D("2.03"), 4,
                        self.t, "synthetic", "EXACT_NBBO")
        item = self.store.on_quote(
            "position-secret", q, self.t, timedelta(seconds=3), D("35"),
            last_trading_at=self.cutoff,
        )
        self.assertIsNotNone(item.notification_id)
        return item.notification_id

    def audit(self, seconds=0, **kwargs):
        return read_outbox_audit(
            self.path, now=self.t + timedelta(seconds=seconds), **kwargs
        )

    def test_empty_db_never_implies_phone_receipt(self):
        r = self.audit()
        self.assertEqual(r["outbox_rows_checked"], 0)
        self.assertFalse(r["manual_reconciliation_required"])
        self.assertFalse(r["phone_delivery_verified"])
        self.assertFalse(r["broker_fill_verified"])

    def test_pending_and_claimed_are_not_fills(self):
        self.queue()
        pending = self.audit()
        self.assertEqual(pending["states"], {"PENDING": 1})
        claimed = self.store.revalidation_candidates(
            "position-secret", now=self.t + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )
        self.assertEqual(len(claimed), 1)
        before = sha256(self.path.read_bytes()).digest()
        report = self.audit(seconds=1)
        self.assertEqual(sha256(self.path.read_bytes()).digest(), before)
        self.assertEqual(report["states"], {"CLAIMED": 1})
        self.assertEqual(report["claimed_without_provider_ack"], 1)
        self.assertTrue(report["manual_reconciliation_required"])
        self.assertEqual(report["expired_or_unqualified_claims"], 0)
        self.assertFalse(report["automatic_retry_authorized"])
        as_text = json.dumps(report)
        self.assertNotIn("claim_token", as_text)
        self.assertNotIn(claimed[0]["claim_token"], as_text)
        self.assertNotIn("position-secret", as_text)
        # No report operation may secretly consume a new notification.
        self.assertEqual(self.store.db.execute(
            "SELECT state FROM outbox").fetchone()["state"], "CLAIMED")

    def test_expired_claims_need_manual_reconciliation_never_auto_retry(self):
        self.queue()
        self.store.revalidation_candidates(
            "position-secret", now=self.t,
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )
        r = self.audit(seconds=5)
        self.assertEqual(r["expired_or_unqualified_claims"], 1)
        self.assertFalse(r["automatic_retry_authorized"])

    def test_claim_invalidated_by_new_quote_still_requires_reconciliation(self):
        self.queue()
        self.store.revalidation_candidates(
            "position-secret", now=self.t,
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )
        lower = OptionQuote(
            self.c, D("1.21"), D("1.25"), 4,
            self.t + timedelta(seconds=1), "synthetic", "EXACT_NBBO",
        )
        self.store.on_quote(
            "position-secret", lower, self.t + timedelta(seconds=1),
            timedelta(seconds=3), D("35"), last_trading_at=self.cutoff,
        )
        report = self.audit(seconds=2)
        self.assertEqual(report["states"], {"INVALIDATED_PRICE_REVERSAL": 1})
        self.assertEqual(report["claimed_without_provider_ack"], 1)
        self.assertTrue(report["manual_reconciliation_required"])
        self.assertFalse(report["automatic_retry_authorized"])
        self.assertFalse(report["phone_delivery_verified"])

    def test_provider_accepted_is_not_phone_delivery(self):
        notice = self.queue()
        claim = self.store.revalidation_candidates(
            "position-secret", now=self.t,
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )[0]
        self.assertTrue(self.store.record_provider_acceptance(
            notice, claim_token=claim["claim_token"],
            accepted_at=self.t + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.cutoff, provider_accepted=True,
            provider_receipt_id="secret-external-provider-ack",
        ))
        r = self.audit(seconds=1)
        self.assertEqual(r["states"], {"ACCEPTED_BY_PROVIDER": 1})
        self.assertFalse(r["phone_delivery_verified"])
        self.assertFalse(r["broker_fill_verified"])
        self.assertNotIn("secret-external-provider-ack", json.dumps(r))

    def test_missing_or_symlink_db_does_not_create_or_follow(self):
        absent = Path(self.tmp.name) / "missing.sqlite"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            read_outbox_audit(absent, now=self.t)
        self.assertFalse(absent.exists())
        link = Path(self.tmp.name) / "link.sqlite"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            read_outbox_audit(link, now=self.t)

    def test_unknown_position_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.audit(position_id="unknown")

    def test_unknown_state_fails_closed(self):
        notice = self.queue()
        self.store.db.execute(
            "UPDATE outbox SET state='UNKNOWN' WHERE id=?", (notice,)
        )
        with self.assertRaisesRegex(ValueError, "unknown outbox"):
            self.audit()

    def test_accepted_without_ack_fails_closed(self):
        notice = self.queue()
        self.store.db.execute(
            "UPDATE outbox SET state='ACCEPTED_BY_PROVIDER' WHERE id=?", (notice,)
        )
        with self.assertRaisesRegex(ValueError, "lacks ACK"):
            self.audit()

    def test_claim_missing_timestamp_fails_closed(self):
        self.queue()
        self.store.db.execute(
            "UPDATE outbox SET state='CLAIMED',claimed_at=NULL"
        )
        with self.assertRaisesRegex(ValueError, "missing claim timestamp"):
            self.audit()

    def test_malformed_ack_fails_closed_even_if_claim_was_invalidated(self):
        notice = self.queue()
        self.store.revalidation_candidates(
            "position-secret", now=self.t,
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )
        self.store.db.execute(
            "UPDATE outbox SET state='INVALIDATED_PRICE_REVERSAL'"
        )
        self.store.db.execute(
            "INSERT INTO provider_ack_events VALUES (?,?,?,?)",
            (notice, "not-a-timestamp", "0" * 64, 1),
        )
        with self.assertRaisesRegex(ValueError, "timestamp"):
            self.audit(seconds=2)

    def test_partial_non_tegara_schema_never_produces_clean_report(self):
        partial = Path(self.tmp.name) / "partial.sqlite"
        with closing(sqlite3.connect(partial)) as db:
            db.execute("CREATE TABLE positions(x)")
            db.execute("CREATE TABLE outbox("
                       "id,position_id,state,claimed_at,quote_expires_at,"
                       "contract_cutoff_at)")
            db.execute("CREATE TABLE provider_ack_events(notice_id)")
        with self.assertRaisesRegex(ValueError, "schema"):
            read_outbox_audit(partial, now=self.t)

    def test_symlink_swap_during_open_is_rejected_atomically(self):
        other = Path(self.tmp.name) / "original.sqlite"
        native_open = os.open

        def replace_with_symlink(path, flags, **kwargs):
            if path == "positions.sqlite" and not other.exists():
                self.path.rename(other)
                self.path.symlink_to(other)
            return native_open(path, flags, **kwargs)

        with patch("tegara.outbox_audit.os.open", side_effect=replace_with_symlink):
            with self.assertRaises(OSError):
                self.audit()
        self.assertTrue(other.exists())

    def test_same_size_sqlite_write_with_restored_mtime_is_rejected(self):
        # A hostile same-UID writer is outside the file permission boundary;
        # the offline audit must nevertheless detect a changed ctime if
        # mtime is restored after an in-place same-size mutation.
        import tegara.outbox_audit as audit_module

        source = self.path
        saved = source.stat()
        original = source.read_bytes()
        self.assertIn(b"position-secret", original)
        real_fdopen = os.fdopen
        test_case = self
        was_modified = []

        class MutateAfterRead:
            def __init__(self, file):
                self.file = file

            def __enter__(self):
                self.file.__enter__()
                return self

            def __exit__(self, *args):
                return self.file.__exit__(*args)

            def fileno(self):
                return self.file.fileno()

            def read(self, length):
                old_snapshot = self.file.read(length)
                test_case.assertNotIn(b"position-secreu", old_snapshot)
                with source.open("r+b", buffering=0) as writer:
                    writer.seek(original.index(b"position-secret"))
                    writer.write(b"position-secreu")
                # Restore the exact mtime and size so the previous check
                # incorrectly accepted the now-stale snapshot.
                os.utime(source, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                was_modified.append(True)
                return old_snapshot

        with patch.object(audit_module.os, "fdopen",
                          side_effect=lambda fd, *a, **kw: MutateAfterRead(
                              real_fdopen(fd, *a, **kw))):
            with self.assertRaisesRegex(ValueError, "changed during offline"):
                audit_module._offline_snapshot_bytes(source)
        self.assertEqual(was_modified, [True])
        actual = source.stat()
        self.assertEqual(actual.st_size, saved.st_size)
        self.assertEqual(actual.st_mtime_ns, saved.st_mtime_ns)
        self.assertNotEqual(actual.st_ctime_ns, saved.st_ctime_ns)
        self.assertNotEqual(source.read_bytes(), original)

    def test_wal_sidecar_requires_offline_quiescent_database(self):
        sidecar = Path(str(self.path.resolve()) + "-wal")
        sidecar.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "sidecar"):
            self.audit()
        sidecar.unlink()

    def test_provider_accepted_too_late_without_ack_fails_closed(self):
        self.queue()
        self.store.db.execute(
            "UPDATE outbox SET state='PROVIDER_ACCEPTED_TOO_LATE'"
        )
        with self.assertRaisesRegex(ValueError, "lacks ACK"):
            self.audit()

    def test_provider_ack_timeliness_must_match_recorded_state(self):
        notice = self.queue()
        claim = self.store.revalidation_candidates(
            "position-secret", now=self.t,
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )[0]
        self.store.record_provider_acceptance(
            notice, claim_token=claim["claim_token"],
            accepted_at=self.t + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.cutoff, provider_accepted=True,
            provider_receipt_id="ack-only",
        )
        self.store.db.execute(
            "UPDATE outbox SET state='PROVIDER_ACCEPTED_TOO_LATE'"
        )
        with self.assertRaisesRegex(ValueError, "contradicts"):
            self.audit(seconds=2)

    def test_intermediate_symlink_directory_is_rejected(self):
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.tmp.name, target_is_directory=True)
        with self.assertRaises(OSError):
            read_outbox_audit(alias / "positions.sqlite", now=self.t)

    def test_substituted_intermediate_directory_symlink_is_rejected(self):
        intermediate = Path(self.tmp.name) / "middle"
        intermediate.mkdir()
        file = intermediate / "copied.sqlite"
        file.write_bytes(self.path.read_bytes())
        source_open = os.open
        moved = Path(self.tmp.name) / "moved-middle"

        def swap_during_dir_walk(path, flags, **kwargs):
            if path == "middle" and not moved.exists():
                intermediate.rename(moved)
                intermediate.symlink_to(moved, target_is_directory=True)
            return source_open(path, flags, **kwargs)

        with patch("tegara.outbox_audit.os.open", side_effect=swap_during_dir_walk):
            with self.assertRaises(OSError):
                read_outbox_audit(file, now=self.t)

    def test_invalidated_ack_timely_true_is_rejected(self):
        notice = self.queue()
        claim = self.store.revalidation_candidates(
            "position-secret", now=self.t, max_quote_age=timedelta(seconds=3),
            last_trading_at=self.cutoff,
        )[0]
        self.store.on_quote(
            "position-secret",
            OptionQuote(self.c, D("1.21"), D("1.25"), 4,
                        self.t + timedelta(seconds=1),
                        "synthetic", "EXACT_NBBO"),
            self.t + timedelta(seconds=1), timedelta(seconds=3), D("35"),
            last_trading_at=self.cutoff,
        )
        self.store.record_provider_acceptance(
            notice, claim_token=claim["claim_token"],
            accepted_at=self.t + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
            provider_accepted=True, provider_receipt_id="later-ack",
        )
        self.store.db.execute(
            "UPDATE provider_ack_events SET considered_timely=1"
        )
        with self.assertRaisesRegex(ValueError, "invalidated"):
            self.audit(seconds=2)

    def test_unrecognized_invalidated_prefix_never_looks_clean(self):
        self.queue()
        self.store.db.execute(
            "UPDATE outbox SET state='INVALIDATED_BOGUS'"
        )
        with self.assertRaisesRegex(ValueError, "unknown outbox"):
            self.audit()

    def test_cli_strict_nonzero_unacknowledged_and_no_mutation(self):
        self.queue()
        self.store.revalidation_candidates(
            "position-secret", now=self.t,
            max_quote_age=timedelta(seconds=3), last_trading_at=self.cutoff,
        )
        original = sha256(self.path.read_bytes()).digest()
        command = [
            sys.executable, "-m", "tegara.outbox_audit",
            "--db", str(self.path), "--strict",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(sha256(self.path.read_bytes()).digest(), original)
        self.assertTrue(json.loads(result.stdout)["manual_reconciliation_required"])
        self.assertFalse(json.loads(result.stdout)["phone_delivery_verified"])


if __name__ == "__main__":
    unittest.main()
