"""Data fetching and gamma exposure math. run.py calls into this."""
import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

import numpy as np
import requests

ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (zulz-gex dashboard)"}
OPT_RE = re.compile(r"^([A-Z]+?)(\d{6})([CP])(\d{8})$")
YEAR_SECS = 365 * 24 * 3600


def get_json(url, tries=3, wait=5):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # network hiccup, bad JSON, HTTP error
            last = e
            if i < tries - 1:
                time.sleep(wait * (i + 1))
    raise RuntimeError(f"{url} failed: {last}")


def num(s):
    return float(str(s).replace(",", ""))


def round_tick(x, tick=0.25):
    return round(round(x / tick) * tick, 2)


# ---------- futures quotes ----------

def cnbc_quote(symbol):
    """Last price + official prior settlement + contract code (e.g. ESZ26)."""
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           f"?symbols={symbol}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
    q = get_json(url)["FormattedQuoteResult"]["FormattedQuote"][0]
    return {
        "last": num(q["last"]),
        "settle": num(q["settlePrice"]),
        "settle_date": q["settleDate"][:10],
        "contract": q.get("altSymbol") or None,
        "time": q.get("last_time"),
        "source": "CNBC",
    }


def yahoo_quote(symbol, now):
    """Fallback. Settlement is approximated by the price at 4:00 PM ET (when ES/NQ settle)."""
    import yfinance as yf
    bars = yf.Ticker(symbol).history(period="5d", interval="5m")
    if bars.empty:
        raise RuntimeError(f"Yahoo returned no bars for {symbol}")
    bars.index = bars.index.tz_convert(ET)
    eod = bars[(bars.index.hour == 15) & (bars.index.minute == 55)]
    eod = eod[eod.index + dt.timedelta(minutes=5) <= now]
    if eod.empty:
        raise RuntimeError(f"Yahoo has no 4 PM bar for {symbol}")
    return {
        "last": float(bars["Close"].iloc[-1]),
        "settle": float(eod["Close"].iloc[-1]),
        "settle_date": eod.index[-1].date().isoformat(),
        "contract": None,
        "time": bars.index[-1].isoformat(),
        "source": "Yahoo",
    }


def futures_quote(icfg, now):
    errors = []
    for fn in (lambda: cnbc_quote(icfg["cnbc_symbol"]), lambda: yahoo_quote(icfg["yahoo_symbol"], now)):
        try:
            return fn(), errors
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("All futures price sources failed: " + " | ".join(errors))


def index_close(yahoo_symbol, date_iso, chain_meta):
    """Close of the index/ETF on the settlement date (Yahoo, then CBOE as fallback)."""
    try:
        import yfinance as yf
        h = yf.Ticker(yahoo_symbol).history(period="15d", interval="1d")
        for ts, row in h.iterrows():
            if ts.date().isoformat() == date_iso:
                return float(row["Close"]), "Yahoo"
    except Exception:
        pass
    # CBOE: "close" is the latest session's close, "prev_day_close" the one before.
    last_day = (chain_meta.get("last_trade_time") or "")[:10]
    if last_day == date_iso:
        return float(chain_meta["close"]), "CBOE"
    if last_day > date_iso and chain_meta.get("prev_day_close"):
        return float(chain_meta["prev_day_close"]), "CBOE"
    raise RuntimeError(f"No {yahoo_symbol} close found for {date_iso}")


# ---------- contract calendar ----------

def third_friday(y, m):
    d = dt.date(y, m, 15)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def quarterlies(around):
    for y in (around.year - 1, around.year, around.year + 1):
        for m, code in ((3, "H"), (6, "M"), (9, "U"), (12, "Z")):
            exp = third_friday(y, m)
            yield code, y, exp, exp - dt.timedelta(days=8)  # roll = Thursday 8 days before expiry


def front_contract(root, d):
    """Contract most volume trades in on date d (switches on roll Thursday)."""
    for code, y, exp, roll in quarterlies(d):
        if d < roll:
            return f"{root}{code}{y % 100:02d}"


def roll_week(d):
    """True from the Monday of roll week through expiration Friday."""
    for _, _, exp, roll in quarterlies(d):
        if roll - dt.timedelta(days=3) <= d <= exp:
            return True
    return False


# ---------- option chain ----------

