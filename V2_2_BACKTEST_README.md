# V2.2 Backtest Package

This package creates a v2.2 research engine around stronger breakout quality and better winner capture.

## What changed from v2.1b

V2.1b baseline is still included in `--mode variants` as `v2_1b_breakout_only_baseline`.

V2.2 default logic changes:

1. Breakout-only by default. Pullbacks are disabled unless `--allow-pullbacks` is used.
2. Stronger breakout quality filters:
   - close near the high of the candle
   - positive relative strength vs SPY / QQQ
   - positive 20-day and 63-day stock momentum
   - price above 20/50/100-day trend structure
   - MA20 slope filter
   - volume confirmation
   - avoids overextended breakouts
   - prefers 55-day breakouts or very strong RS 20-day breakouts
3. Better winner capture:
   - partial is later: 1.8R or +12%
   - partial is smaller: ~33% instead of 50%
   - breakeven moves later
   - trailing stop is wider before the trend proves itself
   - time stop removes dead breakouts
   - failed-breakout exit removes breakouts that quickly lose the breakout level

## Baseline to beat

From your prior v2.1b breakout-only backtest:

- Return: +49.57%
- Profit factor: 1.357
- Max drawdown: -16.03%
- Positions: 391

From the 50 bps stress test:

- Return: -12.16%
- Profit factor: 0.903
- Max drawdown: -22.91%

V2.2 is only better if it improves robustness, not just headline return.

## Setup

Copy `backtest_engine_v2_2.py` to your machine or Railway environment where `FMP_API_KEY` exists.

Install requirements if needed:

```bash
pip install pandas requests pandas_market_calendars
```

Set your FMP key:

```bash
export FMP_API_KEY="YOUR_KEY_HERE"
```

On Windows PowerShell:

```powershell
$env:FMP_API_KEY="YOUR_KEY_HERE"
```

## Run comparison: v2.1b vs v2.2 variants

```bash
python backtest_engine_v2_2.py \
  --mode variants \
  --start 2022-01-01 \
  --end 2026-05-20 \
  --capital 4000 \
  --slippage-bps 10
```

This creates a zip under:

```text
./data/backtests/variant_compare_YYYYMMDD_HHMMSS.zip
```

Send me that zip.

## Run stress comparison with 50 bps slippage

```bash
python backtest_engine_v2_2.py \
  --mode variants \
  --start 2022-01-01 \
  --end 2026-05-20 \
  --capital 4000 \
  --slippage-bps 50
```

Send me that zip too.

## Run walk-forward test on v2.2 default

```bash
python backtest_engine_v2_2.py \
  --mode walkforward \
  --start 2022-01-01 \
  --end 2026-05-20 \
  --capital 4000 \
  --slippage-bps 10 \
  --train-months 12 \
  --test-months 3
```

Then run stress walk-forward:

```bash
python backtest_engine_v2_2.py \
  --mode walkforward \
  --start 2022-01-01 \
  --end 2026-05-20 \
  --capital 4000 \
  --slippage-bps 50 \
  --train-months 12 \
  --test-months 3
```

## Decision rule

Do not move v2.2 into the live Telegram bot unless it beats v2.1b on several dimensions:

- total return meaningfully higher than +49.57%, or similar return with much lower drawdown
- profit factor above 1.35, preferably above 1.50
- 50 bps stress test above breakeven, or at least much better than v2.1b's -12.16%
- profitable walk-forward windows better than v2.1b
- no single ticker or one lucky period explains the whole edge

## Important

This is a research/backtest engine first. Do not replace the live bot with v2.2 until the comparison zip proves it.
