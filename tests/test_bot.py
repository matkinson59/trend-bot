"""Checks for the live bot, run against SimBroker with made-up prices.
Nothing here touches the network or Alpaca.

Run from the repo folder with:  python -m pytest tests
"""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402
import strategy as s  # noqa: E402
from broker import AlpacaBroker, SimBroker  # noqa: E402

NY = "America/New_York"
FUND_PRICES = {f: 100.0 for f in bot.MANAGED}


def make_data(end="2026-12-11", n=700, seed=1):
    days = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    drift = np.linspace(-0.0004, 0.0006, len(s.TICKERS))
    rets = rng.normal(drift, 0.01, size=(n, len(s.TICKERS)))
    closes = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=days, columns=s.TICKERS)
    tbill = pd.Series(0.04, index=days)
    return closes, tbill, "yahoo"


def trading_days(data, extra=()):
    return sorted({d.date() for d in data[0].index} | set(extra))


def at(day, hour=10, minute=17):
    return pd.Timestamp(f"{day} {hour}:{minute}", tz=NY)


def go(broker, tmp_path, day, data, enabled=True, dividend_fn=None):
    return bot.run(broker, enabled, log_dir=tmp_path, now=at(day), data=data,
                   dividend_fn=dividend_fn or (lambda sym: pd.Series(dtype=float)),
                   opens_fn=lambda syms: {}, timeout=0,
                   shadow_fn=lambda since, today, tbill: ({"v1": 1.0, "v2": 2.0, "v6": 6.0}, None))


def test_dry_run_sends_nothing(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]))
    row = go(broker, tmp_path, "2026-12-14", data, enabled=False)
    assert broker.sent == []
    assert row["status"].startswith("dry run")
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["evaluated_week"] == "2026W51" and state["last_scores"] == {}
    plan = pd.read_csv(tmp_path / "orders.csv")
    assert (plan["phase"] == "plan").all() and plan["dry_run"].all()


def test_first_live_run_buys_targets_and_never_borrows(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]))
    row = go(broker, tmp_path, "2026-12-14", data)
    assert row["status"].startswith("traded")
    assert broker.cash >= 0
    equity = broker.account()["equity"]
    assert broker.cash <= bot.CASH_BUFFER * equity + 1.0
    targets = s.target_weights_v2(data[0], data[1]).iloc[-1]
    for t in s.TICKERS:
        assert abs(row[f"actual_{t}"] - targets[t]) < 0.001
    assert abs(row["boxx_weight"] - max(0.0, 1 - targets.sum() - bot.CASH_BUFFER)) < 0.002
    sides = [o["side"] for o in broker.sent]
    assert "sell" not in sides                      # nothing to sell on day one


def test_only_one_trading_run_per_week(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14), date(2026, 12, 15)]))
    go(broker, tmp_path, "2026-12-14", data)
    n = len(broker.sent)
    data2 = (pd.concat([data[0], data[0].iloc[[-1]].set_axis([pd.Timestamp("2026-12-14")])]),
             pd.concat([data[1], pd.Series([0.04], index=[pd.Timestamp("2026-12-14")])]), "yahoo")
    row = go(broker, tmp_path, "2026-12-15", data2)
    assert len(broker.sent) == n and row["status"] == "no-trade day"


def test_sells_first_and_buys_scale_to_real_cash():
    broker = SimBroker(500, FUND_PRICES, [date(2026, 12, 14)], partial_fill={"VOO": 0.5})
    broker.qty = {"VOO": 300.0}                     # $30,000 of the SPY slot
    equity = broker.account()["equity"]
    positions = broker.positions()
    targets = pd.Series(0.0, index=s.TICKERS)
    targets["SPY"], targets["IEF"] = 0.10, 0.25
    dollars = {"SPY": -20_000.0, "IEF": 25_000.0}
    sent, notes = bot.execute(broker, dollars, targets, positions, equity, "2026W51", timeout=0)
    sides = [o["side"] for o in broker.sent]
    assert sides.index("sell") < sides.index("buy")
    assert broker.cash >= 0
    assert any("scaled" in n for n in notes) and any("not fully filled" in n for n in notes)
    assert broker.qty["IEF"] * 100 < 25_000         # only bought what the cash allowed


