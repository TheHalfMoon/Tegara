"""Synthetic-fixture tests for offline options quote replay. Not market evidence."""
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from tegara import OptionContract, OptionQuote
from tegara.quote_replay import (
    ReplayOutcome, ReplayRules, ReplayTick, replay_long_option
)


class QuoteReplayTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
        self.c = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.rules = ReplayRules(
            contract=self.c, quantity=1, entry_fee_per_contract=D("2.29"),
            exit_fee_per_contract=D("2.29"),
            entry_slippage_per_share=D("0"), exit_slippage_per_share=D("0"),
            target_net_profit=D("35"), maximum_net_loss=D("50"),
            deadline=self.at + timedelta(minutes=10),
            max_quote_gap=timedelta(minutes=5)
        )

    def tick(self, minutes, bid, ask=None, bid_size=5, ask_size=5, feed="EXACT_NBBO"):
        b = D(bid)
        a = D(ask) if ask is not None else b + D("0.03")
        return ReplayTick(OptionQuote(
            self.c, b, a, bid_size,
            self.at + timedelta(minutes=minutes),
            "synthetic_fixture_not_real_market", feed), ask_size)

    def test_requires_explicit_provenance_assertion(self):
        r = replay_long_option([self.tick(0, "1.18", "1.20"),
                                 self.tick(3, "1.62")], self.rules)
        self.assertEqual(r.outcome, ReplayOutcome.UNQUALIFIED_DATA)
        self.assertIsNone(r.simulated_net_pnl)

    def test_truthy_nonboolean_provenance_does_not_unlock_pnl(self):
        r = replay_long_option([self.tick(0, "1.18", "1.20"),
                                 self.tick(3, "1.62")], self.rules,
                               provenance_approved="yes")
        self.assertEqual(r.outcome, ReplayOutcome.UNQUALIFIED_DATA)
        self.assertIsNone(r.simulated_net_pnl)

    def test_target_exit_uses_ask_for_buy_bid_for_sell_and_fees(self):
        r = replay_long_option([self.tick(0, "1.18", "1.20"),
                                 self.tick(3, "1.62")], self.rules,
                               provenance_approved=True)
        self.assertEqual(r.outcome, ReplayOutcome.TARGET_EXIT)
        self.assertEqual(r.simulated_net_pnl, D("37.42"))
        self.assertEqual(r.entry_premium, D("1.20"))
        self.assertEqual(r.exit_premium, D("1.62"))

    def test_stop_loss_exit(self):
        r = replay_long_option(
            [self.tick(0, "1.18", "1.20"), self.tick(3, "0.60", "0.66")],
            self.rules, provenance_approved=True
        )
        self.assertEqual(r.outcome, ReplayOutcome.STOP_EXIT)
        self.assertEqual(r.simulated_net_pnl, D("-64.58"))

    def test_slippage_reduces_pnl(self):
        rules = replace(self.rules, entry_slippage_per_share=D("0.01"),
                        exit_slippage_per_share=D("0.02"))
        r = replay_long_option(
            [self.tick(0, "1.18", "1.20"), self.tick(3, "1.62"),
             self.tick(7, "1.62"), self.tick(10, "1.62")],
            rules, provenance_approved=True
        )
        # With target $35, the slipped quote does NOT trigger. The final
        # deadline quote yields a hypothetical time exit with lower net P&L.
        self.assertEqual(r.outcome, ReplayOutcome.TIME_EXIT)
        self.assertEqual(r.simulated_net_pnl, D("34.42"))

    def test_time_exit_at_final_eligible_bid(self):
        ticks = [self.tick(0, "1.18", "1.20"),
                 self.tick(4, "1.28"),
                 self.tick(9, "1.33")]
        r = replay_long_option(ticks, self.rules, provenance_approved=True)
        self.assertEqual(r.outcome, ReplayOutcome.TIME_EXIT)
        self.assertEqual(r.simulated_net_pnl, D("8.42"))

    def test_missing_exit_quotes_give_no_claimed_pnl(self):
        r = replay_long_option([self.tick(0, "1.18", "1.20")],
                               self.rules, provenance_approved=True)
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)
        self.assertIsNone(r.simulated_net_pnl)

    def test_quote_gap_gives_no_claimed_pnl(self):
        r = replay_long_option(
            [self.tick(0, "1.18", "1.20"), self.tick(9, "1.60")],
            self.rules, provenance_approved=True
        )
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)
        self.assertIsNone(r.simulated_net_pnl)

    def test_ask_liquidity_required_for_entry(self):
        r = replay_long_option(
            [self.tick(0, "1.18", "1.20", ask_size=0),
             self.tick(3, "1.62")],
            self.rules, provenance_approved=True
        )
        self.assertEqual(r.outcome, ReplayOutcome.NO_ENTRY_LIQUIDITY)
        self.assertIsNone(r.simulated_net_pnl)

    def test_non_nbbo_not_treated_as_qualified(self):
        r = replay_long_option([self.tick(0, "1.18", "1.20", feed="INDICATIVE")],
                               self.rules, provenance_approved=True)
        self.assertEqual(r.outcome, ReplayOutcome.UNQUALIFIED_DATA)

    def test_out_of_order_data_rejected(self):
        with self.assertRaises(ValueError):
            replay_long_option(
                [self.tick(3, "1.18", "1.20"), self.tick(1, "1.62")],
                self.rules, provenance_approved=True
            )

    def test_cross_contract_data_rejected(self):
        wrong = replace(self.tick(1, "1.62"),
                        quote=replace(self.tick(1, "1.62").quote,
                                      contract=replace(self.c, right="P")))
        with self.assertRaises(ValueError):
            replay_long_option([self.tick(0, "1.18", "1.20"), wrong],
                               self.rules, provenance_approved=True)

    def test_unrealistically_low_target_does_not_matter_without_quotes(self):
        rules = replace(self.rules, target_net_profit=D("0"))
        r = replay_long_option([self.tick(0, "1.18", "1.20")],
                               rules, provenance_approved=True)
        self.assertEqual(r.outcome, ReplayOutcome.NO_EXIT_EVIDENCE)

    def test_bad_rules(self):
        with self.assertRaises(ValueError):
            replace(self.rules, quantity=0)
        with self.assertRaises(ValueError):
            replace(self.rules, entry_slippage_per_share=D("-0.01"))


if __name__ == "__main__":
    unittest.main()
