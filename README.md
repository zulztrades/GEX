# Zulz GEX dashboard

A free gamma exposure (GEX) dashboard for ES and NQ futures. It updates itself every weekday and lives at one public link.

## How it works

1. GitHub starts `run.py --loop` before each trading window (8:00 AM - 12:15 PM ET weekdays, 7:00 - 9:45 PM ET Sunday to Thursday). It waits for the window, then refreshes every 15 minutes until it closes.
2. Each refresh downloads the delayed SPX and QQQ option chains from Yahoo (CBOE blocked automated access on Nov 19, 2026) and the newest ES/NQ futures price from CNBC or Yahoo. It checks that open interest is new for today and works out the walls, the flip and the regime. The first good morning refresh is saved to `docs/history.csv`.
3. Each refresh commits the new `docs/data.json`. GitHub Pages serves the `docs/` folder as the website.

Weekends and NYSE holidays are skipped (the list is in `config.toml`). Daylight saving is handled by checking the New York clock on every run.

## Files

| File | What it is |
|---|---|
| `config.toml` | All settings: days to expiry (30), profile range (±4%), chart zoom, schedule windows, holidays |
| `chain.py` | Downloads the option chains and works out implied vol |
| `gex.py` | Futures prices, contract calendar and the gamma math |
| `run.py` | Runs everything and writes the files the page reads |
| `docs/` | The website (index.html, style.css, app.js) plus `data.json`, `status.json`, `history.csv` |
| `data/state.json` | Memory between runs: today's basis and yesterday's open interest, used for the stale check |
| `.github/workflows/update.yml` | The schedule |

## Run it on your computer

```
pip install -r requirements.txt
python run.py --force
python -m http.server 8789 -d docs
```

Then open http://localhost:8789.

## Updating holidays

Each December, add the next year's NYSE holidays to `holidays` in `config.toml`.
