@echo off
REM V2.5 Monster Synthesis full test batch.
REM Optional: uncomment and set your key here.
REM set FMP_API_KEY=YOUR_KEY_HERE

python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50

python backtest_engine_v2_5_monster_synthesis.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10 --train-months 12 --test-months 3
python backtest_engine_v2_5_monster_synthesis.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25 --train-months 12 --test-months 3
python backtest_engine_v2_5_monster_synthesis.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50 --train-months 12 --test-months 3

python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 25
python backtest_engine_v2_5_monster_synthesis.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 50

pause
