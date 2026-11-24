"""
bot.py: the live paper-trading bot. Runs spec version 2 (see SPEC.md,
"Locked: version 2").

    python bot.py              Alpaca paper account (GitHub Actions). Sends orders
                               only when the TRADING_ENABLED variable is "true".
    python bot.py --simulate   A simulated $100,000 account on this computer, at
                               the latest closing prices. Nothing is sent anywhere.

Each run: check the market is open, load prices, compute the version 2
targets from the last close, add up income that paper trading leaves out,
and on the first run of each week trade toward the targets (sells, then
buys with the cash actually in the account, then idle cash into BOXX).
Everything is written to log/ (or sim_log/ when simulating).
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import strategy as s
from broker import DONE, AlpacaBroker, SimBroker

NY = s.NEW_YORK
BUFFER = 0.02             # trade an ETF when its weight drifts more than this from target
CASH_BUFFER = 0.005       # share of equity kept as cash, outside BOXX
LIMIT_BAND = 0.01          # orders refuse prices more than 1% worse than the latest trade
MIN_ORDER = 1.00          # smallest order in dollars
MIN_BOXX_ORDER = 50.00    # smallest move into or out of BOXX
MAX_ORDER = 0.30          # largest single ETF order, as a share of equity (BOXX exempt: it is cash)
SHADOWS = {"v1": "target_weights", "v2": "target_weights_v2", "v6": "target_weights_v6"}
B7_LEVERAGE = 1.7         # bot 2 round 2's B7, at the leverage its cap rule chose (results/bot2_round2)
DRAWDOWN_FLAG = 0.10
COST_BPS = 5
PHASE_TIMEOUT = 180       # seconds to wait for each group of orders to fill
POLL = 3

FUND = {t: s.LIVE_FUNDS.get(t, t) for t in s.TICKERS}      # signal ETF -> fund held
SIGNAL = {f: t for t, f in FUND.items()}                  # fund held -> signal ETF
MANAGED = set(FUND.values()) | {s.CASH_ETF}


# ---------------------------------------------------------------- data

def load_signal_data(broker, today):
    """Adjusted closes for the nine signal ETFs and the T-bill rate, through
    the last finished trading day. Yahoo first; Alpaca bars plus FRED if
    Yahoo fails."""
    start = (pd.Timestamp(today) - pd.DateOffset(years=3)).date()
    try:
        closes = s.load_prices(start=str(start))
        tbill = s.load_tbill(closes.index, start=str(start))
        if closes.empty or tbill.isna().iloc[-1]:
            raise ValueError("empty or incomplete Yahoo data")
        return closes, tbill, "yahoo"
    except Exception as yahoo_error:  # noqa: BLE001
        closes = broker.daily_closes(s.TICKERS, start, end=today)
        closes = closes[closes.index.date < today].dropna()
        tbill = fred_tbill(closes.index, start)
        return closes, tbill, f"alpaca+fred (Yahoo failed: {str(yahoo_error)[:80]})"


def fred_tbill(index, start):
    """3-month T-bill rate (FRED series DTB3), as a decimal, on the given dates."""
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                     params={"id": "DTB3", "cosd": str(start)}, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text), na_values=".")
    series = pd.Series(df.iloc[:, 1].values / 100, index=pd.to_datetime(df.iloc[:, 0]))
    return series.reindex(index.union(series.index)).ffill().reindex(index)


def yahoo_dividends(symbol):
    import yfinance as yf
    d = yf.Ticker(symbol).dividends
    d.index = pd.to_datetime(d.index).tz_localize(None).date
    return d


def yahoo_opens_today(symbols, today):
    """Today's opening price for each symbol, for the order log. Best effort."""
    try:
        import yfinance as yf
        raw = yf.download(list(symbols), period="5d", interval="1d", auto_adjust=False, progress=False)["Open"]
        raw.index = pd.to_datetime(raw.index).date
        return raw.loc[today].to_dict() if today in raw.index else {}
    except Exception:  # noqa: BLE001
        return {}


