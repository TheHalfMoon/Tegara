"""Entire synthetic Tegara quote-to-notice-to-ledger journey tests.

All input quotes and provider ACKs are fabricated fixtures. Tests MUST NOT
send macOS, phone, network, broker or TradingView messages.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tegara import LongOptionPosition, OptionContract
from tegara.local_notify import make_manual_demo_notice
from tegara.position_store import MonitorDecision, PositionStore, PositionStatus
from tegara.quote_import import FIELDS, read_option_csv


class FullSyntheticJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "synthetic_quotes.csv"
        self.db_path = Path(self.tmp.name) / "positions.sqlite"
        self.at = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
        self.cutoff = self.at + timedelta(hours=5)
        self.contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.position = LongOptionPosition(
            self.contract, D("1.20"), 1, D("2"), D("2")
        )

    def generate(self, *, feeds=None, bids=None):
        feeds = feeds or ["EXACT_NBBO", "EXACT_NBBO", "EXACT_NBBO"]
        bids = bids or ["1.20", "1.61", "1.65"]
        with self.csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for i, (feed, bid) in enumerate(zip(feeds, bids)):
                writer.writerow({
                    "observed_at": (self.at + timedelta(seconds=i)).isoformat(),
                    "symbol": "SPY", "right": "C", "strike": "600",
                    "expiration": "2026-10-09", "multiplier": "100",
                    "bid": bid, "ask": str(D(bid) + D("0.04")),
                    "bid_size": "4", "ask_size": "4",
                    "feed_type": feed, "source": "SYNTHETIC_TEST_FIXTURE",
                })
        return read_option_csv(
            self.csv, expected_contract=self.contract, expected_quantity=1,
            last_trading_at=self.cutoff,
        )

    def test_csv_quote_to_candidate_to_simulated_provider_ack_and_manual_fill(self):
        ticks, quality = self.generate()
        self.assertFalse(quality.live_eligible)
        self.assertEqual(quality.provenance_status, "NOT_INDEPENDENTLY_VERIFIED")
        with PositionStore(self.db_path) as store:
            store.open("synthetic-position", self.position, self.at - timedelta(minutes=1))
            decisions = [
                store.on_quote(
                    "synthetic-position", tick.quote, tick.quote.observed_at,
                    timedelta(seconds=3), D("30"), last_trading_at=self.cutoff,
                )
                for tick in ticks
            ]
            self.assertEqual(
                [d.decision for d in decisions],
                [MonitorDecision.TARGET_NOT_REACHED,
                 MonitorDecision.ALERT_QUEUED,
                 MonitorDecision.ALREADY_NOTIFIED]
            )
            self.assertEqual(len(store.pending("synthetic-position")), 1)
            candidate = store.revalidation_candidates(
                "synthetic-position", now=self.at + timedelta(seconds=2),
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.cutoff,
            )
            self.assertEqual(len(candidate), 1)
            self.assertEqual(store.pending("synthetic-position"), [])
            with patch("tegara.local_notify.subprocess.run") as no_os_message:
                demo = make_manual_demo_notice(
                    self.position, manual_bid=ticks[-1].quote.bid,
                    target_net_profit=D("30"),
                )
                self.assertFalse(demo.market_data_verified)
                self.assertFalse(demo.phone_delivery_verified)
                self.assertFalse(demo.broker_execution_verified)
                no_os_message.assert_not_called()
            accepted = store.record_provider_acceptance(
                candidate[0]["id"], claim_token=candidate[0]["claim_token"],
                accepted_at=self.at + timedelta(seconds=2),
                max_quote_age=timedelta(seconds=3),
                last_trading_at=self.cutoff,
                provider_accepted=True, provider_receipt_id="FAKE_PROVIDER_ACCEPTANCE",
            )
            self.assertTrue(accepted)
            self.assertEqual(store.get("synthetic-position").open_quantity, 1)
            closed, pnl, recorded = store.confirm_manual_close(
                "synthetic-position", "FAKE_MANUAL_FILL",
                self.contract, 1, D("1.65"), D("2"),
                self.at + timedelta(seconds=3),
            )
            self.assertTrue(recorded)
            self.assertEqual(closed.status, PositionStatus.CLOSED)
            self.assertEqual(closed.open_quantity, 0)
            self.assertEqual(pnl, D("41.00"))

    def test_indicative_quotes_cannot_trigger_option_target_alert(self):
        ticks, quality = self.generate(feeds=["INDICATIVE"] * 3)
        self.assertEqual(quality.indicative_rows, 3)
        with PositionStore() as store:
            store.open("a", self.position, self.at - timedelta(minutes=1))
            for tick in ticks:
                result = store.on_quote(
                    "a", tick.quote, tick.quote.observed_at,
                    timedelta(seconds=3), D("30"), last_trading_at=self.cutoff,
                )
                self.assertEqual(result.decision, MonitorDecision.UNQUALIFIED_FEED)
            self.assertEqual(store.pending("a"), [])

    def test_absent_cutoff_causes_suppression_end_to_end(self):
        ticks, _ = self.generate()
        with PositionStore() as store:
            store.open("a", self.position, self.at - timedelta(minutes=1))
            for tick in ticks:
                result = store.on_quote(
                    "a", tick.quote, tick.quote.observed_at,
                    timedelta(seconds=3), D("30"), last_trading_at=None
                )
                self.assertEqual(result.decision, MonitorDecision.CUTOFF_UNVERIFIED)
            self.assertEqual(store.pending("a"), [])

    def test_quote_data_gap_ends_replay_without_claiming_fill(self):
        from tegara.quote_replay import ReplayRules, ReplayOutcome, replay_long_option
        ticks, quality = self.generate()
        self.assertFalse(quality.live_eligible)
        rules = ReplayRules(
            contract=self.contract, quantity=1,
            entry_fee_per_contract=D("2"), exit_fee_per_contract=D("2"),
            entry_slippage_per_share=D("0"), exit_slippage_per_share=D("0"),
            target_net_profit=D("30"), maximum_net_loss=D("60"),
            deadline=self.at + timedelta(minutes=5),
            max_quote_gap=timedelta(milliseconds=500),
            last_trading_at=self.cutoff,
        )
        result = replay_long_option(ticks, rules, provenance_approved=True)
        self.assertEqual(result.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)
        self.assertIsNone(result.simulated_net_pnl)

    def test_no_synthetic_receipt_turns_into_phone_or_derayah_execution(self):
        ticks, _ = self.generate()
        with PositionStore() as store:
            store.open("a", self.position, self.at - timedelta(minutes=1))
            alert = store.on_quote(
                "a", ticks[1].quote, ticks[1].quote.observed_at,
                timedelta(seconds=3), D("30"), last_trading_at=self.cutoff,
            )
            self.assertEqual(alert.decision, MonitorDecision.ALERT_QUEUED)
            self.assertEqual(store.get("a").open_quantity, 1)
            self.assertEqual(len(store.pending("a")), 1)


if __name__ == "__main__":
    unittest.main()
