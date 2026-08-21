# NEA Rainfall Scraper (Phase 2 ground-truth data collection)

Fills the gap the forecast scraper exposed: Phase 1's rainfall record
(Admiralty station, 2009-2017) doesn't overlap the forecast scrape's window
(2020-02-01 onward), so nothing in Phase 2 can be calibrated against actual
outcomes yet. This scraper pulls the real-time rainfall API's own history
for the same 2020-02-01-to-today window, so it can be joined directly
against `four_day_outlook.csv` / `twenty_four_hr_general.csv` on date.

Two scripts, same pattern as the forecast scraper: run the first one
unattended, run the second one anytime after (instant, no network).

## 1. `fetch_nea_rainfall.py` — the unattended scraper

```bash
pip install requests --break-system-packages   # one-time, skip if already done

export NEA_API_KEY="your-dev-api-key"

# Smoke test first -- 3 dates, should take well under a minute:
python3 fetch_nea_rainfall.py --max-dates 3

# Check nea_rainfall_data/raw/ has per-date folders with page_00.json,
# page_01.json, ... and a _complete.json in each, then run the real thing:
python3 fetch_nea_rainfall.py
```

**Why this one takes longer than the forecast scraper.** The forecast
scraper was ~2 requests/day (4,784 total, ~70-80 min). Rainfall is reported
in 5-minute windows, and a plain `date=YYYY-MM-DD` query only returns 25
readings per page — so a full day takes **~12 paginated requests**, not 2.
Across the same 2020-02-01-to-today range (2,392 days) that's **~28,700
requests**, roughly:

| Rate limit | Estimated time |
|---|---|
| 6 req/10s (no key) | ~13.3 hours |
| 10 req/10s (default, safety margin) | ~8.0 hours |
| 12 req/10s (full dev-key allowance) | ~6.6 hours |

Same "leave it running overnight" shape as the forecast scrape, just a
longer overnight.

**How pagination actually works (reverse-engineered, confirmed, not
guessed).** You tested `?date=2020-12-01` by hand and got back 25 readings
plus `"paginationToken": "b2Zmc2V0PTI1"`. Decoding that token
(`base64.b64decode(...)`) gives the literal string `offset=25` — the token
isn't an opaque cursor, it's just a base64-wrapped offset into the day's
reading list. That means the script doesn't need to wait for the server to
hand back each token before it can ask for the next page: it computes
`paginationToken = base64("offset=" + N*25)` itself for page N. It still
reads whatever token the server actually returns and uses "no token in the
response" as the authoritative signal that a date is finished (a short
final page also stops it, as a second check) — so if the real offset step
ever isn't exactly 25 for some page, the script still terminates correctly
instead of assuming a fixed page count blindly.

**Resumable at the page level, not just the day level.** Every `(date,
page)` response is saved to its own file
(`raw/<date>/page_00.json`, `page_01.json`, ...). A date only gets marked
done (`raw/<date>/_complete.json`) once a page comes back signalling "no
more data." If the script is interrupted mid-day, re-running the exact same
command resumes that date from its next unfetched page — it does not
re-request pages already on disk, and it does not re-request whole days
that already have a `_complete.json`. Safe to run once now and again later
to pick up new dates.

Progress prints every 10 dates with a running ETA. At the end, check
`nea_rainfall_data/run_summary.json` and `failed_dates.log` — anything in
the failed log is a genuine failure (bad response after retries, or a day
that hit the 40-page safety cap without a clean "done" signal, which would
itself be worth flagging back). Re-running the script retries only
incomplete dates.

## 2. `collate_rainfall.py` — turn the raw pages into daily-mm-per-station

```bash
python3 collate_rainfall.py --data-dir nea_rainfall_data
```

Reads every `raw/<date>/page_*.json`, de-duplicates readings by (station,
timestamp), and sums each station's 5-minute totals into a daily mm figure.
Produces:

- `stations.csv` — station_id, name, latitude, longitude for every station
  seen (74-101 stations network-wide; there's no "Admiralty" station in the
  real-time network, so this is here to support picking the nearest proxy
  station once you're ready to decide — several Woodlands-area candidates
  came up earlier: S100 Woodlands Road, S104 Woodlands Avenue 9, S210
  Woodlands Centre, S211 Kranji Road, S227 Woodlands Drive 62).
- `daily_rainfall_mm.csv` — one row per (date, station_id): `total_mm`,
  `slots_present`, `slots_expected` (288), `completeness_pct`. This is the
  fidelity-preserving part: a day with only 60% of its 5-minute slots
  present is never silently treated as if the missing 40% were zero
  rainfall — it's flagged via `completeness_pct` so you can decide a
  cutoff (e.g. exclude/flag any day below 90% complete) rather than have
  that decision made invisibly inside the aggregation.

Calendar-day attribution uses each reading's own timestamp (converted to
Singapore time), not the query-date folder it happened to be fetched under
— this matters right at midnight, where a page fetched under one date's
folder can legitimately contain a reading that belongs to the adjacent
calendar day.

Safe to re-run anytime, including mid-scrape, to refresh the CSVs with
whatever's been fetched so far.

## What I verified before handing this over

Same situation as the forecast scraper: no live network route to
`api-open.data.gov.sg` from this sandbox, so no real end-to-end smoke test
from here. What I did verify:

- The pagination mechanic itself — decoded your real `paginationToken`
  (`b2Zmc2V0PTI1` → `offset=25`) to confirm it's a plain base64-wrapped
  offset, not an opaque cursor.
- The full scrape+resume+collate loop against a mocked HTTP layer built to
  mimic the real response shape (`data.readings[]`, `data.stations[]`,
  `data.paginationToken`) — covering a full 288-slot day, a short/partial
  day, a mid-day interruption-and-resume (confirmed it resumes from the
  correct next page, not page 0, and makes zero extra network calls for
  already-fetched pages), a 429 that recovers on retry, a second run
  against an already-complete date making zero network calls, and the
  daily-mm summation + completeness-percentage math in `collate_rainfall.py`
  (checked against hand-computed expected totals). All 5 test cases pass —
  see `test_mock_run.py`, safe to delete once you trust it or re-run
  anytime with `python3 test_mock_run.py`.

Worth double-checking once real output exists: watch the first few dates
of live output to confirm `readings` is in fact the field name the live
API uses (it's what your two hand-tested responses showed, and the script
has a couple of fallback field names plus an explicit warning if a page
has stations but no recognizable reading list — but this is the one thing
that depends on the live API matching the sampled shape exactly across
every date, not just the two you tested by hand).
