"""Checks that no trading rule uses prices from the future.

Run from the repo folder with:  python -m pytest tests
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import strategy  # noqa: E402


def test_no_lookahead():
    prices = strategy.load_prices()
    tbill = strategy.load_tbill(prices.index)
    checked, mismatches = strategy.lookahead_test(prices, tbill, n_dates=50)
    assert checked == 50
    assert mismatches == [], f"rules used future data on {mismatches}"


def test_weights_stay_in_bounds():
    prices = strategy.load_prices()
    tbill = strategy.load_tbill(prices.index)
    weights = strategy.target_weights(prices, tbill)
    assert (weights >= 0).all().all(), "a negative weight would be a short"
    assert (weights.sum(axis=1) <= 1.0 + 1e-9).all(), "weights above 100% would mean borrowing"
