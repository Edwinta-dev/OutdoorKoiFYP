# NEA Climate Scraper (closes the notebook's temperature gap + adds a 2-hr nowcast reference)

Two gaps `forecast_reality_pipeline.ipynb` surfaced on its first real run:

1. `daily_climate.csv` (air temperature) only covers 2020-02-01 → 2025-06-02 —
   about 15 months short of the full range, which starves every heat-related
   section (§4, §5 PREEMPT, §6) of 2025-2026 test data. No script in the repo
   shows how that file was originally generated, so its exact aggregation
   convention can't be verified either.
2. There was no reality-adjacent signal shorter-lead than the 4-day outlook.
   The 2-hour nowcast (reissued every ~30 min, per-area) is a plausible
   stand-in for "close to ground truth" where a station observation isn't
   available for a given location.

Same two-script shape as the other scrapers here: run the fetcher unattended,
run the collator anytime after (instant, no network).

## 1. `fetch_nea_climate.py` — the unattended scraper

```bash
pip install requests truststore --break-system-packages   # truststore only needed if your
                                                            # Python can't verify NEA's TLS cert
                                                            # (see note below)
export NEA_API_KEY="your-dev-api-key"

# Smoke test first -- 2-3 dates, all three endpoints:
python3 fetch_nea_climate.py --max-dates 3

# Check nea_climate_data/raw/<endpoint>/<date>/page_00.json, ... and a
# _complete.json per date, then run the real thing. Read the printed ETA
# table first -- see below for why air-temperature/relative-humidity are
# much more expensive than everything else in this repo's scrapers.
python3 fetch_nea_climate.py

# Just close the gap instead of re-scraping 2020-2025 (much cheaper):
python3 fetch_nea_climate.py --start 2025-06-02 --endpoints air-temperature,relative-humidity

# Two-hr-forecast is cheap on its own -- fine to run for the full range:
python3 fetch_nea_climate.py --endpoints two-hr-forecast

# Configure where the raw downloaded pages land:
python3 fetch_nea_climate.py --out-dir "D:/some/other/place"
```

**Why air-temperature/relative-humidity are ~29x costlier per day than the
rainfall scrape.** Rainfall reports every 5 minutes (~12 pages/day). These
two report every **1 minute** — confirmed live (page 0 for a test date
returned 25 one-minute readings, 23:59 down to 23:35) — so a full day is
~58 pages each. Across the full 2020-02-01-to-today range (~2,400 days) that's
roughly:

| Scope | Pages (both endpoints) | @ 10 req/10s |
|---|---|---|
| Full range (2020-02-01 → today) | ~278,000 | ~77 hours |
| Just the gap (2025-06-02 → today) | ~56,000 | ~15.5 hours |

Neither is a single overnight run at the default rate limit. The script
prints the exact numbers for whatever `--start`/`--end`/`--endpoints` you
pass before it starts, so decide the scope you want with real numbers rather
than a guess — `--start 2025-06-02` (just the gap) is the recommended default
unless there's a reason to distrust the existing pre-2025-06 file. Two-hr-
forecast is cheap regardless (~2 pages/day, same order of magnitude as the
24hr/4-day forecast scrape already in this repo).

**Same resumability/rate-limiting/raw-first design as `fetch_nea_rainfall.py`**
— see that script's own docstring for the reasoning; this one just
generalizes it across three endpoints sharing one rate limiter. Progress
prints every 20 (endpoint, date) pairs with a running ETA. At the end, check
`run_summary.json` and `failed_dates.log`. Re-running the script retries only
incomplete (endpoint, date) pairs.

**TLS certificate note.** This scraper needs a live HTTPS connection to
`api-open.data.gov.sg`. On a Windows machine where Python's bundled `certifi`
CA list doesn't include a corporate/AV root that's already trusted by the OS
(the same class of problem this repo's git config already had —
`http.sslBackend=schannel`), plain `requests` calls will fail with
`CERTIFICATE_VERIFY_FAILED` even though `curl` and the browser work fine. The
fix here is the `truststore` package: install it and the script uses it
automatically (`truststore.inject_into_ssl()`) to make Python trust whatever
the OS already trusts, mirroring the schannel fix for git. If your machine
doesn't have this problem, `truststore` is simply skipped.

## 2. `collate_climate.py` — turn the raw pages into CSVs

```bash
python3 collate_climate.py --data-dir nea_climate_data

# Write straight into the notebook's data folder instead:
python3 collate_climate.py --data-dir nea_climate_data --csv-out-dir ../../NEA_Data_Analysis
```

Produces:

- **`daily_climate.csv`** — same column layout as the notebook's existing
  file (`date, t_mean, t_max, t_min, rh_mean, rh_min, n_samples_t,
  n_samples_rh, stations_used, source`), but with an explicit, documented
  aggregation rule (see the script's docstring) since the old file's
  generating script isn't in the repo. Calendar-day attribution uses each
  reading's own timestamp in Singapore time, same reasoning as
  `collate_rainfall.py`'s midnight handling. There's no Woodlands-specific
  temperature station the way there is for rainfall (only S104 of the
  4-station rain cluster exists in this ~12-station network at all), so this
  is a network-wide mean, not a cluster-local one — `source` says so
  explicitly (`network_mean(<n> stations)`) rather than reusing the old
  file's undocumented `"cluster/cluster"` label, so a partially-regenerated
  file is never silently mistaken for the original.
- **`two_hr_forecast.csv`** — one row per (issuance, area): `query_date,
  issued_at, update_timestamp, valid_from, valid_to, valid_text, area,
  forecast_text`. Filter to `area == "Woodlands"` for the pond's proxy
  location. This is a much shorter-lead, more frequent signal than the 4-day
  outlook — treat it as a second, area-resolved observation-ish reference
  rather than a forecast to validate the same way `four_day_outlook.csv` is.

Safe to re-run anytime, including mid-scrape, to refresh the CSVs with
whatever's been fetched so far.

## What I verified before handing this over

Unlike the other two scrapers' handoff notes, this one WAS live-tested against
the real API from this machine (once `truststore` resolved the TLS issue
above): confirmed the pagination mechanic (25 items/page, same
base64("offset=N") token as rainfall) for all three endpoints, confirmed
`readings` is the list field for air-temperature/relative-humidity and
`items` for two-hr-forecast, confirmed the ~12-station network for
air-temperature/relative-humidity (including S104, but none of the other
three Woodlands rain-cluster stations), confirmed two-hr-forecast has
historical coverage at least back to 2025-06 (didn't confirm all the way to
2020-02-01 — worth checking the first real run's `failed_dates.log`/`empty`
counts for the earliest dates), and ran `fetch_nea_climate.py --max-dates 1`
end-to-end against the live API as a smoke test.