def backtest_band():
    """5th and 95th percentile of version 2's 60-trading-day backtest returns."""
    path = Path(__file__).parent / "results" / "v2" / "equity.csv"
    eq = pd.read_csv(path, index_col="date", parse_dates=True)["trend_bot_v2"]
    r60 = eq.pct_change(60).dropna()
    return float(r60.quantile(0.05)), float(r60.quantile(0.95))


# ---------------------------------------------------------------- log

def yahoo_bars(start, today):
    return s.load_bars(start=str(start))


def yahoo_b7_bars(start, today):
    import bot2_backtest as b2
    return s.load_bars(start=str(start), tickers=list(dict.fromkeys(s.TICKERS + b2.SECTORS)),
                       require=s.TICKERS + b2.SECTORS_CORE)


def b7_equity(start, opens, closes, rates):
    """B7 (bot 2 round 2): B5's equal mix of version 2, trend-timed S&P 500 and
    sector momentum at 1.7x, with exposure cut in a straight line from a 15% to
    a 30% drawdown. $100,000 at `start`, through the last close in `closes`."""
    import bot2_backtest as b2
    from backtest import first_day_of_month, first_day_of_week, simulate
    from bot2_round2 import floor_half_cap
    weekly, monthly = first_day_of_week(closes.index), first_day_of_month(closes.index)
    used = b2.b5_used([(s.target_weights_v2(closes[s.TICKERS], rates), weekly),
                       (b2.b2_targets(closes)[0], weekly),
                       (b2.b3_targets(closes, rates)[0], monthly)], closes.index)
    cols = list(used.columns)
    daily, _, _ = simulate(b2.as_close_rows(used), None, opens[cols].bfill(), closes[cols].bfill(),
                           rates, weekly, start)
    rows = b2.base_rows(daily, rates, used)
    R, _ = b2.overlay([rows[c].values for c in ("excess", "invested", "rf", "spread")],
                      B7_LEVERAGE, floor=floor_half_cap)
    return round(b2.bt.START_VALUE * float(np.prod(1 + R)), 2)


def shadow_equity(since, today, tbill, bars_fn=yahoo_bars, b7_bars_fn=yahoo_b7_bars):
    """Simulated equity of each shadow version, $100,000 on the go-live date,
    run with the backtest's own simulator through the last finished close.
    Returns ({version: equity}, date of that close). B7 is None when its data
    or rules fail, so a problem there never blanks the other shadows."""
    from backtest import START_VALUE, first_day_of_week, simulate
    start = pd.Timestamp(since)
    opens, closes = bars_fn((start - pd.DateOffset(years=3)).date(), today)
    keep = closes.index.date < today
    opens, closes = opens[keep], closes[keep]
    if closes.empty or closes.index[-1] < start:
        return {**{k: START_VALUE for k in SHADOWS}, "b7": START_VALUE}, None
    rates = tbill.reindex(closes.index.union(tbill.index)).ffill().reindex(closes.index).bfill()
    weekly = first_day_of_week(closes.index)
    scores = s.trend_scores(closes, rates)
    out = {}
    for name, fn in SHADOWS.items():
        daily, _, _ = simulate(getattr(s, fn)(closes, rates), scores, opens, closes, rates, weekly, start)
        out[name] = round(float(daily["equity"].iloc[-1]), 2)
    try:
        o7, c7 = b7_bars_fn((start - pd.DateOffset(years=3)).date(), today)
        keep7 = c7.index <= closes.index[-1]
        o7, c7 = o7[keep7], c7[keep7]
        if c7.index[-1] != closes.index[-1]:
            raise ValueError("B7 prices end on a different day")
        r7 = tbill.reindex(c7.index.union(tbill.index)).ffill().reindex(c7.index).bfill()
        out["b7"] = b7_equity(start, o7, c7, r7)
    except Exception:  # noqa: BLE001
        out["b7"] = None
    return out, closes.index[-1].date()


