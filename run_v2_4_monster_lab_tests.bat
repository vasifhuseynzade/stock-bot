@echo off
REM Set your key first if not already set in Windows environment:
REM set FMP_API_KEY=YOUR_KEY_HERE

python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50

python backtest_engine_v2_4_monster_lab.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 10 --train-months 12 --test-months 3
python backtest_engine_v2_4_monster_lab.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 25 --train-months 12 --test-months 3
python backtest_engine_v2_4_monster_lab.py --mode walkforward_variants --start 2022-01-01 --end 2026-05-20 --slippage-bps 50 --train-months 12 --test-months 3

python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 25
python backtest_engine_v2_4_monster_lab.py --mode variants --start 2022-01-01 --end 2022-12-31 --slippage-bps 50

pause