def test_stale_data_blocks_and_retries_next_day(tmp_path):
    data = make_data(end="2026-12-09")
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 10), date(2026, 12, 11),
                                                                 date(2026, 12, 14)]))
    row = go(broker, tmp_path, "2026-12-14", data)
    assert row["status"].startswith("blocked") and "price data ends" in row["status"]
    assert broker.sent == []
    assert json.loads((tmp_path / "state.json").read_text()).get("evaluated_week") is None


def test_holiday_skip(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data), is_open=False)
    row = go(broker, tmp_path, "2026-12-25", data)
    assert row["status"] == "holiday skip" and broker.sent == []


def test_orders_already_sent_this_week_block(tmp_path):
    data = make_data()
    earlier = {"id": "x", "client_order_id": "tb-2026W51-VOO-buy", "symbol": "VOO", "side": "buy",
               "status": "filled", "submitted_at": at("2026-12-14", 10, 20).isoformat()}
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]), past_orders=[earlier])
    row = go(broker, tmp_path, "2026-12-14", data)
    assert "already sent this week" in row["status"] and broker.sent == []


def test_catch_up_when_the_first_day_of_the_week_was_missed(tmp_path):
    data = make_data(end="2026-12-14")
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 15)]))
    row = go(broker, tmp_path, "2026-12-15", data)
    assert row["status"].startswith("traded") and "catch-up" in row["flags"]


def test_rerun_same_day_keeps_one_row_and_counts_income_once(tmp_path):
    data = make_data()
    days = trading_days(data, [date(2026, 12, 14), date(2026, 12, 15)])
    broker = SimBroker(100_000, FUND_PRICES, days)
    go(broker, tmp_path, "2026-12-14", data)
    data2 = (pd.concat([data[0], data[0].iloc[[-1]].set_axis([pd.Timestamp("2026-12-14")])]),
             pd.concat([data[1], pd.Series([0.04], index=[pd.Timestamp("2026-12-14")])]), "yahoo")
    div = lambda sym: pd.Series({date(2026, 12, 15): 0.30}) if sym == "IEF" else pd.Series(dtype=float)  # noqa: E731
    first = go(broker, tmp_path, "2026-12-15", data2, dividend_fn=div)
    second = go(broker, tmp_path, "2026-12-15", data2, dividend_fn=div)
    daily = pd.read_csv(tmp_path / "daily.csv")
    assert (daily["date"] == "2026-12-15").sum() == 1
    assert first["income_today"] > 0 and second["income_today"] == 0
    assert np.isclose(second["income_total"], first["income_total"])


def test_whole_shares_when_a_fund_is_not_fractionable(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]),
                       not_fractionable={"VOO", s.CASH_ETF})
    go(broker, tmp_path, "2026-12-14", data)
    for o in broker.sent:
        if o["symbol"] in {"VOO", s.CASH_ETF}:
            assert float(o["filled_qty"]).is_integer()
    assert broker.cash >= 0


def test_safety_checks_catch_bad_targets_and_big_orders():
    acct = {"cash": 1000.0, "status": "ACTIVE", "trading_blocked": False, "account_blocked": False}
    targets = pd.Series(0.1, index=s.TICKERS)
    targets["SPY"] = 0.30
    p = bot.safety_problems(targets, {"SPY": 35_000.0}, 100_000, acct, True, None, None)
    assert any("above 25%" in x for x in p) and any("above 30% of equity" in x for x in p)
    assert any("add up to" in x for x in p)


def test_live_broker_only_accepts_the_paper_address():
    with pytest.raises(ValueError):
        AlpacaBroker("key", "secret", base_url="https://api.alpaca.markets")
    with pytest.raises(ValueError):
        AlpacaBroker(None, None)
    assert AlpacaBroker("key", "secret").base == "https://paper-api.alpaca.markets"


def test_boxx_holds_whatever_the_targets_leave(tmp_path):
    data = make_data(seed=8)                        # targets add up to about 80%
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]))
    row = go(broker, tmp_path, "2026-12-14", data)
    targets = s.target_weights_v2(data[0], data[1]).iloc[-1]
    assert targets.sum() < 0.9
    assert abs(row["boxx_weight"] - (1 - targets.sum() - bot.CASH_BUFFER)) < 0.002
    assert abs(row["cash"] - bot.CASH_BUFFER * row["equity_alpaca"]) < 60


