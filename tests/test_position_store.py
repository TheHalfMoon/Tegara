"""Regression tests for local position reconciliation and sell-to-close alert state."""
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from tegara import LongOptionPosition, OptionContract, OptionQuote
from tegara.position_store import MonitorDecision, PositionStatus, PositionStore


class PositionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "trades.sqlite"
        self.now = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
        self.contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.position = LongOptionPosition(self.contract, D("1.20"), 3,
                                           D("2.29"), D("2.29"))
        self.store = PositionStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.store.open("entry-1", self.position, self.now - timedelta(minutes=5))

    def quote(self, bid="1.62", offset=0, feed="EXACT_NBBO", size=5, contract=None):
        return OptionQuote(contract or self.contract, D(bid), D(bid) + D("0.03"),
                           size, self.now + timedelta(seconds=offset),
                           "trusted-test-fixture", feed)

    def check(self, quote, offset=0, target="105"):
        return self.store.on_quote("entry-1", quote,
                                   self.now + timedelta(seconds=offset),
                                   timedelta(seconds=3), D(target))

    def test_created_db_is_private(self):
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_existing_database_permissions_hardened_on_reopen(self):
        if os.name == "posix":
            os.chmod(self.path, 0o644)
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
            with PositionStore(self.path) as reopened:
                self.assertEqual(reopened.get("entry-1").open_quantity, 3)
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_open_denied(self):
        self.assertEqual(self.store.get("entry-1").status, PositionStatus.OPEN)
        with self.assertRaises(Exception):
            self.store.open("entry-1", self.position, self.now)

    def test_crossing_queues_once(self):
        alert = self.check(self.quote())
        self.assertEqual(alert.decision, MonitorDecision.ALERT_QUEUED)
        self.assertTrue(alert.notification_id)
        self.assertEqual(self.check(self.quote()).decision, MonitorDecision.OUT_OF_ORDER)
        self.assertEqual(self.check(self.quote(offset=1), offset=1).decision,
                         MonitorDecision.ALREADY_NOTIFIED)
        self.assertEqual(len(self.store.pending("entry-1")), 1)

    def test_untrusted_quote_does_not_rearm(self):
        self.check(self.quote())
        untrusted = self.quote(bid="1.20", offset=1, feed="INDICATIVE")
        self.assertEqual(self.check(untrusted, offset=1).decision,
                         MonitorDecision.UNQUALIFIED_FEED)
        self.assertEqual(self.check(self.quote(offset=2), offset=2).decision,
                         MonitorDecision.ALREADY_NOTIFIED)

    def test_valid_dip_rearms(self):
        self.check(self.quote())
        self.assertEqual(self.check(self.quote(bid="1.20", offset=1), offset=1).decision,
                         MonitorDecision.TARGET_NOT_REACHED)
        self.assertEqual(self.check(self.quote(offset=2), offset=2).decision,
                         MonitorDecision.ALERT_QUEUED)
        self.assertEqual(len(self.store.pending("entry-1")), 2)

    def test_mismatched_and_stale_quote(self):
        wrong = self.quote(contract=replace(self.contract, right="P"))
        self.assertEqual(self.check(wrong).decision, MonitorDecision.CONTRACT_MISMATCH)
        self.assertEqual(self.check(self.quote(offset=-10)).decision,
                         MonitorDecision.STALE_QUOTE)
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_insufficient_contracts_at_bid(self):
        self.assertEqual(self.check(self.quote(size=2)).decision,
                         MonitorDecision.INSUFFICIENT_SIZE)

    def test_partial_fill_invalidates_pending_alert(self):
        notice = self.check(self.quote())
        self.assertEqual(notice.decision, MonitorDecision.ALERT_QUEUED)
        self.store.confirm_manual_close(
            "entry-1", "fill-new", self.contract, 1,
            D("1.62"), D("2.29"), self.now
        )
        self.assertEqual(self.store.pending("entry-1"), [])
        with self.assertRaises(KeyError):
            self.store.mark_delivered(notice.notification_id)
        # A fresh contract-matching quote is needed for the remaining quantity.
        newer = self.check(self.quote(offset=1), offset=1)
        self.assertEqual(newer.decision, MonitorDecision.TARGET_NOT_REACHED)

    def test_changing_target_rearms_using_fresh_quote(self):
        self.check(self.quote())
        # Different target amount changes the trigger; using the same quote is
        # still disallowed as a replay.
        self.assertEqual(self.check(self.quote(), target="100").decision,
                         MonitorDecision.OUT_OF_ORDER)
        self.assertEqual(self.check(self.quote(offset=1), offset=1, target="100").decision,
                         MonitorDecision.ALERT_QUEUED)

    def test_partial_sell_confirmed_separately(self):
        snapshot, profit, new = self.store.confirm_manual_close(
            "entry-1", "fill-1", self.contract, 1, D("1.62"), D("2.29"), self.now
        )
        self.assertTrue(new)
        self.assertEqual(profit, D("37.42"))
        self.assertEqual(snapshot.open_quantity, 2)
        self.assertEqual(snapshot.status, PositionStatus.PARTIALLY_CLOSED)
        again = self.store.confirm_manual_close(
            "entry-1", "fill-1", self.contract, 1, D("1.62"), D("2.29"), self.now
        )
        self.assertFalse(again[2])
        self.assertEqual(again[0].realized_net_pnl, D("37.42"))

    def test_fill_id_conflict_denied(self):
        self.store.confirm_manual_close("entry-1", "fill-1",
                                        self.contract, 1, D("1.8"), D("2"), self.now)
        with self.assertRaises(ValueError):
            self.store.confirm_manual_close("entry-1", "fill-1",
                                            self.contract, 2, D("1.8"), D("2"), self.now)

    def test_oversell_denied(self):
        with self.assertRaises(ValueError):
            self.store.confirm_manual_close("entry-1", "fill-2",
                                            self.contract, 4, D("1.9"), D("2"), self.now)

    def test_cross_contract_fill_denied(self):
        with self.assertRaises(ValueError):
            self.store.confirm_manual_close("entry-1", "fill-2",
                                            replace(self.contract, right="P"),
                                            1, D("1.9"), D("2"), self.now)

    def test_money_rejects_nan_infinity_negative(self):
        for price in (D("NaN"), D("Infinity"), D("-1")):
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    self.store.confirm_manual_close("entry-1", "bad", self.contract,
                                                    1, price, D("2"), self.now)

    def test_opened_after_fill_denied(self):
        with self.assertRaises(ValueError):
            self.store.confirm_manual_close("entry-1", "early", self.contract, 1,
                                            D("1.9"), D("2"), self.now - timedelta(hours=1))

    def test_closed_never_queues_sell_alert(self):
        snap, _, _ = self.store.confirm_manual_close(
            "entry-1", "all", self.contract, 3, D("1.62"), D("2.29"), self.now
        )
        self.assertEqual(snap.status, PositionStatus.CLOSED)
        self.assertEqual(self.check(self.quote()).decision, MonitorDecision.CLOSED)
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_delivered_notification_does_not_close_position(self):
        a = self.check(self.quote())
        self.store.mark_delivered(a.notification_id)
        self.assertEqual(self.store.pending("entry-1"), [])
        self.assertEqual(self.store.get("entry-1").open_quantity, 3)

    def test_pending_alert_persists_across_restart(self):
        a = self.check(self.quote())
        self.store.close()
        self.store = PositionStore(self.path)
        self.assertEqual(self.store.pending("entry-1")[0]["id"], a.notification_id)
        self.assertEqual(self.check(self.quote()).decision, MonitorDecision.OUT_OF_ORDER)
        self.assertEqual(self.check(self.quote(offset=1), offset=1).decision,
                         MonitorDecision.ALREADY_NOTIFIED)

    def test_expired_contract_no_alert(self):
        after = self.now + timedelta(days=2)
        q = self.quote(offset=2 * 86400)
        result = self.store.on_quote("entry-1", q, after, timedelta(seconds=3), D("105"))
        self.assertEqual(result.decision, MonitorDecision.EXPIRED)
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_unknown_notice_rejected(self):
        with self.assertRaises(KeyError):
            self.store.mark_delivered("not-a-real-notification")

    def test_bad_naive_entry_rejected(self):
        with self.assertRaises(ValueError):
            self.store.open("bad", self.position, datetime(2026, 10, 9, 15, 0))


if __name__ == "__main__":
    unittest.main()