class LogBook:
    DAILY = "daily.csv"
    ORDERS = "orders.csv"
    INCOME = "income.csv"
    ERRORS = "errors.csv"
    STATE = "state.json"

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self.folder / self.STATE
        self.state = json.loads(p.read_text()) if p.exists() else {}
        p = self.folder / self.DAILY
        self.daily = pd.read_csv(p, dtype={"date": str}) if p.exists() else pd.DataFrame()

    def append(self, name, rows):
        if not rows:
            return
        p = self.folder / name
        new = pd.DataFrame(rows)
        if p.exists():
            new = pd.concat([pd.read_csv(p, dtype=str), new.astype(object)], ignore_index=True)
        new.to_csv(p, index=False)

    def write_day(self, row):
        row = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()}
        new = pd.DataFrame([row]).astype({"date": str})
        if not self.daily.empty:
            self.daily = self.daily[self.daily["date"] != row["date"]]
        self.daily = pd.concat([self.daily, new], ignore_index=True)
        self.daily.to_csv(self.folder / self.DAILY, index=False)

    def save(self):
        (self.folder / self.STATE).write_text(json.dumps(self.state, indent=2, sort_keys=True, default=str))


# ---------------------------------------------------------------- helpers

def week_id(day):
    iso = pd.Timestamp(day).isocalendar()
    return f"{iso.year}W{iso.week:02d}"


def week_start_utc(day):
    monday = pd.Timestamp(day) - pd.Timedelta(days=pd.Timestamp(day).weekday())
    return monday.tz_localize(NY).tz_convert("UTC")


def floor_to(x, places):
    f = 10 ** places
    return math.floor(x * f + 1e-9) / f


def add_income(book, today, positions, cash, tbill_rate, days, dividend_fn, credited):
    """Dividends and T-bill interest that paper trading doesn't pay, for
    trading days since the last counted day. Returns (amount, rows, notes)."""
    rows, notes = [], []
    st = book.state
    if "interest_through" not in st:
        st["interest_through"] = st["dividends_through"] = str(today)
        return 0.0, rows, notes
    total = 0.0
    last_i = pd.Timestamp(st["interest_through"]).date()
    n = len([d for d in days if last_i < d <= today])
    if n and cash > 0 and np.isfinite(tbill_rate):
        amount = cash * tbill_rate / s.TRADING_DAYS * n
        total += amount
        rows.append({"date": str(today), "symbol": "cash", "type": "T-bill interest", "shares": "",
                     "per_share": "", "amount": round(amount, 2)})
    st["interest_through"] = str(today)

    last_d = pd.Timestamp(st["dividends_through"]).date()
    try:
        for sym, pos in positions.items():
            if sym == s.CASH_ETF or sym not in MANAGED:
                continue
            divs = dividend_fn(sym)
            for ex_date, per_share in divs.items():
                if last_d < ex_date <= today:
                    if any(c_sym == sym and c_date >= ex_date for c_sym, c_date in credited):
                        notes.append(f"Alpaca credited the {sym} dividend itself; not added")
                        continue
                    amount = pos["qty"] * float(per_share)
                    total += amount
                    rows.append({"date": str(today), "symbol": sym, "type": f"dividend (ex {ex_date})",
                                 "shares": round(pos["qty"], 6), "per_share": float(per_share),
                                 "amount": round(amount, 2)})
        st["dividends_through"] = str(today)
    except Exception as e:  # noqa: BLE001
        notes.append(f"dividend data unavailable ({str(e)[:60]}); will retry next run")
    return total, rows, notes


def plan_trades(targets, scores, current, last_scores, equity):
    """Which ETFs trade and by how many dollars. An ETF trades when its trend
    score changed since its last trade, or its weight drifted more than 2
    points. Returns (dollars by ETF, set of ETFs whose scores get recorded)."""
    dollars, chosen = {}, set()
    for t in s.TICKERS:
        changed = t not in last_scores or float(last_scores[t]) != float(scores[t])
        drift = abs(targets[t] - current[t]) > BUFFER
        if changed or drift:
            chosen.add(t)
            d = (targets[t] - current[t]) * equity
            if abs(d) >= MIN_ORDER:
                dollars[t] = d
    return dollars, chosen


