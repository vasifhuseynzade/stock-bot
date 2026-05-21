# V2.4 Monster Lab Backtest Package

This package is for research only. Do not deploy this directly to the live Telegram bot.

## What changed

The engine is copied from `backtest_engine_v2_3_fixed_walkforward.py` and only the variant basket was replaced.

The new basket keeps the old benchmark and tests stronger v2.4 candidates:

1. `benchmark_v2_1b_breakout_only`
   - Old breakout-only benchmark to beat.

2. `control_v2_3_vcp_breakout`
   - Prior VCP survivor for comparison.

3. `v2_4_primary_vcp_rs_no_weak`
   - Primary clean candidate.
   - Strict VCP + relative strength.
   - No WEAK names.
   - Stronger market regime filter.

4. `v2_4_rs_breakout_quality_no_weak`
   - Cleaner breakout strategy.
   - No WEAK names.
   - Stronger volume, close-location, RS and extension filters.

5. `v2_4_leader_rider_55d_breakout`
   - Attempts bigger winner capture.
   - Later partial, looser trailing stop.

6. `v2_4_cost_survivor_top1_liquid`
   - Built to survive 25-50 bps slippage.
   - Very liquid names only.
   - Top 1 signal per scan.

7. `v2_4_aggressive_high_beta_extreme_quality`
   - Controlled aggressive test.
   - Allows high beta / WEAK only at extreme quality and strong market regime.

8. `v2_4_bluechip_reclaim_experiment`
   - One controlled pullback/reclaim test.
   - Large liquid leaders only.
   - If this fails, reject pullbacks for now.

## First commands to run

Open Command Prompt in the folder with the file and run:

```bat
set FMP_API_KEY=YOUR_KEY_HERE
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50
```

Then run walk-forward:

```bat
python backtest_engine_v2_4_monster_lab.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10 --train-months 12 --test-months 3
python backtest_engine_v2_4_monster_lab.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25 --train-months 12 --test-months 3
python backtest_engine_v2_4_monster_lab.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50 --train-months 12 --test-months 3
```

## Bear-market torture test

```bat
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 25
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 50
```

## How to judge the winner

Do not pick the strategy with only the highest full-period return.

A candidate is interesting only if it has:

- positive full-period result at 25 bps,
- preferably survives or loses only mildly at 50 bps,
- positive walk-forward compounded return at 25 bps,
- at least 45-55% positive walk-forward windows,
- max drawdown materially better than the benchmark,
- enough trades to avoid a meaningless sample,
- no single ticker dominating the whole result.

## Next step after running

Send the generated zip files from `data/backtests/`:

- all three `variant_compare_*.zip` files,
- all three `walkforward_variants_*.zip` files,
- the 2022 torture-test zips.

Then compare:

1. full-period performance,
2. walk-forward performance,
3. cost sensitivity,
4. 2022 survival,
5. by-setup and by-ticker concentration.
