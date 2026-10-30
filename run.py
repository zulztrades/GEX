"""Runs the GEX pipeline and writes the files the dashboard reads.

    python run.py           normal scheduled run (decides by ET clock whether to do anything)
    python run.py --force   run now regardless of the clock
    python run.py --check   only print what mode this run would be (used by the workflow)
"""
import csv
import datetime as dt
import json
import os
import sys
import time
import tomllib
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
STATE = ROOT / "data" / "state.json"
HISTORY_COLS = [
    "date", "time_et", "es_contract", "es_price", "es_regime", "es_flip", "es_call_wall", "es_put_wall",
    "nq_contract", "nq_price", "nq_regime", "nq_flip", "nq_call_wall", "nq_put_wall",
    "es_oi", "nq_oi", "warnings",
]


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1), encoding="utf-8")


def decide_mode(now, cfg, force):
    s = cfg["schedule"]
    t = now.strftime("%H:%M")
    not_holiday = now.date().isoformat() not in cfg["calendar"]["holidays"]
    trading_day = now.weekday() < 5 and not_holiday
    evening_day = now.weekday() in (6, 0, 1, 2, 3) and not_holiday  # Sunday-Thursday nights
    if trading_day and s["morning_start"] <= t < s["rth_start"]:
        return "morning"
    if trading_day and s["rth_start"] <= t <= s["day_end"]:
        return "intraday"
    if evening_day and s["evening_start"] <= t <= s["evening_end"]:
        return "evening"
    return "manual" if force else "skip"


def get_conversion(key, ch, quote, meta, mode, state, today):
    """Basis (ES) or ratio (NQ from QQQ) from the latest settlement, held for the whole session.
    Daytime uses the prior day's settle; evening uses that afternoon's settle."""
    import gex
    session = "evening" if mode == "evening" else "day"
    saved = state.get("conv", {}).get(key)
    if (mode in ("intraday", "evening") and saved and saved["date"] == today
            and saved["chain"] == ch["name"] and saved.get("session") == session):
        return saved
    close, close_src = gex.index_close(ch["yahoo_close"], quote["settle_date"], meta)
    value = quote["settle"] - close if ch["conversion"] == "additive" else quote["settle"] / close
    conv = {
        "date": today, "session": session, "chain": ch["name"], "type": ch["conversion"], "value": value,
        "settle": quote["settle"], "settle_date": quote["settle_date"],
        "index_close": close, "close_source": close_src,
    }
    state.setdefault("conv", {})[key] = conv
    return conv


def build_instrument(key, icfg, cfg, now, mode, state, warnings):
    import gex
    calc, today = cfg["calc"], now.date()

    quote, qerr = gex.futures_quote(icfg, now)
    if qerr:
        warnings.append(f"{key}: main futures price source failed, used {quote['source']} instead.")

    chain_errors = []
    attempts = 1 + (cfg["schedule"]["oi_retries"] if mode == "morning" else 0)
    for ch in icfg["chains"]:
        try:
            for attempt in range(attempts):
                meta, options = gex.fetch_chain(ch["symbol"], cfg["sources"]["cboe_url"])
                c = gex.parse_chain(options, now, calc["max_dte_days"], calc["min_hours_to_expiry"], ch["am_roots"])
                fp = gex.oi_fingerprint(c, today)
                stale = gex.oi_is_stale(fp, state.get("oi_prev", {}).get(ch["name"]))
                if not stale or attempt == attempts - 1:
                    break
                print(f"{ch['name']} open interest looks stale, retrying in {cfg['schedule']['oi_retry_wait_min']} min")
                time.sleep(cfg["schedule"]["oi_retry_wait_min"] * 60)
            break
        except Exception as e:
            chain_errors.append(f"{ch['name']}: {e}")
    else:
        raise RuntimeError(f"{key}: every option chain failed: " + " | ".join(chain_errors))
    if ch is not icfg["chains"][0]:
        warnings.append(f"{key}: {icfg['chains'][0]['name']} options unavailable, levels built from {ch['name']} instead.")
    state.setdefault("oi_last", {})[ch["name"]] = fp
    if stale:
        warnings.append(f"{key}: {ch['name']} open interest looks unchanged from yesterday. Walls may reflect yesterday's positioning.")

    conv = get_conversion(key, ch, quote, meta, mode, state, today.isoformat())
    additive = conv["type"] == "additive"
    to_index = (lambda f: f - conv["value"]) if additive else (lambda f: f / conv["value"])
    to_fut = (lambda x: x + conv["value"]) if additive else (lambda x: x * conv["value"])
    F = lambda x: gex.round_tick(to_fut(x), calc["tick"])

    spot = to_index(quote["last"])
    cboe_spot = float(meta.get("current_price") or 0)
    if cboe_spot and abs(spot / cboe_spot - 1) > 0.03:
        warnings.append(f"{key}: futures-implied {ch['name']} ({spot:.2f}) is far from CBOE's last ({cboe_spot:.2f}). Double check levels.")

    res = gex.analyze(c, spot, calc["risk_free_rate"], ch["dividend_yield"], calc["profile_range_pct"],
                      calc["profile_steps"], calc["secondary_levels"])
    price = gex.round_tick(quote["last"], calc["tick"])
    for name in ("call_wall", "put_wall"):
        if abs(res[name] / spot - 1) > 0.08:
            warnings.append(f"{key}: {name.replace('_', ' ')} is more than 8% from price. Treat it with caution.")

    expected = gex.front_contract(icfg["root"], today)
    contract = quote["contract"] or expected
    if contract != expected:
        warnings.append(f"{key}: price feed is on {contract} but volume rolls to {expected}. Levels are for {contract}.")

    lo, hi = spot * (1 - icfg["chart_range_pct"] / 100), spot * (1 + icfg["chart_range_pct"] / 100)
    strikes = [
        {"k": F(k), "s": float(k), "c": round(cv / 1e9, 4), "p": round(pv / 1e9, 4)}
        for k, cv, pv in zip(res["strikes"], res["call"], res["put"]) if lo <= k <= hi
    ]
    profile = [[F(g), round(v / 1e9, 4)] for g, v in list(zip(res["grid"], res["profile"]))[::4]]

    return {
        "contract": contract,
        "roll_week": gex.roll_week(today),
        "price": price,
        "price_time": quote["time"],
        "regime": "positive" if res["net_gex"] > 0 else "negative",
        "net_gex_bn": round(res["net_gex"] / 1e9, 3),
        "flip": F(res["flip"]) if res["flip"] else None,
        "flip_side": None if res["flip"] else ("above" if res["profile_above_all"] else "below"),
        "call_wall": F(res["call_wall"]),
        "put_wall": F(res["put_wall"]),
        "secondary": [F(s) for s in res["secondary"]],
        "oi_status": "unknown" if stale is None else ("stale" if stale else "fresh"),
        "index": {
            "name": ch["name"], "spot": round(spot, 2), "flip": round(res["flip"], 2) if res["flip"] else None,
            "call_wall": res["call_wall"], "put_wall": res["put_wall"], "secondary": res["secondary"],
            "contracts_used": len(c["K"]), "options_time_utc": meta.get("timestamp"),
        },
        "conversion": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in conv.items()},
        "sources": {"price": quote["source"], "options": f"CBOE {ch['name']} (delayed)"},
        "strikes": strikes,
        "profile": profile,
    }


