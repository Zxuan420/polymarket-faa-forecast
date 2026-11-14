# Polymarket FAA Outage Attribution Forecaster

Forecasts Polymarket Yes prices minute by minute, 4 hours ahead (240 minutes), for the FAA outage attribution event markets.

## Method
Winsorized per-market volatility (mean absolute 1-minute change, each change capped at 0.01, blended 0.7 recent-24h / 0.3 full-history), floor 0.0025, cap 0.02, Gaussian outer quantiles +-1.644854, persistence median, and the cross-market sum constraint (medians scaled to sum to at most 1.0).

## Local Backtest & Results
Mean pinball loss 0.352576 on the 6 practice cases vs house baseline 0.371754 on the same answers (house's stated practice loss 0.363589). Note that the practice is a different event from the final so the number is a signal, not a guarantee.

## Running Locally
```bash
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
python3 backtest.py
```
