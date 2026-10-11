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

    def test_direct_window_must_not_forecast_historical_bars(self):
        valid = self.window(horizon=2)
        forged = replace(
            valid,
            prediction_times=(
                valid.bars[-2].observed_at,
                valid.bars[-1].observed_at,
            ),
        )
        model = StubPredictor()
        with self.assertRaisesRegex(ValueError, "strictly future"):
            forecast_underlying(model, forged)
        self.assertIsNone(model.call, "model must not see leaked future context")

    def test_direct_window_must_not_cross_cutoff(self):
        valid = self.window(horizon=2)
        forged = replace(
            valid, last_trading_at=valid.bars[-1].observed_at + self.interval
        )
        model = StubPredictor()
        with self.assertRaisesRegex(ValueError, "forecast horizon"):
            forecast_underlying(model, forged)
        self.assertIsNone(model.call)

    def test_direct_window_gap_is_rejected_before_predictor(self):
        valid = self.window(horizon=2)
        forged = replace(
            valid,
            bars=valid.bars[:20] + valid.bars[21:],
        )
        model = StubPredictor()
        with self.assertRaisesRegex(ValueError, "gap/duplicate"):
            forecast_underlying(model, forged)
        self.assertIsNone(model.call)

    def test_direct_window_forecast_time_shape_is_required(self):
        valid = self.window(horizon=2)
        for bad in (
            replace(valid, prediction_times=[]),
            replace(valid, prediction_times=()),
            replace(valid, prediction_times=valid.prediction_times[::-1]),
        ):
            with self.subTest(forecast_times=bad.prediction_times):
                model = StubPredictor()
                with self.assertRaises(ValueError):
                    forecast_underlying(model, bad)
                self.assertIsNone(model.call)

    def test_direct_window_cannot_bypass_ny_date_rule(self):
        valid = self.window(horizon=2)
        forged = replace(
            valid,
            bars=(replace(valid.bars[0],
                          observed_at=valid.bars[0].observed_at - timedelta(days=1)),)
                 + valid.bars[1:],
        )
        model = StubPredictor()
        with self.assertRaisesRegex(ValueError, "NY trading date"):
            forecast_underlying(model, forged)
        self.assertIsNone(model.call)

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

    def test_duplicate_forecast_times_cannot_inflate_sample_count(self):
        f = forecast_underlying(StubPredictor(), self.window(horizon=2))
        duplicate = replace(
            f, prediction_times=(f.prediction_times[0],) * 2
        )
        # A dict has one actual observation despite the two forecast rows.
        actual = {f.prediction_times[0]: D("100.4")}
        with self.assertRaisesRegex(ValueError, "strictly increase"):
            score_forecast(duplicate, actual)

    def test_forecast_array_length_mismatch_is_not_silently_truncated(self):
        f = forecast_underlying(StubPredictor(), self.window(horizon=2))
        actual = {t: D("100.4") for t in f.prediction_times}
        for field in ("predicted_closes", "baseline_closes"):
            with self.subTest(field=field):
                invalid = replace(f, **{field: (D("100.5"),)})
                with self.assertRaisesRegex(ValueError, "arrays must match"):
                    score_forecast(invalid, actual)

    def test_unsorted_or_naive_forecast_horizon_is_rejected(self):
        f = forecast_underlying(StubPredictor(), self.window(horizon=2))
        actual = {t: D("100.4") for t in f.prediction_times}
        bad_order = replace(f, prediction_times=tuple(reversed(f.prediction_times)))
        with self.assertRaisesRegex(ValueError, "strictly increase"):
            score_forecast(bad_order, actual)
        naive = replace(
            f, prediction_times=(
                f.prediction_times[0].replace(tzinfo=None),
                f.prediction_times[1],
            ),
        )
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            score_forecast(naive, actual)

    def test_nonfinite_or_negative_forecast_inputs_never_score(self):
        f = forecast_underlying(StubPredictor(), self.window(horizon=2))
        actual = {t: D("100.4") for t in f.prediction_times}
        for field in ("predicted_closes", "baseline_closes"):
            for bad in (D("NaN"), D("Infinity"), D("-1"), D("0")):
                with self.subTest(field=field, bad=str(bad)):
                    broken = replace(f, **{field: (bad, D("100.5"))})
                    with self.assertRaisesRegex(ValueError, "close"):
                        score_forecast(broken, actual)
        with self.assertRaisesRegex(ValueError, "reference close"):
            score_forecast(replace(f, reference_close=D("NaN")), actual)

    def test_valid_scoring_still_counts_every_unique_future_bar(self):
        f = forecast_underlying(StubPredictor(), self.window(horizon=2))
        actual = {t: D("100.4") for t in f.prediction_times}
        score = score_forecast(f, actual)
        self.assertEqual(score.sample_size, 2)
        self.assertEqual(score.model_mae, D("0.1"))
        self.assertEqual(score.persistence_mae, D("0.3"))

    def test_no_prediction_of_options_profit_from_underlying(self):
        result = forecast_underlying(StubPredictor(), self.window())
        self.assertFalse(result.options_pnl_validated)
        self.assertFalse(result.trade_order_created)


if __name__ == "__main__":
    unittest.main()
