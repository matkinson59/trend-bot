"""
two_x_costs.py: measures what each 2x fund in strategy.TWO_X costs per year
beyond "2 x its 1x ETF's daily return, minus T-bill interest on the borrowed
half". The result covers the fund's fee, its borrowing spread and any
tracking slippage. strategy.TWO_X_COST holds the numbers this printed on
Nov 23, 2026 (data to Nov 20, 2026).

Usage:  python tools/two_x_costs.py
"""
import os
import sys

import numpy as np
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy as s  # noqa: E402

tickers = list(s.TWO_X) + list(s.TWO_X.values()) + [s.TBILL_TICKER]
closes = yf.download(tickers, start="2006-01-01", end="2026-11-21",
                     auto_adjust=True, progress=False)["Close"]
tbill = (closes[s.TBILL_TICKER] / 100).ffill()
for one, two in s.TWO_X.items():
    pair = closes[[one, two]].dropna()
    r = pair.pct_change().dropna()
    y = tbill.reindex(r.index).shift(1).bfill()
    model = 2 * r[one] - y / s.TRADING_DAYS
    cost = (np.log1p(model) - np.log1p(r[two])).mean() * s.TRADING_DAYS
    print(f"{two} ({one} x2): from {pair.index[0].date()}, extra cost {cost:.2%} a year")
