# V2.3 Backtest Research Package

This package is for research/backtesting only. Do not replace the live trading bot with a v2.3 strategy until the tests prove it.

## What V2.3 tests

V2.3 is not one strategy. It is a strategy lab that compares several different hypotheses against the old baseline:

1. `v2_1b_breakout_only_baseline` - old benchmark control.
2. `v2_3_rs_breakout_ranked` - breakout strategy with relative-strength ranking and baseline exits.
3. `v2_3_vcp_breakout` - volatility contraction / VCP-style breakout.
4. `v2_3_pullback_reclaim` - MA20 reclaim / leader pullback continuation.
5. `v2_3_hybrid_ranked` - lets breakout, VCP, and reclaim compete by rank score.
6. `v2_3_cost_robust_top2_no_weak` - selective, liquid, no-weak candidate for 50 bps stress testing.
7. `v2_3_vcp_macd_confirmed` - VCP breakout with MACD histogram confirmation.

## New indicators added

- MA150 / MA200 trend template context
- MACD histogram
- ATR14 / ATR50 compression ratio
- Bollinger Band width rank
- 10/20/63-day momentum / ROC
- Relative strength versus SPY, QQQ, and SMH
- Close-location score
- Base compression / VCP filters

## Commands

Run normal variant comparison:

```bash
python backtest_engine_v2_3.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10
```

Run stress-test variant comparison:

```bash
python backtest_engine_v2_3.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50
```

Run walk-forward for the default hybrid config:

```bash
python backtest_engine_v2_3.py --mode walkforward --start 2022-01-01 --end 2026-05-20 --slippage-bps 10 --train-months 12 --test-months 3
```

Run walk-forward for every variant:

```bash
python backtest_engine_v2_3.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10 --train-months 12 --test-months 3
```

Run 50 bps walk-forward for every variant:

```bash
python backtest_engine_v2_3.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50 --train-months 12 --test-months 3
```

## Decision rule

Do not select the highest-return strategy blindly. A candidate must:

- Beat or closely match v2.1b at 10 bps.
- Survive 50 bps better than v2.1b.
- Have positive median walk-forward window return.
- Have position/trade count large enough to matter.
- Avoid drawdown that is emotionally or practically unacceptable.

## What to send back for analysis

After each run, download and send the generated zip from the printed `Saved:` path.

The most important files inside are:

- `variant_compare.csv`
- `walkforward_variant_compare.csv`
- each variant's `summary.json`
- each variant's `trades.csv`
- each variant's `equity_curve.csv`
