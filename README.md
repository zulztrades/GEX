# Zulz GEX dashboard

A free gamma exposure (GEX) dashboard for ES and NQ futures. It updates itself every weekday and lives at one public link.

## How it works

1. **8:45 AM ET:** GitHub runs `run.py`. It downloads the delayed SPX and QQQ option chains from CBOE and the ES/NQ futures prices from CNBC (Yahoo is the backup). It checks that open interest is new for today, works out the walls, the flip and the regime, and saves a snapshot to `docs/history.csv`.
2. **9:30 AM to 4:00 PM ET, every 30 min:** the same steps run again, keeping the morning's basis.
3. Each run commits the new `docs/data.json`. GitHub Pages serves the `docs/` folder as the website.

Weekends and NYSE holidays are skipped (the list is in `config.toml`). Daylight saving is handled by checking the New York clock on every run.

## Files

| File | What it is |
|---|---|
| `config.toml` | All settings: days to expiry (30), profile range (±4%), chart zoom, schedule windows, holidays |
| `gex.py` | Data downloads and the gamma math |
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