def test_next_week_rebalances_end_to_end(tmp_path):
    week1 = make_data(seed=8)
    week2 = make_data(end="2026-12-18", seed=5)     # different prices, so different scores
    days = trading_days(week1, [date(2026, 12, 14)]) + trading_days(week2, [date(2026, 12, 21)])
    broker = SimBroker(100_000, FUND_PRICES, sorted(set(days)))
    go(broker, tmp_path, "2026-12-14", week1)
    first = len(broker.sent)
    row = go(broker, tmp_path, "2026-12-21", week2)
    new = broker.sent[first:]
    assert row["status"].startswith("traded")
    sides = [o["side"] for o in new]
    assert "sell" in sides and sides.index("sell") < sides.index("buy")
    assert broker.cash >= 0
    targets = s.target_weights_v2(week2[0], week2[1]).iloc[-1]
    for t in s.TICKERS:
        assert abs(row[f"actual_{t}"] - targets[t]) < 0.006


def test_crash_is_written_to_the_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["bot.py"])
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    with pytest.raises(ValueError):
        bot.main()
    daily = pd.read_csv(tmp_path / "log" / "daily.csv")
    assert daily["status"].iloc[-1].startswith("error: ValueError: Alpaca keys are missing")
    assert (tmp_path / "log" / "errors.csv").exists()


def test_crash_keeps_a_row_already_written_today(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]))
    go(broker, tmp_path, "2026-12-14", data)
    bot.record_crash(RuntimeError("boom"), True, log_dir=tmp_path, now=at("2026-12-14"))
    daily = pd.read_csv(tmp_path / "daily.csv")
    assert len(daily) == 1 and daily["status"].iloc[0].startswith("traded")
    assert "boom" in pd.read_csv(tmp_path / "errors.csv")["error"].iloc[0]


def test_live_record_starts_at_the_first_live_run(tmp_path):
    week1 = make_data(end="2026-12-04")
    week2 = make_data(end="2026-12-11")
    days = sorted(set(trading_days(week1, [date(2026, 12, 7)]) + trading_days(week2, [date(2026, 12, 14)])))
    broker = SimBroker(100_000, FUND_PRICES, days)
    dry = go(broker, tmp_path, "2026-12-07", week1, enabled=False)
    assert dry["status"].startswith("dry run") and not broker.sent
    live = go(broker, tmp_path, "2026-12-14", week2, enabled=True)
    state = json.load(open(tmp_path / "state.json"))
    assert live["status"].startswith("traded")
    assert state["live_since"] == state["start_date"] == "2026-12-14"
    assert live["income_total"] == 0                # no interest counted for the dry-run week
    assert abs(live["return_since_start"] + live["cost_total"] / 100_000) < 1e-9



def test_shadows_follow_the_backtest_from_go_live():
    data = make_data(end="2026-12-18")
    closes, tbill = data[0], data[1]
    bars = lambda start, today: (closes * 0.999, closes)          # opens a touch below closes
    out, close_day = bot.shadow_equity("2026-12-14", date(2026, 12, 21), tbill, bars_fn=bars)
    assert close_day == date(2026, 12, 18) and set(out) == {"v1", "v2", "v6"}
    assert all(50_000 < v < 150_000 for v in out.values())
    from backtest import first_day_of_week, simulate
    weekly = first_day_of_week(closes.index)
    expect = simulate(s.target_weights_v2(closes, tbill), s.trend_scores(closes, tbill),
                      closes * 0.999, closes, tbill, weekly, pd.Timestamp("2026-12-14"))[0]["equity"].iloc[-1]
    assert abs(out["v2"] - expect) < 0.01
    before, none_yet = bot.shadow_equity("2026-12-21", date(2026, 12, 21), tbill, bars_fn=bars)
    assert none_yet is None and set(before.values()) == {100_000.0}


def test_shadows_are_logged_only_once_live(tmp_path):
    data = make_data()
    broker = SimBroker(100_000, FUND_PRICES, trading_days(data, [date(2026, 12, 14)]))
    dry = go(broker, tmp_path, "2026-12-14", data, enabled=False)
    assert "shadow_v2" not in dry
    live = go(broker, tmp_path, "2026-12-14", data, enabled=True)
    assert live["shadow_v1"] == 1.0 and live["shadow_v6"] == 6.0
