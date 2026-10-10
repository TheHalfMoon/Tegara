"""Synthetic no-lookahead Kronos adapter tests. Does NOT load pretrained AI."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from tegara.kronos_bridge import (
    MarketBar, create_window, forecast_underlying, score_forecast,
)


class StubPredictor:
    def __init__(self):
        self.call = None

    def predict(self, **kwargs):
        import pandas as pd
        self.call = kwargs
        expected = kwargs["pred_len"]
        return pd.DataFrame({"close": [100.5] * expected},
                            index=kwargs["y_timestamp"])


class BadPredictor:
    def predict(self, **kwargs):
        import pandas as pd
        return pd.DataFrame({"volume": [20]})


class KronosBridgeTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
        self.interval = timedelta(minutes=1)
        self.bars = [
            MarketBar(
                self.start + self.interval * i, D("100"), D("100.3"),
                D("99.8"), D("100.1"), D("1000") + i
            )
            for i in range(40)
        ]
        self.cutoff = self.start + timedelta(hours=6)

    def window(self, *, horizon=5, bars=None, cutoff=None):
        return create_window(
            self.bars if bars is None else bars,
            horizon_bars=horizon, interval=self.interval,
            last_trading_at=self.cutoff if cutoff is None else cutoff
        )

    def test_valid_pure_window_has_only_future_times(self):
        w = self.window()
        self.assertEqual(w.lookback, 40)
        self.assertEqual(w.prediction_times[0], self.bars[-1].observed_at + self.interval)
        self.assertEqual(w.prediction_times[-1], self.bars[-1].observed_at + timedelta(minutes=5))

    def test_model_predictor_receives_past_only_and_future_timestamps(self):
        predictor = StubPredictor()
        forecast = forecast_underlying(predictor, self.window(), model_label="stub-only")
        self.assertEqual(len(forecast.predicted_closes), 5)
        self.assertEqual(forecast.predicted_closes, (D("100.5"),) * 5)
        self.assertEqual(forecast.baseline_closes, (D("100.1"),) * 5)
        self.assertEqual(forecast.status, "UNDERLYING_RESEARCH_ONLY")
        self.assertFalse(forecast.options_pnl_validated)
        self.assertFalse(forecast.trade_order_created)
        self.assertEqual(predictor.call["pred_len"], 5)
        self.assertTrue(
            predictor.call["x_timestamp"].iloc[-1] <
            predictor.call["y_timestamp"].iloc[0]
        )
        self.assertEqual(len(predictor.call["df"]), 40)

    def test_no_forward_inference_beyond_user_cutoff(self):
        with self.assertRaisesRegex(ValueError, "forecast horizon"):
            self.window(cutoff=self.bars[-1].observed_at + timedelta(minutes=3))

    def test_no_gap_or_duplicate_in_training_window(self):
        broken = list(self.bars)
        broken[12] = replace(broken[12], observed_at=broken[11].observed_at)
        with self.assertRaisesRegex(ValueError, "gap/duplicate"):
            self.window(bars=broken)

    def test_wrong_day_context_rejected(self):
        broken = list(self.bars)
        broken[0] = replace(broken[0], observed_at=broken[0].observed_at - timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "NY trading date"):
            self.window(bars=broken)

    def test_short_context_rejected(self):
        with self.assertRaisesRegex(ValueError, "32..512"):
            self.window(bars=self.bars[:31])

    def test_wrong_interval_rejected(self):
        with self.assertRaisesRegex(ValueError, "1 or 5"):
            create_window(self.bars, horizon_bars=5, interval=timedelta(minutes=2),
                          last_trading_at=self.cutoff)

    def test_absent_cutoff_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.window(cutoff=datetime(2026, 10, 9, 18, 0))

    def test_invalid_ohlc_and_nan_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.bars[0], high=D("99"))
        with self.assertRaises(ValueError):
            replace(self.bars[0], close=D("NaN"))
        with self.assertRaises(ValueError):
            replace(self.bars[0], volume=D("-1"))

    def test_model_missing_close_rejected(self):
        with self.assertRaises(ValueError):
            forecast_underlying(BadPredictor(), self.window())

    def test_invalid_sample_count_rejected(self):
        for n in (0, -1, 17, True):
            with self.subTest(n=n):
                with self.assertRaises(ValueError):
                    forecast_underlying(StubPredictor(), self.window(), sample_count=n)

    def test_actual_holdout_scoring_vs_persistence_baseline(self):
        f = forecast_underlying(StubPredictor(), self.window())
        actual = {t: D("100.4") for t in f.prediction_times}
        score = score_forecast(f, actual)
        self.assertEqual(score.model_mae, D("0.1"))
        self.assertEqual(score.persistence_mae, D("0.3"))
        self.assertTrue(score.terminal_direction_correct)
        self.assertEqual(score.sample_size, 5)

    def test_partial_future_outcomes_rejected(self):
        f = forecast_underlying(StubPredictor(), self.window())
        with self.assertRaisesRegex(ValueError, "match forecast"):
            score_forecast(f, {f.prediction_times[0]: D("100.4")})

    def test_nonfinite_actual_outcome_rejected(self):
        f = forecast_underlying(StubPredictor(), self.window())
        with self.assertRaises(ValueError):
            score_forecast(f, {t: D("NaN") for t in f.prediction_times})

    def test_absurd_input_price_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            replace(self.bars[0], high=D("1e50"))

    def test_no_prediction_of_options_profit_from_underlying(self):
        result = forecast_underlying(StubPredictor(), self.window())
        self.assertFalse(result.options_pnl_validated)
        self.assertFalse(result.trade_order_created)


if __name__ == "__main__":
    unittest.main()
