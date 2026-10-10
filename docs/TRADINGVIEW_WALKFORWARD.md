# WF-P01 — TradingView OHLCV Walk-Forward Underlying Research

Status: LOCAL RESEARCH TOOL, not an option trading backtest or any proof of alpha.

## Purpose

Use historical underlying candlesticks exported by TradingView Premium (or
other legally retained market source) to compare a **pre-existing, frozen**
Kronos model against a trivial persistence baseline on reserved later dates.

No derivatives market Bid/Ask, IV, commissions, broker orders, fill queue,
assignment risk or live alerts are modeled. There are no credentials or APIs
in this workflow. Public pretrained Kronos itself may have previously trained
on some calendar intervals now labeled "holdout". Do not describe these
calendar holdouts as independent of pretraining unless verified.

## Source-time convention and lookahead

Financial chart CSVs commonly timestamp bars at their *open*. The OHLC and
volume of such a bar become known only after it closes. The Tegara converter
therefore REQUIRES the exact explicit flag:

    --bar-time-is open

This moves each source timestamp forward by one full 1m/5m interval, so the
normalized timestamp refers to *bar data availability*. Use
"--bar-time-is close" ONLY for sources known to report complete bar times.
Never use future bar OHLC at its opening timestamp.

The converter supports a strict TradingView-like subset of columns:

    time,open,high,low,close,Volume

The "time" field can contain a 10-digit Unix epoch second, a 13-digit epoch
millisecond, or timezone-aware ISO-8601 timestamp. An ISO time without timezone
needs a --source-timezone IANA string (e.g. America/New_York). Ambiguous
fall-back and nonexistent spring-forward local timestamps fail instead of
guessing. Extra indicator columns or a different source header require an
explicit mapping/cleaning step; no column silently maps itself.

Example (illustrative CSV with verified bar-open timestamp semantics):

    PYTHONPATH=src python3 -m tegara.tradingview_csv \
      --source /private/chart_export.csv \
      --output /private/chart_normalized.csv \
      --interval-minutes 1 --bar-time-is open

The destination must not already exist; it is published with private POSIX
file permissions. The converter first stages a complete, flushed CSV in the
same directory, then uses an atomic no-overwrite hard link to publish it.
Malformed rows, failed writes, or concurrent destination creation cannot leave
an incomplete CSV at the requested final path or overwrite another file.
No private data files belong in the Git repository.

## Normalized input format

    timestamp,open,high,low,close,volume

All timestamps are offset-aware and are the **complete bar availability**
times. Bars must be in chronological order with no duplicate timestamps.

A separate cutoff file is REQUIRED and must contain one row per NY trading
session in the dataset:

    session_date,last_trading_at
    2026-10-09,2026-10-09T20:00:00+00:00

**That value is an example, NOT a verified close for any underlying or option,
not a shortened trading-day rule, and not a valid 0DTE options last-trade rule.**
Use actual confirmed session times per symbol/date. A cutoff is user-supplied,
not validated against an exchange. The tool rejects rows at or after cutoff.

## First: audit and lock the input

    PYTHONPATH=src python3 scripts/walkforward_research.py \
      --csv /private/chart_normalized.csv \
      --cutoffs /private/cutoffs.csv \
      --interval-minutes 1 --lookback 40 --horizon 5 \
      --stride 10 --holdout-sessions 10 --max-windows 100 \
      --inspect-only

The audit returns content SHA-256s for data and cutoff file plus parameters
and an explicit status OFFLINE_UNDERLYING_AUDIT_ONLY. No model is loaded.

## Then: optional actual Kronos evaluation

Requires the separate official pinned upstream Kronos checkout and locally
installed compatible PyTorch and public Hugging Face model checkpoint.
The command may download weights; no paid API is required.

    PYTHONPATH=src .venv/bin/python scripts/walkforward_research.py \
      --csv /private/chart_normalized.csv \
      --cutoffs /private/cutoffs.csv \
      --interval-minutes 1 --lookback 40 --horizon 5 \
      --stride 10 --holdout-sessions 10 --max-windows 100 \
      --upstream-root .external/Kronos \
      --model-id NeoQuasar/Kronos-small --device cpu

The tool selects the last ten trading-session dates as a reserved evaluation
period, requires at least one earlier day, and distributes the window limit
across all reserved days. Within each selected window, it sends only 40
completed prior bars to Kronos; it scores only after the future 5 completed
bars are available. Reports include model close MAE, persistence close MAE,
simple terminal directional correctness and each forecast as-of timestamp.

This is *not* a training/fine-tuning procedure, so there is no hyperparameter
optimization, controlled market regime sampling or independent pretraining
contamination analysis. Overlapping windows can make naive uncertainty
intervals invalid. NEVER advertise an MAE improvement as net option returns.

## Executed synthetic integration evidence: 2026-10-10

An actual Kronos-small CPU inference run evaluated 2 windows from 3
artificial 50-bar sessions (last day reserved). Results:

- Full pipeline successful, status UNDERLYING_RESEARCH_ONLY.
- Window count: 2; heldout NY date: 2026-10-09.
- Model MAE: about 0.0000015258789 on constant synthetic closes;
  persistence MAE: 0.0 (better in this artificial test).
- Options P&L validated: FALSE; brokerage fills validated: FALSE.
- No real historical TradingView export, OPRA data or live fills were used.

Synthetic integration success does not establish trading utility.

## Acceptance beyond the prototype

1. Obtain a lawful, quality-checked historical underlying export and qualify
   option quote rights, contract identity, timestamps, and usable bid depth.
2. Verify market schedule/half-days/exchange last trading cutoffs from a
   trusted provider rather than example CSV metadata.
3. Lock an evaluation date protocol and explain how it avoids upstream
   pretrained-data overlap, strategy tuning and survivorship bias.
4. Compare Kronos and persistence/LightGBM/ORB under dates and execution costs
   that match actual traded derivatives. Then prospectively paper test.
5. Independently review source and tests with Alibaba OCR and CI.
6. Keep real Derayah Global execution manual until separately authorized.
