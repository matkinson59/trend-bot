"""
broker.py: the two accounts the live bot can talk to.

AlpacaBroker  Alpaca's paper trading API, over plain HTTPS. It refuses any
              address other than the paper one.
SimBroker     An in-memory account for tests and local dry runs. Orders fill
              at prices the caller supplies; nothing leaves the computer.

Both offer the same methods, so bot.py works with either.
"""
import math
import time
import uuid

import pandas as pd
import requests

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
DONE = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced", "stopped", "suspended"}


class AlpacaBroker:
    def __init__(self, key_id, secret_key, base_url=PAPER_URL, session=None):
        if base_url.rstrip("/") != PAPER_URL:
            raise ValueError(f"The bot only runs against Alpaca's paper address ({PAPER_URL}), not {base_url}")
        if not key_id or not secret_key:
            raise ValueError("Alpaca keys are missing: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
        self.base = PAPER_URL
        self.session = session or requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key})

    def _request(self, method, url, **kwargs):
        for attempt in range(3):
            r = self.session.request(method, url, timeout=30, **kwargs)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Alpaca {method} {url} failed ({r.status_code}): {r.text[:300]}")
            return r.json() if r.content else None
        raise RuntimeError(f"Alpaca {method} {url} failed after 3 tries ({r.status_code})")

    def _get(self, path, **params):
        return self._request("GET", self.base + path, params=params)

    def account(self):
        a = self._get("/v2/account")
        return {"equity": float(a["equity"]), "cash": float(a["cash"]), "status": a["status"],
                "trading_blocked": bool(a["trading_blocked"]), "account_blocked": bool(a["account_blocked"])}

    def positions(self):
        return {p["symbol"]: {"qty": float(p["qty"]), "market_value": float(p["market_value"]),
                              "price": float(p["current_price"])}
                for p in self._get("/v2/positions")}

    def clock(self):
        c = self._get("/v2/clock")
        return {"is_open": bool(c["is_open"]), "timestamp": pd.Timestamp(c["timestamp"])}

    def trading_days(self, start, end):
        days = self._get("/v2/calendar", start=str(start), end=str(end))
        return [pd.Timestamp(d["date"]).date() for d in days]

    def asset(self, symbol):
        a = self._get(f"/v2/assets/{symbol}")
        return {"tradable": bool(a["tradable"]), "fractionable": bool(a["fractionable"])}

    def orders_since(self, after):
        return self._get("/v2/orders", status="all", after=pd.Timestamp(after).isoformat(),
                         limit=500, direction="asc")

    def submit(self, symbol, side, client_order_id, qty=None, notional=None):
        body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day",
                "client_order_id": client_order_id}
        if (qty is None) == (notional is None):
            raise ValueError("give qty or notional, not both")
        if qty is not None:
            body["qty"] = f"{qty:.9f}".rstrip("0").rstrip(".")
        else:
            body["notional"] = f"{notional:.2f}"
        return self._request("POST", self.base + "/v2/orders", json=body)

    def order(self, order_id):
        return self._get(f"/v2/orders/{order_id}")

    def cancel(self, order_id):
        self._request("DELETE", self.base + f"/v2/orders/{order_id}")

    def latest_price(self, symbol):
        t = self._request("GET", f"{DATA_URL}/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
        return float(t["trade"]["p"])

    def credited_dividends(self, after):
        """Dividends Alpaca itself credited (paper trading normally credits none)."""
        try:
            acts = self._get("/v2/account/activities/DIV", after=pd.Timestamp(after).isoformat())
        except RuntimeError:
            return []
        return [(a.get("symbol"), pd.Timestamp(a.get("date") or a.get("transaction_time")).date()) for a in acts or []]

    def daily_closes(self, symbols, start, end):
        """Dividend- and split-adjusted daily closes from Alpaca (fallback data source).
        `end` should be today's date: the free data plan refuses full-market (SIP)
        data from the last 15 minutes, and the bot only uses finished days."""
        out, token = {}, None
        while True:
            params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": str(start),
                      "end": str(end), "adjustment": "all", "feed": "sip", "limit": 10000}
            if token:
                params["page_token"] = token
            page = self._request("GET", f"{DATA_URL}/v2/stocks/bars", params=params)
            for sym, bars in (page.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = page.get("next_page_token")
            if not token:
                break
        frames = {sym: pd.Series({pd.Timestamp(b["t"]).tz_convert("America/New_York").normalize().tz_localize(None): b["c"]
                                  for b in bars}) for sym, bars in out.items()}
        return pd.DataFrame(frames)[list(symbols)].sort_index()


class SimBroker:
    """In-memory paper account. `prices` maps symbol -> fill price."""

    def __init__(self, cash, prices, trading_days, is_open=True, now=None,
                 partial_fill=None, not_fractionable=(), past_orders=()):
        self.cash = float(cash)
        self.prices = dict(prices)
        self.qty = {}
        self.days = sorted(trading_days)
        self.is_open = is_open
        self.now = now or pd.Timestamp.now(tz="America/New_York")
        self.partial_fill = partial_fill or {}      # symbol -> share of the order that fills
        self.not_fractionable = set(not_fractionable)
        self.history = list(past_orders)
        self.sent = []

    def account(self):
        mv = sum(q * self.prices[s] for s, q in self.qty.items())
        return {"equity": self.cash + mv, "cash": self.cash, "status": "ACTIVE",
                "trading_blocked": False, "account_blocked": False}

    def positions(self):
        return {s: {"qty": q, "market_value": q * self.prices[s], "price": self.prices[s]}
                for s, q in self.qty.items() if q > 1e-12}

    def clock(self):
        return {"is_open": self.is_open, "timestamp": self.now}

    def trading_days(self, start, end):
        return [d for d in self.days if pd.Timestamp(start).date() <= d <= pd.Timestamp(end).date()]

    def asset(self, symbol):
        return {"tradable": symbol in self.prices, "fractionable": symbol not in self.not_fractionable}

    def orders_since(self, after):
        return [o for o in self.history if pd.Timestamp(o["submitted_at"]) >= pd.Timestamp(after)]

    def submit(self, symbol, side, client_order_id, qty=None, notional=None):
        if any(o["client_order_id"] == client_order_id for o in self.history):
            raise RuntimeError(f"client_order_id {client_order_id} already used")
        price = self.prices[symbol]
        want = qty if qty is not None else math.floor(notional / price * 1e9) / 1e9
        if symbol in self.not_fractionable and abs(want - round(want)) > 1e-9:
            raise RuntimeError(f"requested asset is not fractionable: {symbol}")
        fill = want * self.partial_fill.get(symbol, 1.0)
        if side == "sell":
            if fill > self.qty.get(symbol, 0) + 1e-9:
                raise RuntimeError(f"insufficient qty to sell {symbol}")
            self.qty[symbol] = self.qty.get(symbol, 0) - fill
            self.cash += fill * price
        else:
            if fill * price > self.cash + 1e-6:
                raise RuntimeError(f"insufficient buying power for {symbol}")
            self.qty[symbol] = self.qty.get(symbol, 0) + fill
            self.cash -= fill * price
        order = {"id": str(uuid.uuid4()), "client_order_id": client_order_id, "symbol": symbol, "side": side,
                 "status": "filled" if fill == want else "partially_filled",
                 "qty": want, "filled_qty": fill, "filled_avg_price": price, "notional": notional,
                 "submitted_at": self.now.isoformat()}
        self.history.append(order)
        self.sent.append(order)
        return dict(order)

    def order(self, order_id):
        return dict(next(o for o in self.history if o["id"] == order_id))

    def cancel(self, order_id):
        for o in self.history:
            if o["id"] == order_id and o["status"] not in DONE:
                o["status"] = "canceled"

    def latest_price(self, symbol):
        return self.prices[symbol]

    def credited_dividends(self, after):
        return []
