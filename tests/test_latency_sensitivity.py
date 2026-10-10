"""Fixed latency stress tests with synthetic options quotes, no broker APIs."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from tegara.core import OptionContract, OptionQuote
from tegara.latency_sensitivity import latency_sensitivity, parse_latency_axis
from tegara.quote_replay import ReplayRules, ReplayTick


class LatencySensitivityTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
        self.contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.rules = ReplayRules(
            self.contract, 1, D("2.29"), D("2.29"), D("0"), D("0"),
            D("35"), D("50"), self.t0 + timedelta(seconds=60),
            timedelta(seconds=10), self.t0 + timedelta(seconds=100),
        )
        self.ticks = [
            self.tick(1, "1.18", "1.25"),
            self.tick(2, "1.65", "1.69"),
            self.tick(3, "0.80", "0.85"),
            self.tick(4, "1.55", "1.60"),
        ]

    def tick(self, sec, bid, ask):
        return ReplayTick(
            OptionQuote(self.contract, D(bid), D(ask), 2,
                        self.t0 + timedelta(seconds=sec),
                        "SYNTHETIC_ONLY", "EXACT_NBBO"), 2,
        )

    def report(self, entry=(1, 4), exit=(1, 2), allow=True):
        return latency_sensitivity(
            self.ticks, self.rules, entry_decision_at=self.t0,
            entry_delays=entry, exit_delays=exit,
            allow_hypothetical=allow,
        )

    def test_strict_parser_accepts_only_small_finite_whole_second_axes(self):
        self.assertEqual(parse_latency_axis("30,1,5", name="axis"), (1, 5, 30))
        for x in ("", "0", "-1", "1.0", "1e2", "1,1", "1,,2", "1, 2",
                  "1,2,", "301", "1,2,3,4,5", "1;2", "1" * 65, "01"):
            with self.subTest(x=x):
                with self.assertRaises(ValueError):
                    parse_latency_axis(x, name="axis")

    def test_four_scenarios_with_some_unpriced_never_report_summary_extrema(self):
        r = self.report()
        self.assertEqual(r["scenario_count"], 4)
        self.assertEqual(r["scenarios_with_priced_hypothetical_exit"], 2)
        self.assertEqual(r["scenarios_without_exit_pnl"], 2)
        self.assertFalse(r["all_scenarios_priced"])
        self.assertIsNone(r["min_hypothetical_pnl_only_if_complete_usd"])
        self.assertIsNone(r["max_hypothetical_pnl_only_if_complete_usd"])
        self.assertIsNone(r["selected_optimal_latency"])
        self.assertFalse(r["source_provenance_independently_verified"])
        self.assertFalse(r["actual_fill_reconciliation_verified"])
        cases = r["scenario_results"]
        self.assertEqual([(x["entry_delay_seconds"], x["exit_delay_seconds"])
                          for x in cases], [(1, 1), (1, 2), (4, 1), (4, 2)])
        self.assertEqual(cases[0]["hypothetical_net_pnl_usd"], "-49.58")
        self.assertEqual(cases[1]["hypothetical_net_pnl_usd"], "25.42")
        self.assertIsNone(cases[2]["hypothetical_net_pnl_usd"])

    def test_order_of_supplied_axes_does_not_affect_report(self):
        a = self.report(entry=(4, 1), exit=(2, 1))
        b = self.report(entry=(1, 4), exit=(1, 2))
        self.assertEqual(a, b)

    def test_without_explicit_hypothetical_consent_every_case_abstains(self):
        r = self.report(allow=False)
        self.assertEqual(r["scenarios_with_priced_hypothetical_exit"], 0)
        self.assertTrue(all(x["outcome"] == "UNQUALIFIED_DATA"
                            and x["hypothetical_net_pnl_usd"] is None
                            for x in r["scenario_results"]))
        self.assertIsNone(r["min_hypothetical_pnl_only_if_complete_usd"])

    def test_fully_priced_grid_exposes_bounded_hypothetical_extrema(self):
        r = self.report(entry=(1,), exit=(1, 2))
        self.assertTrue(r["all_scenarios_priced"])
        self.assertEqual(r["min_hypothetical_pnl_only_if_complete_usd"], "-49.58")
        self.assertEqual(r["max_hypothetical_pnl_only_if_complete_usd"], "25.42")
        self.assertIsNone(r["selected_optimal_latency"])

    def test_grid_refuses_invalid_axes_without_processing(self):
        for entry, exit in (
            ((), (1,)), ((1,), ()), ((1, 2, 3, 4, 5), (1,)),
            ((True,), (1,)), ((0,), (1,)), ((1, 1), (1,)),
            ((1,), (301,)),
        ):
            with self.subTest(entry=entry, exit=exit):
                with self.assertRaises(ValueError):
                    self.report(entry=entry, exit=exit)

    def test_indicative_row_cannot_generate_priced_scenarios(self):
        invalid = list(self.ticks)
        q = invalid[1].quote
        invalid[1] = ReplayTick(
            OptionQuote(q.contract, q.bid, q.ask, q.bid_size,
                        q.observed_at, "SYNTHETIC_ONLY", "INDICATIVE"), 2,
        )
        r = latency_sensitivity(
            invalid, self.rules, entry_decision_at=self.t0,
            entry_delays=(1,), exit_delays=(1,), allow_hypothetical=True,
        )
        self.assertEqual(r["scenario_results"][0]["outcome"], "UNQUALIFIED_DATA")
        self.assertIsNone(r["scenario_results"][0]["hypothetical_net_pnl_usd"])

    def test_no_actual_fill_extrapolated_from_missing_evidence(self):
        r = self.report(entry=(4,), exit=(1,))
        self.assertFalse(r["all_scenarios_priced"])
        self.assertEqual(r["scenarios_without_exit_pnl"], 1)
        self.assertNotIn("profitability_validated", r)


if __name__ == "__main__":
    unittest.main()