def safety_problems(targets, dollars, equity, account, fresh, data_date, prev_day):
    p = []
    if (targets < -1e-12).any():
        p.append("a target weight is negative")
    if targets.sum() > 1 + 1e-9:
        p.append(f"targets add up to {targets.sum():.2%}, above 100%")
    if (targets > s.MAX_WEIGHT + 1e-9).any():
        p.append(f"a target is above {s.MAX_WEIGHT:.0%}: {targets.idxmax()} {targets.max():.2%}")
    for t, d in dollars.items():
        if abs(d) > MAX_ORDER * equity:
            p.append(f"{t} order of ${abs(d):,.0f} is above {MAX_ORDER:.0%} of equity")
    if not fresh:
        p.append(f"price data ends {data_date}, but the last trading day was {prev_day}")
    if account["cash"] < 0:
        p.append(f"cash is negative (${account['cash']:,.2f})")
    if account["status"] != "ACTIVE" or account["trading_blocked"] or account["account_blocked"]:
        p.append(f"account not open for trading (status {account['status']})")
    return p


def wait_for(broker, orders, timeout):
    """Wait for orders to finish; cancel whatever is still open at the deadline."""
    pending = {o["id"]: o for o in orders}
    done = {}
    deadline = time.monotonic() + timeout
    while pending:
        for oid in list(pending):
            o = broker.order(oid)
            if o["status"] in DONE:
                done[oid] = o
                pending.pop(oid)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    for oid in pending:
        broker.cancel(oid)
        done[oid] = broker.order(oid)
    return list(done.values())


def limit_for(side, price):
    """The worst price an order accepts: 1% above the latest trade for a buy,
    1% below it for a sell, in whole cents (Alpaca refuses fractions of a cent)."""
    if side == "buy":
        return math.floor(price * (1 + LIMIT_BAND) * 100 + 1e-6) / 100
    return math.ceil(price * (1 - LIMIT_BAND) * 100 - 1e-6) / 100


def send(broker, symbol, side, week, qty=None, notional=None, fractionable=True, ref_price=None):
    """One limit order, 1% from the latest trade. A buy given in dollars is sized
    at its limit price, so it can't cost more than `notional`. If Alpaca refuses
    the limit order (HTTP 422), the same shares go as a market order, marked
    fallback_market. Returns the order, or None when there is nothing to send."""
    cid = f"tb-{week}-{symbol}-{side}"
    try:
        price = broker.latest_price(symbol)
    except Exception:  # noqa: BLE001
        price = ref_price
    if not price or price <= 0:
        if qty is None and not fractionable:
            return None
        o = broker.submit(symbol, side, cid + "-m", qty=qty, notional=None if qty is not None else floor_to(notional, 2))
        o["fallback_market"] = True
        return o
    limit = limit_for(side, price)
    if qty is None:
        qty = notional / limit
    qty = floor_to(qty, 9) if fractionable else math.floor(qty + 1e-9)
    if qty <= 0 or (notional is not None and qty * price < MIN_ORDER):
        return None
    try:
        return broker.submit(symbol, side, cid, qty=qty, limit_price=limit)
    except RuntimeError as e:
        if "(422)" not in str(e):
            raise
        o = broker.submit(symbol, side, cid + "-m", qty=qty)
        o["fallback_market"] = True
        return o


