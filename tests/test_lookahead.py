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


def test_no_lookahead_v2():
    prices = strategy.load_prices()
    tbill = strategy.load_tbill(prices.index)
    checked, mismatches = strategy.lookahead_test(prices, tbill, n_dates=50,
                                                  weights_fn=strategy.target_weights_v2)
    assert mismatches == [], f"v2 rules used future data on {mismatches}"


def test_weights_stay_in_bounds():
    prices = strategy.load_prices()
    tbill = strategy.load_tbill(prices.index)
    for fn in (strategy.target_weights, strategy.target_weights_v2, strategy.target_weights_v5,
               strategy.target_weights_v6):
        weights = fn(prices, tbill)
        assert (weights >= 0).all().all(), "a negative weight would be a short"
        assert (weights.sum(axis=1) <= 1.0 + 1e-9).all(), "weights above 100% would mean borrowing"
        assert (weights <= strategy.MAX_WEIGHT + 1e-12).all().all(), "an ETF above 25%"


def test_no_lookahead_v5():
    prices = strategy.load_prices()
    tbill = strategy.load_tbill(prices.index)
    checked, mismatches = strategy.lookahead_test(prices, tbill, n_dates=50,
                                                  weights_fn=strategy.target_weights_v5)
    assert mismatches == [], f"v5 rules used future data on {mismatches}"
