"""Static safeguards for uncompiled TradingView Pine v6 prototypes.

This suite does NOT replace compilation inside the TradingView Pine Editor.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "tradingview"


class TradingViewContractTests(unittest.TestCase):
    def test_indicator_is_underlying_only_and_nonrepainting_on_bar_close(self):
        s = (ROOT / "tegara_underlying_indicator.pine").read_text()
        self.assertTrue(s.startswith("//@version=6"))
        self.assertIn('indicator(', s)
        self.assertIn("barstate.isconfirmed", s)
        self.assertIn("UNDERLYING_BULLISH_CANDIDATE", s)
        self.assertIn("UNDERLYING_BEARISH_CANDIDATE", s)
        self.assertIn("America/New_York", s)
        self.assertIn('0930-0945:23456', s)
        self.assertNotIn("strategy.entry(", s)
        self.assertNotIn("strategy.close(", s)

    def test_strategy_explicitly_tracks_underlying_not_options(self):
        s = (ROOT / "tegara_underlying_strategy.pine").read_text()
        self.assertTrue(s.startswith("//@version=6"))
        self.assertIn('strategy(', s)
        self.assertIn("NOT Options P&L", s)
        self.assertIn("process_orders_on_close = true", s)
        self.assertIn("barstate.isconfirmed", s)
        self.assertIn("strategy.close_all(", s)
        self.assertIn("1550-1600:23456", s)
        self.assertIn("America/New_York", s)

    def test_no_future_looking_or_external_data_access(self):
        for file in ROOT.glob("*.pine"):
            with self.subTest(file=file.name):
                s = file.read_text()
                self.assertNotIn("lookahead_on", s)
                self.assertNotIn("request.security", s)
                self.assertNotIn("api_key", s)
                self.assertNotIn("Derayah Global API", s)
                self.assertNotIn("options.buy(", s)


if __name__ == "__main__":
    unittest.main()