def write_history(data, mode):
    """One row per day: the morning snapshot (or the first run of the day if the morning was missed)."""
    path = DOCS / "history.csv"
    rows = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    today = data["date"]
    existing = next((r for r in rows if r["date"] == today), None)
    if mode in ("manual", "evening") or (mode == "intraday" and existing):
        return
    # keep the first morning snapshot unless its open interest wasn't confirmed fresh
    if mode == "morning" and existing and existing["es_oi"] == "fresh" and existing["nq_oi"] == "fresh":
        return
    es, nq = data["instruments"]["ES"], data["instruments"]["NQ"]
    row = {
        "date": today, "time_et": data["updated_et"][11:16],
        "es_contract": es["contract"], "es_price": es["price"], "es_regime": es["regime"], "es_flip": es["flip"],
        "es_call_wall": es["call_wall"], "es_put_wall": es["put_wall"],
        "nq_contract": nq["contract"], "nq_price": nq["price"], "nq_regime": nq["regime"], "nq_flip": nq["flip"],
        "nq_call_wall": nq["call_wall"], "nq_put_wall": nq["put_wall"],
        "es_oi": es["oi_status"], "nq_oi": nq["oi_status"], "warnings": " / ".join(data["warnings"]),
    }
    rows = [r for r in rows if r["date"] != today] + [row]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_COLS)
        w.writeheader()
        w.writerows(rows)


def main():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    now = dt.datetime.now(ET)
    mode = decide_mode(now, cfg, "--force" in sys.argv)

    if "--check" in sys.argv:
        print(mode)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"mode={mode}\n")
        return 0
    if mode == "skip":
        print(f"{now:%a %H:%M} ET: outside trading windows, nothing to do")
        return 0

    status = load_json(DOCS / "status.json", {})
    status.update({"last_attempt_et": now.isoformat(timespec="seconds"), "mode": mode})
    state = load_json(STATE, {})
    today = now.date().isoformat()
    # First daytime run of a new day: yesterday's OI becomes the comparison. Evening runs reuse
    # the day's comparison, since OI doesn't change after the close.
    if mode != "evening" and state.get("oi_last_date") != today:
        state["oi_prev"] = state.get("oi_last", {})
        state["oi_last_date"] = today

    try:
        warnings = []
        instruments = {key: build_instrument(key, icfg, cfg, now, mode, state, warnings)
                       for key, icfg in cfg["instruments"].items()}
        if any(i["roll_week"] for i in instruments.values()):
            warnings.append("Roll week: volume is moving to the next contract. Make sure the levels match the contract on your chart.")
        data = {
            "date": today,
            "updated_et": dt.datetime.now(ET).isoformat(timespec="seconds"),
            "mode": mode,
            "max_dte_days": cfg["calc"]["max_dte_days"],
            "warnings": warnings,
            "instruments": instruments,
        }
        save_json(DOCS / "data.json", data)
        write_history(data, mode)
        save_json(STATE, state)
        status.update({"ok": True, "error": None, "last_success_et": data["updated_et"]})
        save_json(DOCS / "status.json", status)
        for k, v in instruments.items():
            print(f"{k} {v['contract']} price {v['price']} | {v['regime']} {v['net_gex_bn']}bn | flip {v['flip']} "
                  f"| call wall {v['call_wall']} | put wall {v['put_wall']} | secondary {v['secondary']} | OI {v['oi_status']}")
        for w in warnings:
            print("WARNING:", w)
        return 0
    except Exception as e:
        traceback.print_exc()
        status.update({"ok": False, "error": str(e)[:400]})
        save_json(DOCS / "status.json", status)
        return 1


if __name__ == "__main__":
    sys.exit(main())