def execute(broker, dollars, targets, positions, equity, week, timeout):
    """Sells, then buys sized to the cash actually there, then idle cash into BOXX.
    Never buys more than the account's cash."""
    info = {sym: broker.asset(sym) for sym in {FUND[t] for t in dollars} | {s.CASH_ETF}}
    finished, notes = [], []

    # 1. sells (and enough BOXX to pay for the buys)
    sells, est_cash = [], broker.account()["cash"]
    for t, d in dollars.items():
        if d >= 0:
            continue
        sym = FUND[t]
        held = positions.get(sym)
        if not held:
            continue
        qty = held["qty"] if targets[t] <= 0 else min(held["qty"], -d / held["price"])
        qty = floor_to(qty, 9) if info[sym]["fractionable"] else math.floor(qty + 1e-9)
        if qty > 0:
            o = send(broker, sym, "sell", week, qty=qty, fractionable=info[sym]["fractionable"],
                     ref_price=held["price"])
            if o:
                sells.append(o)
                est_cash += qty * held["price"]
    buys_total = sum(d for d in dollars.values() if d > 0)
    need = buys_total + CASH_BUFFER * equity - est_cash
    boxx = positions.get(s.CASH_ETF)
    if need > MIN_BOXX_ORDER and boxx:
        qty = floor_to(min(boxx["qty"], need / boxx["price"] * 1.001), 9)
        o = send(broker, s.CASH_ETF, "sell", week, qty=qty, fractionable=info[s.CASH_ETF]["fractionable"],
                 ref_price=boxx["price"])
        if o:
            sells.append(o)
    sent_all = list(sells)
    finished += wait_for(broker, sells, timeout)

    # 2. buys, scaled down if the cash isn't there
    budget = broker.account()["cash"] - CASH_BUFFER * equity
    scale = 0.0 if buys_total <= 0 else max(0.0, min(1.0, budget / buys_total))
    if 0 < scale < 0.99:                 # the 0.5% cash buffer alone is expected, not a flag
        notes.append(f"buys scaled to {scale:.1%} of plan to stay within cash")
    buys = []
    for t, d in dollars.items():
        if d <= 0:
            continue
        sym = FUND[t]
        notional = floor_to(d * scale, 2)
        if notional >= MIN_ORDER:
            o = send(broker, sym, "buy", week, notional=notional, fractionable=info[sym]["fractionable"])
            if o:
                buys.append(o)
    sent_all += buys
    finished += wait_for(broker, buys, timeout)

    # 3. idle cash into BOXX
    extra = floor_to(broker.account()["cash"] - CASH_BUFFER * equity, 2)
    if extra >= MIN_BOXX_ORDER:
        o = send(broker, s.CASH_ETF, "buy", week, notional=extra, fractionable=info[s.CASH_ETF]["fractionable"])
        if o:
            sent_all.append(o)
            finished += wait_for(broker, [o], timeout)

    partial = [o["symbol"] for o in finished if o["status"] != "filled"]
    if partial:
        notes.append("not fully filled (price limit or partial fill): " + ", ".join(sorted(set(partial))))
    fallback = [o["symbol"] for o in sent_all if o.get("fallback_market")]
    if fallback:
        notes.append("sent at market because the limit order was refused or no price was available: "
                     + ", ".join(sorted(set(fallback))))
    return finished, notes


# ---------------------------------------------------------------- one run

