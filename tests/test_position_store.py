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
                                   timedelta(seconds=3), D(target),
                                   last_trading_at=self.now + timedelta(hours=5))

    def claim(self, offset=0):
        candidates = self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=offset),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
        )
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def test_created_db_is_private(self):
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_direct_position_store_refuses_symlink_database(self):
        victim = Path(self.tmp.name) / "unrelated-file.txt"
        victim.write_text("keep intact", encoding="utf-8")
        link = Path(self.tmp.name) / "external-db-link"
        link.symlink_to(victim)
        with self.assertRaisesRegex(ValueError, "symlinks are refused"):
            PositionStore(link)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep intact")

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

    def test_degraded_feed_cancels_pending_and_requires_fresh_revalidation(self):
        original = self.check(self.quote())
        untrusted = self.quote(bid="1.20", offset=1, feed="INDICATIVE")
        self.assertEqual(self.check(untrusted, offset=1).decision,
                         MonitorDecision.UNQUALIFIED_FEED)
        self.assertEqual(self.store.pending("entry-1"), [])
        self.assertEqual(
            self.store.db.execute(
                "SELECT state FROM outbox WHERE id=?", (original.notification_id,)
            ).fetchone()["state"], "INVALIDATED_FEED_DEGRADED"
        )
        # An eligible quote at the same time as the invalid indicative
        # observation is valid because the invalid observation cannot claim
        # ownership of the accepted-quote watermark.
        self.assertEqual(self.check(self.quote(offset=1), offset=1).decision,
                         MonitorDecision.ALERT_QUEUED)
        self.assertEqual(self.check(self.quote(offset=2), offset=2).decision,
                         MonitorDecision.ALREADY_NOTIFIED)

    def test_degraded_feed_cancels_already_claimed_notice(self):
        a = self.check(self.quote())
        claim = self.claim()
        bad = self.quote(bid="1.21", offset=1, feed="INDICATIVE")
        self.assertEqual(self.check(bad, offset=1).decision,
                         MonitorDecision.UNQUALIFIED_FEED)
        self.assertEqual(self.store.pending("entry-1"), [])
        self.assertFalse(self.store.record_provider_acceptance(
            a.notification_id, claim_token=claim["claim_token"],
            accepted_at=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True, provider_receipt_id="delayed-fake-ack",
        ))
        self.assertEqual(
            self.store.db.execute(
                "SELECT state FROM outbox WHERE id=?", (a.notification_id,)
            ).fetchone()["state"], "INVALIDATED_FEED_DEGRADED"
        )

    def test_older_bad_feed_cannot_cancel_newer_alert(self):
        a = self.check(self.quote(offset=1), offset=1)
        self.assertEqual(a.decision, MonitorDecision.ALERT_QUEUED)
        untrusted_old = self.quote(bid="1.20", offset=0, feed="INDICATIVE")
        self.assertEqual(self.check(untrusted_old, offset=1).decision,
                         MonitorDecision.UNQUALIFIED_FEED)
        self.assertEqual(
            [r["id"] for r in self.store.pending("entry-1")],
            [a.notification_id],
        )

    def test_wrong_contract_quote_does_not_cancel_original_notice(self):
        a = self.check(self.quote())
        bad = self.quote(offset=1, contract=replace(self.contract, right="P"))
        self.assertEqual(self.check(bad, offset=1).decision,
                         MonitorDecision.CONTRACT_MISMATCH)
        self.assertEqual(
            [r["id"] for r in self.store.pending("entry-1")],
            [a.notification_id],
        )

    def test_bad_feed_future_timestamp_cannot_poison_quote_watermark(self):
        prior = self.check(self.quote())
        bogus_future = self.quote(
            offset=3600, bid="99", feed="INDICATIVE"
        )
        self.assertEqual(
            self.check(bogus_future, offset=1).decision,
            MonitorDecision.UNQUALIFIED_FEED,
        )
        self.assertEqual(self.store.pending("entry-1"), [])
        # Last genuinely accepted market observation was T0. The fake
        # future timestamp must NOT block T0+2s real eligible recovery.
        state = self.store.db.execute(
            "SELECT last_quote_at FROM monitor WHERE position_id='entry-1'"
        ).fetchone()
        self.assertEqual(state["last_quote_at"], self.now.isoformat(
            timespec="microseconds"
        ))
        recovered = self.check(self.quote(offset=2), offset=2)
        self.assertEqual(recovered.decision, MonitorDecision.ALERT_QUEUED)
        self.assertNotEqual(recovered.notification_id, prior.notification_id)

    def test_unknown_cutoff_future_quote_does_not_poison_watermark(self):
        self.check(self.quote())
        bad = self.quote(offset=3600)
        self.assertEqual(
            self.store.on_quote(
                "entry-1", bad, self.now + timedelta(seconds=1),
                timedelta(seconds=3), D("105"), last_trading_at=None
            ).decision,
            MonitorDecision.CUTOFF_UNVERIFIED,
        )
        self.assertEqual(
            self.check(self.quote(offset=2), offset=2).decision,
            MonitorDecision.ALERT_QUEUED
        )

    def test_newer_transport_stale_quote_invalidates_previous_alert(self):
        a = self.check(self.quote())
        bad = self.quote(offset=1)
        self.assertEqual(self.check(bad, offset=10).decision,
                         MonitorDecision.STALE_QUOTE)
        self.assertEqual(self.store.pending("entry-1"), [])
        self.assertEqual(
            self.store.db.execute(
                "SELECT state FROM outbox WHERE id=?", (a.notification_id,)
            ).fetchone()["state"], "INVALIDATED_FEED_STALE"
        )
        self.assertEqual(self.check(self.quote(offset=2), offset=2).decision,
                         MonitorDecision.ALERT_QUEUED)

    def test_newer_unknown_cutoff_disarms_existing_pending_alert(self):
        a = self.check(self.quote())
        self.assertEqual(self.store.on_quote(
            "entry-1", self.quote(offset=1), self.now + timedelta(seconds=1),
            timedelta(seconds=3), D("105"), last_trading_at=None,
        ).decision, MonitorDecision.CUTOFF_UNVERIFIED)
        self.assertEqual(self.store.pending("entry-1"), [])
        self.assertEqual(
            self.store.db.execute(
                "SELECT state FROM outbox WHERE id=?", (a.notification_id,)
            ).fetchone()["state"], "INVALIDATED_CUTOFF_UNKNOWN"
        )

    def test_valid_dip_rearms(self):
        self.check(self.quote())
        self.assertEqual(self.check(self.quote(bid="1.20", offset=1), offset=1).decision,
                         MonitorDecision.TARGET_NOT_REACHED)
        self.assertEqual(self.check(self.quote(offset=2), offset=2).decision,
                         MonitorDecision.ALERT_QUEUED)
        self.assertEqual(len(self.store.pending("entry-1")), 1)

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
            self.store.record_provider_acceptance(
                notice.notification_id, accepted_at=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(hours=5),
                provider_accepted=True,
                claim_token="0" * 32, provider_receipt_id="fixture-ack",
            )
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
        claim = self.claim()
        self.store.record_provider_acceptance(
            a.notification_id, accepted_at=self.now,
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True,
            claim_token=claim["claim_token"], provider_receipt_id="fixture-ack",
        )
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
        result = self.store.on_quote(
            "entry-1", q, after, timedelta(seconds=3), D("105"),
            last_trading_at=self.now + timedelta(hours=5)
        )
        self.assertEqual(result.decision, MonitorDecision.EXPIRED)
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_monitor_requires_cutoff_and_abstains_on_after_cutoff(self):
        missing = self.store.on_quote(
            "entry-1", self.quote(), self.now, timedelta(seconds=3), D("105")
        )
        self.assertEqual(missing.decision, MonitorDecision.CUTOFF_UNVERIFIED)
        cutoff = self.now - timedelta(minutes=1)
        expired = self.store.on_quote(
            "entry-1", self.quote(), self.now, timedelta(seconds=3), D("105"),
            last_trading_at=cutoff,
        )
        self.assertEqual(expired.decision, MonitorDecision.AFTER_CUTOFF)
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_provider_acceptance_requires_actual_explicit_ack(self):
        a = self.check(self.quote())
        with self.assertRaisesRegex(ValueError, "provider acceptance"):
            self.store.record_provider_acceptance(
                a.notification_id, accepted_at=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(hours=5),
                provider_accepted=False,
                claim_token="0" * 32, provider_receipt_id="fixture-ack",
            )
        self.assertEqual(len(self.store.pending("entry-1")), 1)

    def test_provider_acceptance_requires_cutoff(self):
        a = self.check(self.quote())
        with self.assertRaisesRegex(ValueError, "cutoff unknown"):
            self.store.record_provider_acceptance(
                a.notification_id, accepted_at=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=None,
                provider_accepted=True,
                claim_token="0" * 32, provider_receipt_id="fixture-ack",
            )
        self.assertEqual(len(self.store.pending("entry-1")), 1)

    def test_late_provider_ack_is_recorded_as_stale_not_delivered(self):
        a = self.check(self.quote())
        claim = self.claim()
        result = self.store.record_provider_acceptance(
            a.notification_id, accepted_at=self.now + timedelta(seconds=5),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True,
            claim_token=claim["claim_token"], provider_receipt_id="fixture-ack",
        )
        self.assertFalse(result)
        self.assertEqual(self.store.pending("entry-1"), [])
        row = self.store.db.execute("SELECT state FROM outbox WHERE id=?",
                                    (a.notification_id,)).fetchone()
        self.assertEqual(row["state"], "PROVIDER_ACCEPTED_TOO_LATE")

    def test_provider_ack_does_not_claim_phone_receipt_or_fill(self):
        a = self.check(self.quote())
        claim = self.claim()
        accepted = self.store.record_provider_acceptance(
            a.notification_id, accepted_at=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True,
            claim_token=claim["claim_token"], provider_receipt_id="fixture-ack",
        )
        self.assertTrue(accepted)
        state = self.store.db.execute("SELECT state FROM outbox WHERE id=?",
                                      (a.notification_id,)).fetchone()["state"]
        self.assertEqual(state, "ACCEPTED_BY_PROVIDER")
        self.assertEqual(self.store.get("entry-1").open_quantity, 3)

    def test_outbox_revalidation_lists_fresh_candidates_without_delivering(self):
        alert = self.check(self.quote())
        candidates = self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
        )
        self.assertEqual([c["id"] for c in candidates], [alert.notification_id])
        self.assertEqual(len(self.store.pending("entry-1")), 0)
        self.assertEqual(candidates[0]["state"], "CLAIMED")
        self.assertEqual(self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
        ), [])
        self.assertEqual(self.store.get("entry-1").open_quantity, 3)

    def test_outbox_revalidation_expires_stale_alert(self):
        alert = self.check(self.quote())
        candidates = self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=4),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
        )
        self.assertEqual(candidates, [])
        self.assertEqual(self.store.pending("entry-1"), [])
        with self.assertRaises(KeyError):
            self.store.record_provider_acceptance(
                alert.notification_id, accepted_at=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(hours=5),
                provider_accepted=True,
                claim_token="0" * 32, provider_receipt_id="fixture-ack",
            )

    def test_outbox_revalidation_expires_at_cutoff(self):
        self.check(self.quote())
        candidates = self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(milliseconds=1),
        )
        self.assertEqual(candidates, [])
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_outbox_revalidation_without_cutoff_fails_closed(self):
        alert = self.check(self.quote())
        result = self.store.revalidation_candidates(
            "entry-1", now=self.now, max_quote_age=timedelta(seconds=3),
            last_trading_at=None,
        )
        self.assertEqual(result, [])
        self.assertEqual(self.store.pending("entry-1")[0]["id"], alert.notification_id)

    def test_future_dated_pending_alert_invalidated(self):
        self.check(self.quote())
        result = self.store.revalidation_candidates(
            "entry-1", now=self.now - timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
        )
        self.assertEqual(result, [])
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_outbox_revalidation_wrong_expiry_cutoff_fails(self):
        self.check(self.quote())
        with self.assertRaisesRegex(ValueError, "NY expiry"):
            self.store.revalidation_candidates(
                "entry-1", now=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(days=2),
            )

    def test_price_reversal_invalidates_old_notification_before_acceptance(self):
        alert = self.check(self.quote())
        self.assertEqual(self.check(self.quote(bid="1.20", offset=1), offset=1).decision,
                         MonitorDecision.TARGET_NOT_REACHED)
        self.assertEqual(self.store.pending("entry-1"), [])
        self.assertEqual(self.store.db.execute(
            "SELECT state FROM outbox WHERE id=?", (alert.notification_id,)
        ).fetchone()["state"], "INVALIDATED_PRICE_REVERSAL")
        new = self.check(self.quote(offset=2), offset=2)
        self.assertEqual(new.decision, MonitorDecision.ALERT_QUEUED)
        self.assertNotEqual(new.notification_id, alert.notification_id)

    def test_liquidity_loss_invalidates_old_notification(self):
        alert = self.check(self.quote())
        result = self.check(self.quote(offset=1, size=0), offset=1)
        self.assertEqual(result.decision, MonitorDecision.INSUFFICIENT_SIZE)
        self.assertEqual(self.store.pending("entry-1"), [])
        status = self.store.db.execute(
            "SELECT state FROM outbox WHERE id=?", (alert.notification_id,)
        ).fetchone()["state"]
        self.assertEqual(status, "INVALIDATED_LIQUIDITY")

    def test_target_change_invalidates_old_pending_notification(self):
        old = self.check(self.quote())
        new = self.check(self.quote(offset=1), offset=1, target="100")
        self.assertEqual(new.decision, MonitorDecision.ALERT_QUEUED)
        self.assertEqual([r["id"] for r in self.store.pending("entry-1")],
                         [new.notification_id])
        self.assertEqual(self.store.db.execute(
            "SELECT state FROM outbox WHERE id=?", (old.notification_id,)
        ).fetchone()["state"], "INVALIDATED_TARGET_CHANGE")

    def test_original_quote_ttl_cannot_be_extended_by_caller(self):
        self.check(self.quote())
        result = self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=4),
            max_quote_age=timedelta(hours=1),
            last_trading_at=self.now + timedelta(hours=5),
        )
        self.assertEqual(result, [])
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_original_cutoff_cannot_be_extended_by_caller(self):
        self.store.on_quote(
            "entry-1", self.quote(), self.now, timedelta(seconds=60),
            D("105"), last_trading_at=self.now + timedelta(seconds=1),
        )
        result = self.store.revalidation_candidates(
            "entry-1", now=self.now + timedelta(seconds=2),
            max_quote_age=timedelta(seconds=60),
            last_trading_at=self.now + timedelta(hours=5),
        )
        self.assertEqual(result, [])
        self.assertEqual(self.store.pending("entry-1"), [])

    def test_provider_ack_before_claim_is_rejected(self):
        alert = self.check(self.quote())
        claim = self.claim(offset=1)
        with self.assertRaisesRegex(ValueError, "predates"):
            self.store.record_provider_acceptance(
                alert.notification_id, claim_token=claim["claim_token"],
                accepted_at=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(hours=5),
                provider_accepted=True, provider_receipt_id="test-receipt",
            )

    def test_provider_ack_receipt_is_hashed_and_time_persisted(self):
        alert = self.check(self.quote())
        claim = self.claim()
        self.assertTrue(self.store.record_provider_acceptance(
            alert.notification_id, claim_token=claim["claim_token"],
            accepted_at=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True, provider_receipt_id="external-ack-123",
        ))
        row = self.store.db.execute(
            "SELECT state,accepted_at,receipt_digest FROM outbox WHERE id=?",
            (alert.notification_id,),
        ).fetchone()
        self.assertEqual(row["state"], "ACCEPTED_BY_PROVIDER")
        self.assertIn("15:00:01", row["accepted_at"])
        self.assertEqual(len(row["receipt_digest"]), 64)
        self.assertNotIn("external-ack", row["receipt_digest"])

    def test_fake_claim_cannot_accept_alert(self):
        alert = self.check(self.quote())
        with self.assertRaises(KeyError):
            self.store.record_provider_acceptance(
                alert.notification_id, claim_token="0" * 32,
                accepted_at=self.now + timedelta(seconds=1),
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(hours=5),
                provider_accepted=True, provider_receipt_id="test-receipt",
            )

    def test_claim_invalidated_after_price_reversal(self):
        alert = self.check(self.quote())
        claim = self.claim()
        self.check(self.quote(bid="1.20", offset=1), offset=1)
        was_timely = self.store.record_provider_acceptance(
            alert.notification_id, claim_token=claim["claim_token"],
            accepted_at=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True, provider_receipt_id="test-receipt",
        )
        self.assertFalse(was_timely)
        event = self.store.db.execute(
            "SELECT considered_timely FROM provider_ack_events WHERE notice_id=?",
            (alert.notification_id,),
        ).fetchone()
        self.assertEqual(event["considered_timely"], 0)
        invalidated = self.store.db.execute(
            "SELECT state FROM outbox WHERE id=?", (alert.notification_id,)
        ).fetchone()
        self.assertEqual(invalidated["state"], "INVALIDATED_PRICE_REVERSAL")

    def test_idempotent_provider_ack_and_conflicting_replay(self):
        a = self.check(self.quote())
        claim = self.claim()
        kwargs = dict(
            claim_token=claim["claim_token"], accepted_at=self.now + timedelta(seconds=1),
            max_quote_age=timedelta(seconds=3),
            last_trading_at=self.now + timedelta(hours=5),
            provider_accepted=True, provider_receipt_id="receipt-1"
        )
        self.assertTrue(self.store.record_provider_acceptance(a.notification_id, **kwargs))
        self.assertTrue(self.store.record_provider_acceptance(a.notification_id, **kwargs))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.store.record_provider_acceptance(
                a.notification_id, **{**kwargs, "provider_receipt_id": "receipt-2"}
            )

    def test_unknown_notice_rejected(self):
        with self.assertRaises(KeyError):
            self.store.record_provider_acceptance(
                "not-a-real-notification", accepted_at=self.now,
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.now + timedelta(hours=5),
                provider_accepted=True,
                claim_token="0" * 32, provider_receipt_id="fixture-ack",
            )

    def test_bad_naive_entry_rejected(self):
        with self.assertRaises(ValueError):
            self.store.open("bad", self.position, datetime(2026, 10, 9, 15, 0))


if __name__ == "__main__":
    unittest.main()
