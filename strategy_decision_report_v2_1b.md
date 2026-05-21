# Strategy Decision Report — v2.1b Breakout-Only

## Key backtest metrics

| Test                         |   Return % |   Final equity |   Profit factor |   Max DD % |   Positions |   Avg R record |   Median R record |
|:-----------------------------|-----------:|---------------:|----------------:|-----------:|------------:|---------------:|------------------:|
| v2.1b breakout-only / 10 bps |      49.57 |        5982.95 |           1.357 |     -16.03 |         391 |         0.3828 |            0.5651 |
| v2.1b breakout-only / 50 bps |     -12.16 |        3513.49 |           0.903 |     -22.91 |         348 |         0.153  |            0.2389 |


## Annual returns — v2.1b breakout-only / 10 bps

|   year |   return_pct |   min_equity |   max_equity |
|-------:|-------------:|-------------:|-------------:|
|   2022 |    -12.998   |      3469.57 |      4055.29 |
|   2023 |     23.126   |      3405.41 |      4324.01 |
|   2024 |     25.6986  |      4159.63 |      5477.58 |
|   2025 |      1.15935 |      5019.99 |      5477.88 |
|   2026 |     12.1924  |      5306.59 |      5987.06 |


## Monthly stats

|   months |   profitable_months |   losing_months |   flat_months |   avg_month |   median_month |   best_month |   worst_month |
|---------:|--------------------:|----------------:|--------------:|------------:|---------------:|-------------:|--------------:|
|       53 |                  26 |              21 |             6 |    0.939245 |              0 |        12.08 |         -5.25 |


## Walk-forward windows

| config_name   | start      | end        |   total_return_pct |   positions |   profit_factor |   avg_r |   median_r |   max_drawdown_pct |
|:--------------|:-----------|:-----------|-------------------:|------------:|----------------:|--------:|-----------:|-------------------:|
| wf_01         | 2023-01-01 | 2023-03-31 |               2.29 |          28 |           1.234 |  0.3439 |     0.447  |              -5.94 |
| wf_02         | 2023-04-01 | 2023-06-30 |               1.52 |          31 |           1.189 |  0.3958 |     0.9417 |              -3.63 |
| wf_03         | 2023-07-01 | 2023-09-30 |              -1.81 |          26 |           0.83  |  0.2758 |     0.5262 |              -7.2  |
| wf_04         | 2023-10-01 | 2023-12-31 |              15.28 |          19 |           8.166 |  1.0862 |     0.9715 |              -2.39 |
| wf_05         | 2024-01-01 | 2024-03-31 |              12.95 |          34 |           3.813 |  0.802  |     0.9662 |              -2.63 |
| wf_06         | 2024-04-01 | 2024-06-30 |               7.32 |          21 |           3.118 |  0.5708 |     0.663  |              -2.51 |
| wf_07         | 2024-07-01 | 2024-09-30 |              -0.67 |          29 |           0.917 |  0.1523 |    -0.0242 |              -6.82 |
| wf_08         | 2024-10-01 | 2024-12-31 |               2.2  |          31 |           1.209 |  0.2037 |     0.7855 |              -6.26 |
| wf_09         | 2025-01-01 | 2025-03-31 |              -1.96 |          20 |           0.726 | -0.0023 |    -0.0258 |              -7.57 |
| wf_10         | 2025-04-01 | 2025-06-30 |               2.3  |          24 |           1.325 |  0.3537 |     0.3828 |              -4.05 |
| wf_11         | 2025-07-01 | 2025-09-30 |               4.34 |          43 |           1.438 |  0.3247 |     0.3125 |              -4.82 |
| wf_12         | 2025-10-01 | 2025-12-31 |              -4.8  |          32 |           0.624 |  0.0664 |    -0.023  |              -5.56 |
| wf_13         | 2026-01-01 | 2026-03-31 |              -1.71 |          27 |           0.872 |  0.3334 |     0.9458 |              -5.3  |


## Position-level stats from trade records

|   positions |   pos_win_rate |   avg_pos_r |   median_pos_r |   avg_win_pos_r |   avg_loss_pos_r |   profit_factor |   avg_holding |   median_holding |   partial_rate |   positions_with_mfe_gt2R |   positions_with_mfe_gt3R |   positions_with_mfe_gt5R |
|------------:|---------------:|------------:|---------------:|----------------:|-----------------:|----------------:|--------------:|-----------------:|---------------:|--------------------------:|--------------------------:|--------------------------:|
|         391 |        50.8951 |    0.086698 |        0.13377 |         1.08341 |        -0.946357 |         1.36197 |       15.8593 |               12 |        45.0128 |                    26.087 |                   14.0665 |                   5.11509 |


## Variant comparison

| config_name            |   total_return_pct |   profit_factor |   max_drawdown_pct |   positions |   avg_r |   median_r |
|:-----------------------|-------------------:|----------------:|-------------------:|------------:|--------:|-----------:|
| v2_1_breakout_only     |              49.57 |           1.357 |             -16.03 |         391 |  0.3828 |     0.5651 |
| v2_1_no_rs_hard_filter |              27.56 |           1.175 |             -20.73 |         544 |  0.3561 |     0.2067 |
| v2_1_no_weak           |              24.01 |           1.16  |             -20.79 |         510 |  0.3208 |     0.1168 |
| v2_1_tighter_stop      |              22.01 |           1.143 |             -21.01 |         542 |  0.3758 |     0.1548 |
| v2_1_candidate         |              18.11 |           1.129 |             -21.9  |         519 |  0.3292 |     0.1475 |
| v2_1_stricter          |              18.14 |           1.129 |             -21.9  |         520 |  0.3271 |     0.1438 |


## Decision


The best tested candidate is v2.1b breakout-only, but it is not yet strong enough to call a final live-money strategy. 
It is profitable under 10 bps slippage, but fails under 50 bps slippage, which means the edge is not thick enough yet.

Best next direction:
1. Keep v2.1b breakout-only as the baseline.
2. Do not switch to pure intraday.
3. Build/test v2.2 as a daily relative-strength breakout/trend-following model with better winner capture:
   - stricter RS/ranking,
   - fewer but higher-quality breakouts,
   - smaller/later partial,
   - wider trend-following trail on the remainder,
   - fail-fast/time-stop logic for false breakouts.
