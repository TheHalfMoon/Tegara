"""Synthetic delayed option quote replay: no broker, phone, OPRA or alpha."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from tegara.core import OptionContract, OptionQuote
from tegara.latency_replay import delayed_long_option
from tegara.quote_replay import ReplayOutcome, ReplayRules, ReplayTick


class LatencyReplayTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
        self.c = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.rules = ReplayRules(
            contract=self.c, quantity=1,
            entry_fee_per_contract=D("2.29"), exit_fee_per_contract=D("2.29"),
            entry_slippage_per_share=D("0"), exit_slippage_per_share=D("0"),
            target_net_profit=D("35"), maximum_net_loss=D("40"),
            deadline=self.at + timedelta(minutes=6),
            max_quote_gap=timedelta(seconds=60),
            last_trading_at=self.at + timedelta(minutes=8),
        )

    def q(self, sec, bid, ask=None, bid_size=3, ask_size=3, feed="EXACT_NBBO"):
        b = D(bid)
        a = D(ask) if ask is not None else b + D("0.04")
        return ReplayTick(
            OptionQuote(
                self.c, b, a, bid_size,
                self.at + timedelta(seconds=sec),
                "SYNTHETIC_FIXTURE", feed,
            ), ask_size,
        )

    def replay(self, ticks, **kwargs):
        opts = dict(
            entry_decision_at=self.at,
            entry_latency=timedelta(seconds=1),
            exit_latency=timedelta(seconds=1),
            allow_hypothetical=True,
        )
        opts.update(kwargs)
        return delayed_long_option(ticks, self.rules, **opts)

    def test_later_price_used_not_trigger_quote(self):
        ticks = [
            self.q(0, "1.18", "1.20"),
            self.q(1, "1.17", "1.25"),
            self.q(2, "1.65", "1.69"),
            self.q(3, "1.30", "1.35"),
        ]
        r = self.replay(ticks)
        self.assertEqual(r.outcome, ReplayOutcome.TARGET_EXIT)
        self.assertEqual(r.entry_at, self.at + timedelta(seconds=1))
        self.assertEqual(r.entry_premium, D("1.25"))
        self.assertEqual(r.trigger_at, self.at + timedelta(seconds=2))
        self.assertEqual(r.exit_at, self.at + timedelta(seconds=3))
        self.assertEqual(r.exit_premium, D("1.30"))
        self.assertEqual(r.hypothetical_net_pnl, D("0.42"))
        self.assertLess(r.hypothetical_net_pnl, self.rules.target_net_profit)

    def test_exit_price_can_turn_target_trigger_into_loss(self):
        q = [self.q(1, "1.15", "1.25"),
             self.q(2, "1.65", "1.69"),
             self.q(3, "0.80", "0.84")]
        r = self.replay(q)
        self.assertEqual(r.outcome, ReplayOutcome.TARGET_EXIT)
        self.assertEqual(r.hypothetical_net_pnl, D("-49.58"))

    def test_no_entry_quote_after_latency_abstains(self):
        r = self.replay([self.q(0, "1.18", "1.20")])
        self.assertEqual(r.outcome, ReplayOutcome.NO_ENTRY_EVIDENCE)
        self.assertIsNone(r.hypothetical_net_pnl)

    def test_no_post_trigger_quote_abstains(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.65", "1.69")])
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)
        self.assertIsNone(r.exit_at)
        self.assertIsNone(r.hypothetical_net_pnl)

    def test_quote_gap_abstains_instead_of_late_execution(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.60", "1.65"),
                         self.q(180, "2.00", "2.10")])
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)
        self.assertIsNone(r.hypothetical_net_pnl)

    def test_entry_latency_exceeds_max_quote_gap(self):
        r = self.replay([self.q(100, "1.18", "1.20")])
        self.assertEqual(r.outcome, ReplayOutcome.NO_ENTRY_EVIDENCE)

    def test_partial_bid_depth_fails_closed(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.60", "1.65"),
                         self.q(3, "1.65", "1.69", bid_size=0)])
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)

    def test_stop_loss_signal_later_fill_worse(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "0.65", "0.70"),
                         self.q(3, "0.30", "0.40")])
        self.assertEqual(r.outcome, ReplayOutcome.STOP_EXIT)
        self.assertEqual(r.hypothetical_net_pnl, D("-94.58"))

    def test_indicative_data_never_upgrades_to_valid_pnl(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.60", "1.64", feed="INDICATIVE"),
                         self.q(3, "1.30", "1.35")])
        self.assertEqual(r.outcome, ReplayOutcome.UNQUALIFIED_DATA)
        self.assertIsNone(r.hypothetical_net_pnl)

    def test_hypothetical_optin_absent_abstains(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.60", "1.65"),
                         self.q(3, "1.30", "1.35")],
                        allow_hypothetical=False)
        self.assertEqual(r.outcome, ReplayOutcome.UNQUALIFIED_DATA)
        self.assertIsNone(r.hypothetical_net_pnl)

    def test_invalid_zero_and_negative_latencies(self):
        for delay in (timedelta(0), timedelta(seconds=-1),
                      timedelta(minutes=6)):
            with self.subTest(delay=delay):
                with self.assertRaisesRegex(ValueError, "entry_latency"):
                    self.replay([self.q(1, "1.18", "1.20")],
                                entry_latency=delay)

    def test_same_quote_cannot_fill_even_microsecond_latency(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.60", "1.65")],
                        exit_latency=timedelta(microseconds=1))
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)

    def test_no_fills_after_cutoff(self):
        rules = replace(self.rules, last_trading_at=self.at + timedelta(seconds=3))
        r = delayed_long_option(
            [self.q(1, "1.18", "1.20"), self.q(2, "1.60", "1.65"),
             self.q(3, "2.00", "2.05")],
            rules, entry_decision_at=self.at,
            entry_latency=timedelta(seconds=1),
            exit_latency=timedelta(seconds=1), allow_hypothetical=True,
        )
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)

    def test_out_of_order_rejected(self):
        with self.assertRaisesRegex(ValueError, "increase"):
            self.replay([self.q(2, "1.18", "1.20"),
                         self.q(1, "1.60", "1.65")])

    def test_no_time_exit_fabricated_on_flat_session(self):
        r = self.replay([self.q(1, "1.18", "1.20"),
                         self.q(2, "1.19", "1.24"),
                         self.q(3, "1.20", "1.25")])
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)
        self.assertIsNone(r.hypothetical_net_pnl)


if __name__ == "__main__":
    unittest.main()
