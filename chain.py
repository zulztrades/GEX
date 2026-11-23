"""Option chains from Yahoo (free, ~15 min delayed), with implied vol worked out from prices.

Two quirks of Yahoo's data are handled here:
- When two contract types share a date and strike (SPX and SPXW on monthly expirations), its
  list shows only one. The hidden ones are looked up by name, or the biggest strikes go missing.
- Its own implied vol is unusable for many contracts, so it is solved from option prices.
"""
import datetime as dt
import time
from math import erf
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
YEAR_SECS = 365 * 24 * 3600
QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"


def retry(fn, tries=3, wait=4):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))


def clean(x):
    """Yahoo leaves blanks as None/NaN."""
    try:
        x = float(x)
        return x if np.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def split_id(cid):
    """SPXW261218C07000000 -> root, yymmdd, C/P, strike"""
    return cid[:-15], cid[-15:-9], cid[-9], int(cid[-8:]) / 1000


def listed(ticker, exp):
    ch = retry(lambda: ticker.option_chain(exp))
    if len(ch.calls) == 0 or len(ch.puts) == 0:
        raise RuntimeError(f"calls or puts missing for {exp}")
    rows = []
    for df in (ch.calls, ch.puts):
        for r in df.itertuples():
            ts = r.lastTradeDate
            rows.append({
                "option": r.contractSymbol, "oi": clean(r.openInterest), "bid": clean(r.bid), "ask": clean(r.ask),
                "last": clean(r.lastPrice),
                "time": ts.to_pydatetime().astimezone(ET) if ts is not None and ts == ts else None,
            })
    return rows, ch.underlying


def hidden(rows, spot):
    """Look up contracts Yahoo hid because another root has the same date and strike."""
    from yfinance.data import YfData
    have = {r["option"] for r in rows}
    parts = [split_id(o) for o in have]
    roots = {p[0] for p in parts}
    if len(roots) < 2:
        return []
    ymd = parts[0][1]
    strikes = {p[3] for p in parts if 0.5 * spot <= p[3] <= 1.5 * spot}
    want = {f"{root}{ymd}{cp}{round(k * 1000):08d}" for root in roots for cp in "CP" for k in strikes}
    want = sorted(want - have)
    api, out = YfData(), []
    for i in range(0, len(want), 80):
        batch = ",".join(want[i:i + 80])
        res = retry(lambda: api.get_raw_json(QUOTE_URL, params={"symbols": batch}))
        for q in res["quoteResponse"]["result"]:
            t = q.get("regularMarketTime")
            out.append({
                "option": q["symbol"], "oi": clean(q.get("openInterest")), "bid": clean(q.get("bid")),
                "ask": clean(q.get("ask")), "last": clean(q.get("regularMarketPrice")),
                "time": dt.datetime.fromtimestamp(t, ET) if t else None,
            })
    return [r for r in out if r["option"] not in have]


def norm_cdf(x):
    return 0.5 * (1 + np.vectorize(erf)(x / np.sqrt(2)))


def implied_vol(price, F, K, T, r, is_call):
    """Black-76 implied vol by bisection, for arrays."""
    lo, hi = np.full(len(K), 0.005), np.full(len(K), 4.0)
    for _ in range(50):
        mid = (lo + hi) / 2
        sq = mid * np.sqrt(T)
        d1 = np.log(F / K) / sq + sq / 2
        call = np.exp(-r * T) * (F * norm_cdf(d1) - K * norm_cdf(d1 - sq))
        model = np.where(is_call, call, call - np.exp(-r * T) * (F - K))
        low = model < price
        lo, hi = np.where(low, mid, lo), np.where(low, hi, mid)
    return (lo + hi) / 2


def price_points(rows, live, tq):
    """{option: price} from one moment in time: live mid quotes, or the last session's closing trades."""
    if live:
        pts = {}
        for r in rows:
            if 0 < r["bid"] < r["ask"] <= r["bid"] * 2 + 0.5:
                pts[r["option"]] = (r["bid"] + r["ask"]) / 2
            elif r["last"] > 0 and r["time"] and abs((r["time"] - tq).total_seconds()) < 1800:
                pts[r["option"]] = r["last"]
        return pts
    day = [r for r in rows if r["last"] > 0 and r["time"] and r["time"].date() == tq.date()]
    late = [r for r in day if r["time"].hour >= 15]
    return {r["option"]: r["last"] for r in (late if len(late) >= 12 else day)}