def run(broker, trading_enabled, log_dir="log", now=None, data=None, dividend_fn=None,
        opens_fn=None, timeout=PHASE_TIMEOUT, shadow_fn=None):
    now = pd.Timestamp(now or pd.Timestamp.now(tz=NY)).tz_convert(NY)
    today = now.date()
    week = week_id(today)
    book = LogBook(log_dir)
    st = book.state
    st.setdefault("last_scores", {})
    if trading_enabled and "live_since" not in st:
        # The live record starts with the first run allowed to trade. Dry runs before it
        # don't count toward returns, income or the peak.
        for k in ("start_date", "start_equity", "peak", "interest_through", "dividends_through",
                  "income_total", "traded_total"):
            st.pop(k, None)
        st["live_since"] = str(today)
    recording = "live_since" in st
    flags, problems = [], []
    row = {"date": str(today), "run_time": now.strftime("%H:%M"), "trading_enabled": trading_enabled}

    account = broker.account()
    row.update(equity_alpaca=account["equity"], cash=account["cash"])
    if not broker.clock()["is_open"]:
        is_trading_day = today in broker.trading_days(today, today)
        row["status"] = "market closed at run time" if is_trading_day else "holiday skip"
        book.write_day(row)
        book.save()
        return row

    days = broker.trading_days(today - timedelta(days=20), today)
    prev_day = max(d for d in days if d < today)
    closes, tbill, source = data if data is not None else load_signal_data(broker, today)
    if source != "yahoo":
        flags.append("fallback data source")
    data_date = closes.index[-1].date()
    scores = s.trend_scores(closes, tbill).iloc[-1]
    all_targets = s.target_weights_v2(closes, tbill)
    fresh = data_date == prev_day and all_targets.index[-1].date() == data_date
    targets = all_targets.iloc[-1]

    positions = broker.positions()
    equity = account["equity"]
    current = pd.Series({t: positions.get(FUND[t], {}).get("market_value", 0.0) / equity for t in s.TICKERS})
    unknown = sorted(set(positions) - MANAGED)
    if unknown:
        flags.append("positions outside the bot's list: " + ", ".join(unknown))

    income, notes = 0.0, []
    if recording:
        income, income_rows, notes = add_income(book, today, positions, account["cash"], float(tbill.iloc[-1]),
                                                days, dividend_fn or yahoo_dividends,
                                                broker.credited_dividends(prev_day))
        book.append(LogBook.INCOME, income_rows)

    status = "no-trade day"
    traded = []
    if st.get("evaluated_week") != week:
        first_day_of_week = min(d for d in days if week_id(d) == week)
        if today != first_day_of_week:
            flags.append(f"catch-up: first trading run of the week (week began {first_day_of_week})")
        dollars, chosen = plan_trades(targets, scores, current, st["last_scores"], equity)
        problems = safety_problems(targets, dollars, equity, account, fresh, data_date, prev_day)
        already = [o for o in broker.orders_since(week_start_utc(today))
                   if str(o.get("client_order_id", "")).startswith(f"tb-{week}-")]
        if already:
            problems.append(f"{len(already)} bot orders already sent this week")
            st["evaluated_week"] = week
        planned = [{"date": str(today), "phase": "plan", "signal_etf": t, "symbol": FUND[t],
                    "side": "buy" if d > 0 else "sell", "planned_dollars": round(d, 2), "status": "",
                    "filled_qty": "", "filled_avg_price": "", "open_price_today": "",
                    "dry_run": not trading_enabled} for t, d in dollars.items()]
        if problems:
            status = "blocked: " + "; ".join(problems)
        elif not trading_enabled:
            status = f"dry run: {len(dollars)} orders planned, none sent"
            book.append(LogBook.ORDERS, planned)
            st["evaluated_week"] = week
        else:
            st["evaluated_week"] = week
            book.save()
            try:
                traded, exec_notes = execute(broker, dollars, targets, positions, equity, week, timeout)
                flags += exec_notes
            except Exception as e:  # noqa: BLE001
                problems.append(f"error during trading: {str(e)[:200]}")
                traded = [broker.order(o["id"]) for o in broker.orders_since(week_start_utc(today))
                          if str(o.get("client_order_id", "")).startswith(f"tb-{week}-")]
            if not problems:
                for t in chosen:
                    st["last_scores"][t] = float(scores[t])
            opens = (opens_fn or (lambda syms: yahoo_opens_today(syms, today)))({o["symbol"] for o in traded})
            book.append(LogBook.ORDERS, [
                {"date": str(today), "phase": "sent", "signal_etf": SIGNAL.get(o["symbol"], o["symbol"]),
                 "symbol": o["symbol"], "side": o["side"],
                 "planned_dollars": round(dollars.get(SIGNAL.get(o["symbol"], ""), 0.0), 2),
                 "status": o["status"], "filled_qty": float(o.get("filled_qty") or 0),
                 "filled_avg_price": float(o.get("filled_avg_price") or 0),
                 "open_price_today": opens.get(o["symbol"], ""), "dry_run": False,
                 "order_type": o.get("type", ""), "limit_price": o.get("limit_price") or ""} for o in traded])
            status = (f"error: {problems[-1]}" if problems else f"traded: {len(traded)} orders")

    # after trading, re-read the account
    if traded:
        account, positions = broker.account(), broker.positions()
        equity = account["equity"]
        current = pd.Series({t: positions.get(FUND[t], {}).get("market_value", 0.0) / equity for t in s.TICKERS})
        off = [f"{t} {current[t] - targets[t]:+.1%}" for t in s.TICKERS if abs(current[t] - targets[t]) > 0.01]
        if off:
            flags.append("more than 1 point off target after trading: " + ", ".join(off))

    filled_dollars = sum(float(o.get("filled_qty") or 0) * float(o.get("filled_avg_price") or 0) for o in traded)
    st["income_total"] = st.get("income_total", 0.0) + income
    st["traded_total"] = st.get("traded_total", 0.0) + filled_dollars
    with_income = account["equity"] + st["income_total"]
    after_costs = with_income - st["traded_total"] * COST_BPS / 1e4
    if recording:
        st.setdefault("start_equity", account["equity"])
        st.setdefault("start_date", str(today))
        st["peak"] = max(st.get("peak", after_costs), after_costs)
        start_equity, peak = st["start_equity"], st["peak"]
    else:
        start_equity = peak = after_costs          # dry runs before going live: nothing to measure yet
    drawdown = after_costs / peak - 1
    if drawdown <= -DRAWDOWN_FLAG:
        flags.append(f"equity {drawdown:.1%} below its peak: review")

    ret_60 = np.nan
    opened = book.daily
    if not opened.empty:
        opened = opened[~opened["status"].astype(str).str.contains("closed|holiday|error")
                        & (opened["date"].astype(str) >= st.get("live_since", "9999"))]
    if len(opened) >= 60:
        ret_60 = after_costs / float(opened["equity_after_costs"].iloc[-60]) - 1
    low, high = backtest_band()
    if np.isfinite(ret_60) and not (low <= ret_60 <= high):
        flags.append(f"60-day return {ret_60:.1%} is outside the backtest band ({low:.1%} to {high:.1%})")

    if recording:
        try:
            shadows, shadow_close = (shadow_fn or shadow_equity)(st["live_since"], today, tbill)
            row["shadow_close"] = str(shadow_close) if shadow_close else ""
            for name, value in shadows.items():
                row[f"shadow_{name}"] = value
            missing = [name for name, value in shadows.items() if value is None]
            if missing:
                flags.append(f"shadow {', '.join(missing)} not updated (data or rule error)")
        except Exception as e:  # noqa: BLE001
            flags.append(f"shadow versions not updated ({str(e)[:80]})")

    row.update(status=status, data_source=source, data_date=str(data_date),
               equity_alpaca=account["equity"], cash=account["cash"],
               boxx_weight=positions.get(s.CASH_ETF, {}).get("market_value", 0.0) / account["equity"],
               invested_weight=float(current.sum()),
               income_today=income, income_total=st["income_total"], equity_with_income=with_income,
               traded_total=st["traded_total"], cost_total=st["traded_total"] * COST_BPS / 1e4,
               equity_after_costs=after_costs, return_since_start=after_costs / start_equity - 1,
               drawdown=drawdown, return_60d=ret_60, band_low=low, band_high=high,
               flags=" | ".join(flags + notes))
    for t in s.TICKERS:
        row[f"score_{t}"] = float(scores[t])
        row[f"target_{t}"] = float(targets[t])
        row[f"actual_{t}"] = float(current[t])
    book.write_day(row)
    st["last_run"] = str(today)
    book.save()
    return row


