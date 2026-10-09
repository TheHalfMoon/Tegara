import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from tegara import (
    AlertStatus, LongOptionPosition, OptionContract, OptionQuote,
    assess_profit_target, calculate_net_exit_pnl,
    minimum_bid_for_net_profit, minimum_bid_for_net_return_percent,
)


class TargetCoreTests(unittest.TestCase):
    def setUp(self):
        self.contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.position = LongOptionPosition(
            self.contract, D("1.20"), 1, D("2.29"), D("2.29")
        )
        self.now = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
        self.quote = OptionQuote(self.contract, D("1.62"), D("1.65"), 5, self.now, "fixture", "EXACT_NBBO")

    def assess(self, quote=None):
        return assess_profit_target(
            self.position, quote or self.quote, self.now, timedelta(seconds=3), D("35")
        )

    def test_net_pnl_and_explicit_fees(self):
        self.assertEqual(calculate_net_exit_pnl(self.position, D("1.62")), D("37.42"))

    def test_exact_profit_threshold(self):
        threshold = minimum_bid_for_net_profit(self.position, D("35"))
        self.assertEqual(threshold, D("1.5958"))
        self.assertEqual(calculate_net_exit_pnl(self.position, threshold), D("35"))

    def test_return_target_uses_entry_debit(self):
        bid = minimum_bid_for_net_return_percent(self.position, D("30"))
        self.assertEqual(calculate_net_exit_pnl(self.position, bid), self.position.initial_debit * D("0.30"))

    def test_quantity_and_multiplier(self):
        contract = replace(self.contract, multiplier=10)
        position = LongOptionPosition(contract, D("2"), 3, D("1"), D("1"))
        self.assertEqual(calculate_net_exit_pnl(position, D("3")), D("24"))

    def test_target_reached(self):
        self.assertEqual(self.assess().status, AlertStatus.REVIEW_SELL_TO_CLOSE)

    def test_target_not_reached(self):
        self.assertEqual(self.assess(replace(self.quote, bid=D("1.30"))).status, AlertStatus.TARGET_NOT_REACHED)

    def test_wrong_contract_suppressed(self):
        wrong = replace(self.quote, contract=replace(self.contract, right="P"))
        self.assertEqual(self.assess(wrong).status, AlertStatus.SUPPRESSED_CONTRACT_MISMATCH)

    def test_unqualified_feed_suppressed(self):
        self.assertEqual(self.assess(replace(self.quote, feed_type="INDICATIVE")).status, AlertStatus.SUPPRESSED_UNQUALIFIED_FEED)

    def test_stale_quote_suppressed(self):
        q = replace(self.quote, observed_at=self.now - timedelta(seconds=4))
        self.assertEqual(self.assess(q).status, AlertStatus.SUPPRESSED_STALE_QUOTE)

    def test_future_quote_suppressed(self):
        q = replace(self.quote, observed_at=self.now + timedelta(seconds=1))
        self.assertEqual(self.assess(q).status, AlertStatus.SUPPRESSED_STALE_QUOTE)

    def test_size_suppressed(self):
        self.assertEqual(self.assess(replace(self.quote, bid_size=0)).status, AlertStatus.SUPPRESSED_INSUFFICIENT_SIZE)

    def test_crossed_market_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.quote, ask=D("1.60"))

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.quote, observed_at=datetime(2026, 10, 9, 15, 0))

    def test_bad_money_rejected(self):
        for bad in (D("NaN"), D("Infinity"), D("-1")):
            with self.assertRaises(ValueError):
                calculate_net_exit_pnl(self.position, bad)

    def test_zero_or_fractional_quantity_rejected(self):
        for quantity in (0, -1, 1.0, True):
            with self.assertRaises(ValueError):
                replace(self.position, quantity=quantity)

    def test_expired_contract_never_issues_exit_alert(self):
        after_expiry = self.now + timedelta(days=2)
        x = assess_profit_target(self.position, self.quote, after_expiry,
                                 timedelta(days=4), D("35"))
        self.assertEqual(x.status, AlertStatus.SUPPRESSED_EXPIRED)

    def test_zero_multiplier_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.contract, multiplier=0)

    def test_negative_target_rejected(self):
        with self.assertRaises(ValueError):
            minimum_bid_for_net_profit(self.position, D("-1"))


if __name__ == "__main__":
    unittest.main()