def vol_curve(rows, exp, live, tq, spot, rate, div, am_roots):
    """One vol curve (strikes, IVs) per expiration from out-of-the-money prices. None if prices are too thin."""
    def years(root):
        stop = dt.time(9, 30) if root in am_roots else dt.time(16, 0)
        return (dt.datetime.combine(exp, stop, ET) - tq).total_seconds() / YEAR_SECS

    pts = price_points(rows, live, tq)
    info = {o: split_id(o) for o in pts}
    forwards = {}
    for root in {p[0] for p in info.values()}:
        T = years(root)
        if T <= 0:
            continue
        calls = {p[3]: pts[o] for o, p in info.items() if p[0] == root and p[2] == "C"}
        puts = {p[3]: pts[o] for o, p in info.items() if p[0] == root and p[2] == "P"}
        near = sorted((k for k in calls if k in puts and abs(k / spot - 1) < 0.04), key=lambda k: abs(k - spot))[:9]
        guess = spot * np.exp((rate - div) * T)
        F = guess
        if len(near) >= 3:  # put-call parity
            F = float(np.median([k + np.exp(rate * T) * (calls[k] - puts[k]) for k in near]))
        forwards[root] = F if abs(F / guess - 1) < 0.03 else guess

    use = [(o, p) for o, p in info.items()
           if p[0] in forwards and (p[2] == "C") == (p[3] >= forwards[p[0]]) and pts[o] >= 0.03]
    if len(use) < 10:
        return None
    K = np.array([p[3] for _, p in use])
    iv = implied_vol(np.array([pts[o] for o, _ in use]), np.array([forwards[p[0]] for _, p in use]), K,
                     np.array([years(p[0]) for _, p in use]), rate, np.array([p[2] == "C" for _, p in use]))
    ok = (iv > 0.03) & (iv < 2.5)
    K, iv = K[ok], iv[ok]
    if len(K) < 10 or K.min() > spot * 0.99 or K.max() < spot * 1.01:
        return None
    order = np.argsort(K)
    K, iv = K[order], iv[order]
    iv = np.array([np.median(iv[max(0, i - 2):i + 3]) for i in range(len(iv))])  # drop stray prints
    keep = np.unique(np.linspace(0, len(K) - 1, min(len(K), 60)).astype(int))
    return [[round(float(x), 2) for x in K[keep]], [round(float(x), 4) for x in iv[keep]]]


def fetch_chain(symbol, now, max_dte, rate, div, am_roots, trading_day, saved):
    """Returns (meta, options, curves) with options shaped for gex.parse_chain. "saved" holds the
    last good vol curves, reused when prices are too thin (mainly the first minutes after the open)."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    today = now.date()
    last_day = today + dt.timedelta(days=max_dte)
    exps = [e for e in retry(lambda: ticker.options) if today <= dt.date.fromisoformat(e) <= last_day]
    if not exps:
        raise RuntimeError(f"{symbol}: no expirations returned")
    by_exp, und = {}, {}
    for e in exps:
        by_exp[e], u = listed(ticker, e)
        und = u or und
    spot = clean(und.get("regularMarketPrice"))
    if spot <= 0:
        raise RuntimeError(f"{symbol}: no underlying price")
    for e in exps:
        by_exp[e] += hidden(by_exp[e], spot)

    # From 9:30 to 9:50 the last session's prices are being replaced but live ones aren't in yet.
    opening = trading_day and dt.time(9, 30) <= now.time() < dt.time(9, 50)
    # Prices are live from about 9:50 (15 min delay). Before that, use the last completed session.
    live = trading_day and dt.time(9, 50) <= now.time() <= dt.time(16, 15)
    if live:
        tq, ref = now - dt.timedelta(minutes=15), spot
    else:
        done = [r["time"].date() for rows in by_exp.values() for r in rows
                if r["time"] and (r["time"].date() < today or now.time() > dt.time(16, 15))]
        if not done:
            raise RuntimeError(f"{symbol}: no completed session in the data")
        tq = dt.datetime.combine(max(done), dt.time(16, 0), ET)
        ref = (clean(und.get("regularMarketPreviousClose")) if opening else 0) or spot

    options, curves, total, covered, reused = [], {}, 0.0, 0.0, 0
    for e in exps:
        oi = sum(r["oi"] for r in by_exp[e])
        total += oi
        fresh = None
        if not (opening and e in saved):
            fresh = vol_curve(by_exp[e], dt.date.fromisoformat(e), live, tq, ref, rate, div, am_roots)
        curve = fresh or saved.get(e)
        if not curve:
            continue
        reused += not fresh
        curves[e] = curve
        covered += oi
        k, iv = np.log(curve[0]), curve[1]
        options += [{"option": r["option"], "open_interest": r["oi"],
                     "iv": float(np.interp(np.log(split_id(r["option"])[3]), k, iv))} for r in by_exp[e]]
    every = [r for rows in by_exp.values() for r in rows]
    if sum(r["oi"] > 0 for r in every) < 0.5 * len(every):
        raise RuntimeError(f"{symbol}: open interest is mostly blank right now")
    if covered < 0.9 * total:
        raise RuntimeError(f"{symbol}: prices cover only {covered / total:.0%} of open interest right now")
    print(f"{symbol}: {len(exps)} expirations, {len(every)} contracts, {reused} vol curves reused, "
          f"{'live' if live else tq.date()} prices")
    meta = {"current_price": spot, "prices": "live" if live else f"close {tq.date()}",
            "timestamp": tq.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    return meta, options, curves