# ---------------------------------------------------------------- entry points

def simulate(log_dir="sim_log"):
    """A fresh $100,000 account at the latest closes; orders fill in memory."""
    import yfinance as yf
    today = pd.Timestamp.now(tz=NY).date()
    syms = sorted(MANAGED)
    px = yf.download(syms + ["SPY"], period="10d", auto_adjust=True, progress=False)["Close"]
    px = px[px.index.date < today]
    days = [d.date() for d in s.load_prices(start=str(today - timedelta(days=40))).index] + [today]
    broker = SimBroker(100_000, px.iloc[-1].to_dict(), sorted(set(days)))
    row = run(broker, trading_enabled=True, log_dir=log_dir, timeout=0, opens_fn=lambda syms: {})
    return row, broker


CHECK_SYMBOL = "VOO"


def check_orders(broker, log_dir="log", now=None, pause=2.0):
    """One-time check that the paper account accepts the bot's order type.
    Places two tiny VOO buy orders priced 50% below the market, so they cannot
    fill, reads them back and cancels them. Also records each fund's latest
    IEX trade and how old it is, since limit prices are set from it.
    Writes log/order_check.json and returns the results."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz=NY)).tz_convert(NY)
    out = {"run_at": now.isoformat(), "market_open": bool(broker.clock()["is_open"]), "prices": {}, "tests": []}
    for sym in sorted(set(FUND.values()) | {s.CASH_ETF}):
        try:
            price, when = broker.latest_trade(sym)
            out["prices"][sym] = {"price": price, "trade_time": str(when),
                                  "age_minutes": round((now - pd.Timestamp(when)).total_seconds() / 60, 1)}
        except Exception as e:  # noqa: BLE001
            out["prices"][sym] = {"error": str(e)[:200]}
    far = round(broker.latest_price(CHECK_SYMBOL) * 0.5, 2)
    stamp = now.strftime("%Y%m%d%H%M%S")
    cases = [("fractional shares with a limit price (what the bot sends)", {"qty": 0.01}),
             ("dollar amount with a limit price (not used by the bot)", {"notional": 5.0})]
    for i, (name, size) in enumerate(cases, start=1):
        entry = {"test": name, "symbol": CHECK_SYMBOL, "limit_price": far, **size}
        try:
            o = broker.submit(CHECK_SYMBOL, "buy", f"check-{stamp}-{i}", limit_price=far, **size)
            entry.update(accepted=True, status=o.get("status"))
            time.sleep(pause)
            broker.cancel(o["id"])
            time.sleep(pause)
            after = broker.order(o["id"])
            entry.update(status_after_cancel=after.get("status"), filled_qty=float(after.get("filled_qty") or 0))
        except Exception as e:  # noqa: BLE001
            entry.update(accepted=False, error=str(e)[:300])
        out["tests"].append(entry)
    folder = Path(log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "order_check.json").write_text(json.dumps(out, indent=2, default=str))
    return out


def record_crash(error, trading_enabled, log_dir="log", now=None):
    """Write a run that crashed into the log, so the failure shows in the repository.
    A row already written today (say, by an earlier run that traded) is kept."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz=NY)).tz_convert(NY)
    day = str(now.date())
    message = f"{type(error).__name__}: {str(error)[:300]}"
    book = LogBook(log_dir)
    book.append(LogBook.ERRORS, [{"date": day, "run_time": now.strftime("%H:%M"), "error": message}])
    if book.daily.empty or day not in set(book.daily["date"]):
        book.write_day({"date": day, "run_time": now.strftime("%H:%M"),
                        "trading_enabled": trading_enabled, "status": "error: " + message})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--check-orders", action="store_true",
                        help="place two unfillable VOO limit orders on the paper account, then cancel them")
    args = parser.parse_args()
    if args.check_orders:
        broker = AlpacaBroker(os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY"))
        result = check_orders(broker)
        print(json.dumps(result, indent=2, default=str))
        first = result["tests"][0]
        return 0 if first.get("accepted") and not first.get("filled_qty") else 1
    if args.simulate:
        row, broker = simulate()
        print(f"Simulated run on {row['date']}: {row['status']}")
        for o in broker.sent:
            print(f"  {o['side']:4s} {o['symbol']:5s} {o['filled_qty']:>12.4f} sh at ${o['filled_avg_price']:,.2f}"
                  f" = ${o['filled_qty'] * o['filled_avg_price']:>10,.2f}")
        print(f"  equity ${row['equity_alpaca']:,.2f}, cash ${row['cash']:,.2f}, BOXX {row['boxx_weight']:.1%}")
        return 0

    enabled = os.environ.get("TRADING_ENABLED", "false").strip().lower() == "true"
    try:
        broker = AlpacaBroker(os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY"))
        row = run(broker, trading_enabled=enabled)
    except Exception as e:
        record_crash(e, enabled)
        raise                      # the full error still prints in the Actions log; exit code 1
    print(json.dumps({k: row[k] for k in ("date", "status", "equity_alpaca", "flags") if k in row}, default=str))
    return 1 if str(row.get("status", "")).startswith(("blocked", "error")) else 0


if __name__ == "__main__":
    sys.exit(main())