def fetch_chain(symbol, url_tpl):
    raw = get_json(url_tpl.format(symbol=symbol))
    data = raw["data"]
    meta = {k: v for k, v in data.items() if k != "options"}
    meta["timestamp"] = raw.get("timestamp")
    if not data.get("options"):
        raise RuntimeError(f"{symbol} chain is empty")
    return meta, data["options"]


def parse_chain(options, now, max_dte, min_hours, am_roots):
    """Keep contracts expiring within max_dte days that have OI and a usable IV."""
    cutoff = now.date() + dt.timedelta(days=max_dte)
    rows = []
    for o in options:
        m = OPT_RE.match(o.get("option", ""))
        if not m:
            continue
        root, ymd, cp, strike = m.groups()
        exp = dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
        if exp > cutoff:
            continue
        oi = o.get("open_interest") or 0
        iv = o.get("iv") or 0
        if oi <= 0 or not 0.01 < iv < 3:
            continue
        # AM-settled monthlies stop at the open, everything else at 4 PM
        stop = dt.time(9, 30) if root in am_roots else dt.time(16, 0)
        secs = (dt.datetime.combine(exp, stop, ET) - now).total_seconds()
        if secs <= 0:
            continue
        t = max(secs, min_hours * 3600) / YEAR_SECS
        rows.append((int(strike) / 1000, t, iv, oi, 1.0 if cp == "C" else -1.0, o["option"], exp))
    if not rows:
        raise RuntimeError("No usable contracts after filtering")
    k, t, iv, oi, sign, ids, exps = zip(*rows)
    return {
        "K": np.array(k), "T": np.array(t), "iv": np.array(iv), "oi": np.array(oi),
        "sign": np.array(sign), "ids": ids, "exp": exps,
    }


def contract_gex(S, c, r, q):
    """Dollar GEX per 1% move for each contract at underlying price S (calls +, puts -)."""
    sq = c["iv"] * np.sqrt(c["T"])
    d1 = (np.log(S / c["K"]) + (r - q + 0.5 * c["iv"] ** 2) * c["T"]) / sq
    gamma = np.exp(-q * c["T"]) * np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * sq)
    return c["sign"] * gamma * c["oi"] * 100 * S * S * 0.01


def analyze(c, spot, r, q, range_pct, steps, n_secondary):
    g = contract_gex(spot, c, r, q)
    strikes = np.unique(c["K"])
    idx = np.searchsorted(strikes, c["K"])
    call = np.zeros(len(strikes))
    put = np.zeros(len(strikes))
    np.add.at(call, idx[c["sign"] > 0], g[c["sign"] > 0])
    np.add.at(put, idx[c["sign"] < 0], g[c["sign"] < 0])
    net = call + put

    call_wall = float(strikes[np.argmax(call)])
    put_wall = float(strikes[np.argmin(put)])
    order = np.argsort(-np.abs(net))
    secondary = [float(strikes[i]) for i in order if strikes[i] not in (call_wall, put_wall)][:n_secondary]

    grid = np.linspace(spot * (1 - range_pct / 100), spot * (1 + range_pct / 100), steps)
    profile = np.array([contract_gex(s, c, r, q).sum() for s in grid])
    flip = None
    cross = np.where(np.sign(profile[:-1]) * np.sign(profile[1:]) < 0)[0]
    if len(cross):
        xs = [grid[i] - profile[i] * (grid[i + 1] - grid[i]) / (profile[i + 1] - profile[i]) for i in cross]
        flip = float(min(xs, key=lambda x: abs(x - spot)))

    return {
        "net_gex": float(g.sum()),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "secondary": secondary,
        "flip": flip,
        "profile_above_all": bool(flip is None and profile[0] > 0),
        "strikes": strikes, "call": call, "put": put,
        "grid": grid, "profile": profile,
    }


def oi_fingerprint(c, today, n=300):
    """OI of the n largest contracts expiring 4+ days out. Used to spot stale open interest."""
    keep = [i for i, e in enumerate(c["exp"]) if (e - today).days >= 4]
    keep.sort(key=lambda i: -c["oi"][i])
    return {c["ids"][i]: int(c["oi"][i]) for i in keep[:n]}


def oi_is_stale(current, previous):
    """Stale if 95%+ of the same contracts still show yesterday's exact OI. None = can't tell."""
    common = set(current) & set(previous or {})
    if len(common) < 50:
        return None
    same = sum(current[k] == previous[k] for k in common)
    return same / len(common) >= 0.95
