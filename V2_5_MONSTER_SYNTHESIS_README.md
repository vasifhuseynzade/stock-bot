# V2.5 Monster Synthesis Backtest Lab

This is a research/backtest engine only. It does not replace the live Railway Telegram bot yet.

## Why v2.5 exists

The v2.4 tests showed:

- The old v2.1b breakout benchmark had the highest raw return at low slippage, but it broke badly under 50 bps and in 2022 bear-market testing.
- The v2.4 primary VCP no-weak strategy was the most cost-robust and low-drawdown candidate, but it was too quiet.
- Broad pullback/reclaim logic was too cost-sensitive and should not be used as the live core.
- MEDIUM and WEAK names created most of the drag at realistic/slippage-stress assumptions.
- STRONG names, high score, real volume expansion, and VCP/leader breakouts were the only areas with evidence of edge.

## What v2.5 adds

V2.5 adds new research controls:

- `allow_medium`
- `medium_min_score`
- `medium_min_rank_score`
- `medium_min_volume_ratio`
- `medium_min_market_score`
- `require_spy_above_ma100`
- `require_qqq_above_ma100`
- `require_spy_above_ma200`
- `require_qqq_above_ma200`

These are designed to avoid the two biggest problems discovered in v2.4:

1. too many low-quality MEDIUM/WEAK trades,
2. false breakout trades during bear-market recovery bounces.

## New v2.5 variants

### `v2_5_monster_synthesis_core`

Main candidate.

Hybrid of:

- strict VCP breakout,
- high-score/volume breakout,
- no WEAK names,
- MEDIUM names only if exceptional,
- major-index trend filter,
- leader-rider exits.

### `v2_5_monster_core_recycle_exits`

Same as core, but adds:

- failed-breakout exit,
- time-stop exit.

This tests whether recycling dead trades improves capital efficiency.

### `v2_5_strong_only_leader_breakout`

Tests the strongest trade-level lesson from v2.4:

- STRONG names only,
- high score,
- real volume expansion,
- leader-rider exits.

### `v2_5_cost_proof_top1`

Most conservative/cost-resistant candidate:

- top 1 signal only,
- SPY and QQQ above MA200,
- high liquidity,
- no WEAK names,
- MEDIUM names only if very exceptional.

This is the candidate that should survive 50 bps and 2022 best.

### `v2_5_aggressive_no_weak_top3`

Aggressive but still controlled:

- top 3 signals,
- no WEAK names,
- medium names allowed only with stronger score/rank/volume,
- slight risk boost only for elite setups.

## Commands

Set your FMP key first if needed:

```bat
set FMP_API_KEY=YOUR_KEY_HERE
```

Full-period tests:

```bat
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50
```

Walk-forward tests:

```bat
python backtest_engine_v2_5_monster_synthesis.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10 --train-months 12 --test-months 3
python backtest_engine_v2_5_monster_synthesis.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25 --train-months 12 --test-months 3
python backtest_engine_v2_5_monster_synthesis.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50 --train-months 12 --test-months 3
```

2022 bear-market torture tests:

```bat
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 25
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 50
```

## Decision rules

A v2.5 candidate deserves forward testing only if it passes most of this:

- positive at 25 bps full-period,
- not destroyed at 50 bps,
- positive compounded walk-forward at 25 bps,
- not worse than v2.4 primary VCP in drawdown,
- better opportunity than v2.4 primary VCP,
- 2022 test does not show unacceptable damage,
- no single ticker explains most profit.

If v2.5 fails, the next upgrade is not another indicator. The next upgrade is dynamic universe selection.
