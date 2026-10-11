# KR-P01 — Kronos Research Bridge (Underlying Forecasting Only)

Status: adapter and synthetic unit tests implemented. No claim of profitable
0DTE trading, model calibration, data-feed integration or live inference service.

## Why Kronos is optional

Tegara's deterministic option quote/fee/exit safeguards are independent of
Kronos, Jev and any other model. Forecasts may only annotate a separate
research hypothesis and MUST NOT become executable option Bid prices.

The official upstream is https://github.com/shiyu-coder/Kronos, MIT licensed.
As checked 2026-10-10, revision
67b630e67f6a18c9e9be918d9b4337c960db1e9a provides
KronosPredictor.predict(df, x_timestamp, y_timestamp, pred_len, ...).
The tokenizer and model referenced by its docs are
NeoQuasar/Kronos-Tokenizer-base and NeoQuasar/Kronos-small.

No upstream source code, third-party font, token or pretrained checkpoint is
vendored into Tegara. Pin and inspect a trusted upstream commit separately
before running inference. Respect upstream license and data rights.

## Design and inputs

- MarketBar: strictly finite, bounded OHLC/volume values and timezone-aware
  timestamp. Decimal exponent (-18..15) and significant representation length
  (28 digits) are bounded before conversion to float-backed pandas/model
  arrays; a finite positive price such as 1E-1000 must not silently become
  an impossible zero OHLC input. Directly supplied forecast/actual scoring
  values obey the same numeric bounds. Limits are research-data input guards,
  not venue tick-size certification.
- create_window: 32..512 contiguous past 1m or 5m bars from the SAME NY
  trading date, no duplicate/reordered bars or unknown session cutoff.
- Forecast horizon: max 60 bars and never crossing the explicit user-supplied
  end of tradable session. For abbreviated market days, supply actual cutoff.
- forecast_underlying: injected KronosPredictor-compatible model. Before
  importing pandas or invoking an external predictor, independently
  revalidates *even caller-constructed ForecastWindow objects* against
  create_window's complete 32..512 contiguous prior-bar, NY-session,
  cutoff and exact future-horizon rules. A fabricated window cannot send
  historical outcome bars or after-cutoff timestamps to a model merely by
  bypassing the normal factory. Converts only past bars to pandas input
  and requests strictly future timestamps. Rejects malformed output length,
  timestamp mismatch, nonfinite or invalid predicted closes.
- Produces a naive persistence baseline alongside model predictions. The
  simple baseline is essential: a complex model is not assumed superior.
- score_forecast: after-event underlying close MAE, persistence-baseline MAE,
  and terminal direction classification. A directly constructed public
  UnderlyingForecast must have one unique, strictly increasing, timezone-aware
  timestamp per predicted and persistence-baseline close; list lengths must
  agree and all prices must be finite and strictly positive. Duplicate future
  timestamps cannot inflate sample size or count the same actual bar twice;
  malformed arrays never get truncated silently by zip(). These are NOT
  probabilities of option profit or evidence of executable orders.

The user-supplied last-trading/session cutoff does NOT verify its source.
A live system still requires independently qualified exchange/broker metadata.

## Dependencies

Unit tests for the bridge run against a fully deterministic injected model
stub; no model download or paid cloud is necessary.

For pandas bridge support:

    python3 -m pip install -e '.[kronos-bridge]'

To run optional real pretrained model smoke (synthetic bars only), install
upstream's compatible PyTorch, einops, huggingface_hub, safetensors, numpy,
pandas, tqdm requirements in an isolated virtualenv, clone the official
Kronos repo into a local ignored directory, and invoke:

    PYTHONPATH=src .venv/bin/python scripts/kronos_smoke.py \
      --upstream-root .external/Kronos \
      --model-id NeoQuasar/Kronos-small \
      --device cpu

Downloading model checkpoints can take space and time on a small Mac.
NEVER store HF credentials or private Derayah trade exports in the repo.
Running synthetic inference confirms only a working dependency integration,
not price forecast accuracy or options-trading value.

## Release requirements

- Run exact-head pytest/unittest and real upstream smoke in supported local
  environment.
- Qualify historical underlying and licensed options data separately.
- Walk-forward at event-time availability, calibrate uncertainty if possible,
  and compare to persistence/LightGBM/VWAP baseline.
- Model pretraining data contamination and market regime changes may produce
  overoptimistic retrospective results; reserve locked holdout dates.
- No real-time Sell-to-Close notifications driven by Kronos without separate
  deterministic price/quote/risk gates and manual Derayah execution.
- Review changes with Jev bounded judgments, Graft, and independent OCR;
  TesterArmy browser e2e remains mandatory for user-facing UI behavior.

## Executed local upstream smoke evidence — 2026-10-10

- Device: Apple Silicon macbook, 8 GiB unified RAM.
- Python: isolated uv virtual environment, CPython 3.13.
- Source: upstream Kronos git SHA
  67b630e67f6a18c9e9be918d9b4337c960db1e9a (MIT).
- Model: NeoQuasar/Kronos-small, tokenizer:
  NeoQuasar/Kronos-Tokenizer-base (public Hugging Face weights).
- Device: CPU (no paid accelerator).
- Test input: 40 deliberately synthetic 1-minute OHLCV bars; prediction:
  3 future 1-minute underlying closing prices.
- Outcome: PASS, no exception. The output status was UNDERLYING_RESEARCH_ONLY,
  with options_pnl_validated=false and trade_order_created=false.
- Example synthetic-result closes (illustrative, stochastic model output):
  100.0999984741211, 100.10000610351562, 100.0999984741211.
- This proves only API/checkpoint/runtime integration. It does NOT qualify
  historical forecast accuracy, calibration, options trade execution,
  timing profitability or target-price recommendations.
- Dependencies and model weights reside in a gitignored local environment /
  external checkpoint cache. No credentials, downloaded weights or third-party
  code were committed to the Tegara repository.
