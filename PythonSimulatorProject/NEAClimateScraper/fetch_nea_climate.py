#!/usr/bin/env python3
"""
fetch_nea_climate.py
=====================

Unattended scraper for three more data.gov.sg v2 real-time endpoints, closing
the gaps found running the NEA_Data_Analysis notebook:

    https://api-open.data.gov.sg/v2/real-time/api/air-temperature?date=YYYY-MM-DD
    https://api-open.data.gov.sg/v2/real-time/api/relative-humidity?date=YYYY-MM-DD
    https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast?date=YYYY-MM-DD

Same pagination convention as fetch_nea_rainfall.py, confirmed against a live
response for all three endpoints: a plain `date=YYYY-MM-DD` query returns 25
items per page plus a `paginationToken` when more remain, and that token
base64-decodes to the literal string `offset=<N>` -- so this script computes
`offset = page_num * 25` itself rather than waiting on the server's token.

WHY THIS ONE IS MUCH SLOWER THAN THE RAINFALL SCRAPE: rainfall reports every
5 minutes (~12 pages/day). air-temperature and relative-humidity report every
1 MINUTE -- confirmed live (23:59, 23:58, ... 25 one-minute readings on page
0) -- so a full day is ~58 pages EACH, not ~12. two-hr-forecast is cheap by
contrast: it's reissued roughly every 30 min, ~2 pages/day. This script
prints an honest per-endpoint ETA before starting so a bad surprise doesn't
show up 3 hours in -- if you only need to close a gap (e.g. the FYP notebook's
daily_climate.csv currently stops at 2025-06-02), pass a later --start for a
much shorter run than the full 2020-02-01 range.

Design goals (same as fetch_nea_rainfall.py -- this can run for hours
unattended, so these matter more than usual):

  1. RESUMABLE per (endpoint, date, PAGE). Every response is saved to its own
     file. A (endpoint, date) is only marked done once a page comes back
     signalling "no more data" (see is_last_page below), at which point a
     `_complete.json` marker is written. Re-running the same command skips
     anything already marked complete and resumes any partial date from its
     next unfetched page, not page 0.
  2. RATE-LIMITED, one shared sliding-window limiter across every endpoint
     and every request this script makes (data.gov.sg's limit is per v2
     Realtime API, not per endpoint path).
  3. RAW-FIRST. Saves raw JSON verbatim per page. Turning it into CSVs is a
     separate, offline, re-runnable step (collate_climate.py) so a bug in
     that logic never costs a re-scrape against the rate limit.
  4. NEVER CRASHES THE WHOLE RUN. A single page failing is retried with
     backoff; if it never succeeds the (endpoint, date, page) is recorded in
     failed_dates.log and the loop moves on.
  5. SAFETY-CAPPED per endpoint (see ENDPOINTS below) so a malformed response
     that never signals "last page" can't spin forever on one date.

Usage
-----
    # Put your key in the repo-root .env (NEA_API_KEY=...) -- auto-loaded, no
    # export needed. Or: export NEA_API_KEY="your-dev-api-key-here"

    # Smoke test first -- a handful of dates, all three endpoints:
    python3 fetch_nea_climate.py --max-dates 3

    # Just close the notebook's temperature gap (much cheaper than the full
    # 2020-02-01 range -- see the printed ETA to compare):
    python3 fetch_nea_climate.py --start 2025-06-02 --endpoints air-temperature,relative-humidity

    # Two-hr-forecast is cheap -- fine to run for the full range on its own:
    python3 fetch_nea_climate.py --endpoints two-hr-forecast

    # Configure where raw pages land:
    python3 fetch_nea_climate.py --out-dir "D:/nea_climate_data"

Output layout (under --out-dir, default ./nea_climate_data)
--------------------------------------------------------------
    raw/air-temperature/2020-02-01/page_00.json
    raw/air-temperature/2020-02-01/page_01.json
    ...
    raw/air-temperature/2020-02-01/_complete.json
    raw/relative-humidity/2020-02-01/page_00.json
    ...
    raw/two-hr-forecast/2020-02-01/page_00.json
    ...
    failed_dates.log
    run_summary.json

Next step: python3 collate_climate.py --data-dir <same --out-dir>
           (lets you point the flattened CSVs at a different --csv-out-dir,
           e.g. straight at NEA_Data_Analysis/)

Requires: `requests` (pip install requests --break-system-packages)
Recommended: `truststore` -- if this machine's Python can't verify NEA's TLS
cert (Windows machines using a corporate/AV root not in certifi's bundle
commonly can't), `pip install truststore` and this script will use it
automatically to fall back to the OS trust store, the same fix already
in use for this repo's git config.
Recommended: `python-dotenv` -- lets NEA_API_KEY live in the repo-root .env
(same convention as Backend/DataGovAPI/data.py) instead of an env var you
have to re-export every session. Both are optional -- the script degrades
gracefully (skips the shim / falls back to $NEA_API_KEY) if either isn't
installed.
"""

import argparse
import base64
import glob
import json
import os
import re
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # fine if this machine's Python already trusts the OS cert store

try:
    import requests
except ImportError:
    sys.exit("This script needs the 'requests' package: "
              "pip install requests --break-system-packages")

# Repo-root .env (same file/convention as Backend/DataGovAPI/data.py's
# NEA_API_KEY), loaded by explicit path so this works regardless of the
# directory the script is launched from -- not just via load_dotenv()'s
# CWD-relative upward search.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass  # fine if python-dotenv isn't installed; --api-key/$NEA_API_KEY still work

try:
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:
    SGT = None

BASE_URL = "https://api-open.data.gov.sg/v2/real-time/api"

# endpoint key -> URL path, the field name holding the per-page item list
# (confirmed live: air-temperature/relative-humidity use "readings", two-hr-
# forecast uses "items"), expected pages/day, and a safety cap.
ENDPOINTS = {
    "air-temperature":   dict(path="air-temperature",   list_key="readings", est_pages_per_day=58, max_pages_per_day=90),
    "relative-humidity": dict(path="relative-humidity", list_key="readings", est_pages_per_day=58, max_pages_per_day=90),
    "two-hr-forecast":   dict(path="two-hr-forecast",   list_key="items",    est_pages_per_day=2,  max_pages_per_day=10),
}

DEFAULT_START = date(2020, 2, 1)  # matches the other scrapers' window
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 5
PAGE_SIZE = 25  # confirmed live for all three endpoints


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` calls in any `period`
    seconds, shared across every endpoint this script hits."""

    def __init__(self, max_calls, period=10.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()

    def wait(self):
        now = time.monotonic()
        while self.calls and now - self.calls[0] > self.period:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            sleep_for = self.period - (now - self.calls[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()
        self.calls.append(time.monotonic())


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def offset_token(offset: int) -> str:
    return base64.b64encode(f"offset={offset}".encode()).decode()


def count_items(payload, list_key):
    data = payload.get("data") or {}
    val = data.get(list_key)
    if isinstance(val, list):
        return len(val)
    # defensive fallback in case a page's shape ever differs from the
    # sampled endpoint's usual key
    for key in ("readings", "items", "records"):
        if key in data and isinstance(data[key], list):
            return len(data[key])
    return 0


def fetch_page(session, limiter, endpoint_key, day: date, offset: int, api_key):
    """Fetch a single (endpoint, date, offset) page. Returns (status,
    payload_or_None, reason_or_None, next_offset_or_None)."""
    spec = ENDPOINTS[endpoint_key]
    url = f"{BASE_URL}/{spec['path']}"
    headers = {"x-api-key": api_key} if api_key else {}
    params = {"date": day.isoformat()}
    if offset > 0:
        params["paginationToken"] = offset_token(offset)

    last_reason = None
    for attempt in range(1, MAX_RETRIES + 1):
        limiter.wait()
        try:
            resp = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            last_reason = f"network_error: {e}"
            time.sleep(min(2 ** attempt, 20))
            continue

        if resp.status_code == 429:
            last_reason = "429 rate limited"
            time.sleep(10.5)
            continue

        if resp.status_code != 200:
            last_reason = f"http_error: {resp.status_code}"
            if 500 <= resp.status_code < 600:
                time.sleep(min(2 ** attempt, 20))
                continue
            return "http_error", None, last_reason, None

        try:
            payload = resp.json()
        except ValueError:
            last_reason = "bad_json"
            time.sleep(min(2 ** attempt, 20))
            continue

        code = payload.get("code")
        if code != 0:
            return "api_error", payload, payload.get("errorMsg") or f"code={code}", None

        n_items = count_items(payload, spec["list_key"])
        server_token = (payload.get("data") or {}).get("paginationToken")
        # Same rule as fetch_nea_rainfall.py: trust the token's absence as
        # the authoritative "done" signal; a short/empty page is a second,
        # independent check.
        is_last_page = (not server_token) or (n_items < PAGE_SIZE)
        next_offset = None if is_last_page else offset + PAGE_SIZE
        return "ok", payload, None, next_offset

    return "network_error", None, last_reason or "exhausted retries", None


def existing_pages(day_dir):
    pages = []
    for p in glob.glob(os.path.join(day_dir, "page_*.json")):
        m = re.search(r"page_(\d+)\.json$", p)
        if m:
            pages.append(int(m.group(1)))
    return sorted(pages)


def is_complete(day_dir):
    return os.path.exists(os.path.join(day_dir, "_complete.json"))


def fetch_day(session, limiter, endpoint_key, day, out_root, api_key, counts, failures):
    spec = ENDPOINTS[endpoint_key]
    day_dir = os.path.join(out_root, endpoint_key, day.isoformat())
    os.makedirs(day_dir, exist_ok=True)

    if is_complete(day_dir):
        counts["days_skipped"] += 1
        return

    pages_done = existing_pages(day_dir)
    page_num = (pages_done[-1] + 1) if pages_done else 0
    offset = page_num * PAGE_SIZE

    while page_num < spec["max_pages_per_day"]:
        page_path = os.path.join(day_dir, f"page_{page_num:02d}.json")
        status, payload, reason, next_offset = fetch_page(session, limiter, endpoint_key, day, offset, api_key)

        if status != "ok":
            counts[status] = counts.get(status, 0) + 1
            failures.append({"endpoint": endpoint_key, "date": day.isoformat(), "page": page_num,
                              "status": status, "reason": reason})
            return  # leave the date incomplete; a re-run resumes from this page

        with open(page_path, "w") as f:
            json.dump(payload, f)
        counts["pages_ok"] += 1
        counts["items_total"] += count_items(payload, spec["list_key"])

        if next_offset is None:
            with open(os.path.join(day_dir, "_complete.json"), "w") as f:
                json.dump({"pages": page_num + 1, "finished_at": datetime.now().isoformat()}, f)
            counts["days_ok"] += 1
            return

        page_num += 1
        offset = next_offset

    counts["capped"] = counts.get("capped", 0) + 1
    failures.append({"endpoint": endpoint_key, "date": day.isoformat(), "page": page_num, "status": "capped",
                      "reason": f"hit max_pages_per_day={spec['max_pages_per_day']} without a last-page signal"})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", default=os.environ.get("NEA_API_KEY", ""),
                         help="data.gov.sg API key. Defaults to $NEA_API_KEY, which is auto-loaded from "
                              "the repo-root .env's NEA_API_KEY= line if set there. "
                              "Leave blank to run unauthenticated (6 req/10s).")
    parser.add_argument("--start", default=DEFAULT_START.isoformat(),
                         help="Start date YYYY-MM-DD (default 2020-02-01). "
                              "Pass a later date to close a specific gap instead of re-scraping everything.")
    parser.add_argument("--end", default=None,
                         help="End date YYYY-MM-DD (default: today in Singapore time).")
    parser.add_argument("--endpoints", default=",".join(ENDPOINTS),
                         help=f"Comma-separated subset of: {','.join(ENDPOINTS)} (default: all three).")
    parser.add_argument("--out-dir", default="./nea_climate_data",
                         help="Output directory for raw pages (default ./nea_climate_data). "
                              "Point this wherever you want the downloaded data to live.")
    parser.add_argument("--rate-limit", type=int, default=10,
                         help="Requests per 10-second window (default 10; your dev key allows 12).")
    parser.add_argument("--max-dates", type=int, default=None,
                         help="Only process the first N dates per endpoint -- smoke test before the full run.")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else (datetime.now(SGT).date() if SGT else date.today())
    endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    for e in endpoints:
        if e not in ENDPOINTS:
            sys.exit(f"Unknown endpoint '{e}'. Choose from: {','.join(ENDPOINTS)}")

    if not args.api_key:
        print("WARNING: no API key provided (--api-key or $NEA_API_KEY). "
              "Running unauthenticated: 6 requests / 10s instead of 12.", file=sys.stderr)
        if args.rate_limit > 6:
            args.rate_limit = 6

    dates = list(daterange(start, end))
    if args.max_dates:
        dates = dates[: args.max_dates]

    out_root = os.path.join(args.out_dir, "raw")
    os.makedirs(out_root, exist_ok=True)
    failed_log_path = os.path.join(args.out_dir, "failed_dates.log")
    summary_path = os.path.join(args.out_dir, "run_summary.json")

    print(f"Date range: {start} to {end} ({len(dates)} days)")
    print(f"Endpoints: {', '.join(endpoints)}")
    print("Estimated cost per endpoint at this rate limit:")
    total_pages = 0
    for e in endpoints:
        p = ENDPOINTS[e]["est_pages_per_day"] * len(dates)
        total_pages += p
        mins = p / args.rate_limit * 10 / 60
        print(f"  {e:<20} ~{p:>8,} pages  -> ~{mins:>7.1f} min ({mins/60:.1f} h)  [{ENDPOINTS[e]['est_pages_per_day']} pages/day typical]")
    print(f"  {'TOTAL':<20} ~{total_pages:>8,} pages  -> ~{total_pages/args.rate_limit*10/60:>7.1f} min "
          f"({total_pages/args.rate_limit*10/3600:.1f} h)")
    print("air-temperature/relative-humidity are ~29x costlier per day than rainfall was (1-min readings, "
          "not 5-min) -- if you only need to close a gap, re-run with a later --start.")
    print(f"Output dir: {os.path.abspath(args.out_dir)}\n")

    session = requests.Session()
    limiter = RateLimiter(max_calls=args.rate_limit, period=10.0)

    counts = {"days_ok": 0, "days_skipped": 0, "pages_ok": 0, "items_total": 0,
              "api_error": 0, "http_error": 0, "network_error": 0, "capped": 0}
    failures = []
    start_time = time.monotonic()

    targets = [(e, d) for e in endpoints for d in dates]

    try:
        for i, (endpoint_key, day) in enumerate(targets, 1):
            fetch_day(session, limiter, endpoint_key, day, out_root, args.api_key, counts, failures)
            if i % 20 == 0 or i == len(targets):
                elapsed = time.monotonic() - start_time
                done = counts["days_ok"] + counts["days_skipped"]
                rate = done / elapsed if elapsed > 0 and done > 0 else 0
                eta_min = ((len(targets) - i) / rate / 60) if rate > 0 else float("nan")
                errors = counts['api_error'] + counts['http_error'] + counts['network_error'] + counts['capped']
                print(f"[{i}/{len(targets)}] {endpoint_key} {day} done | ok={counts['days_ok']} skipped={counts['days_skipped']} "
                      f"pages={counts['pages_ok']} items={counts['items_total']} errors={errors} | ETA ~{eta_min:.1f} min")
    except KeyboardInterrupt:
        print("\nInterrupted -- progress so far is saved. Re-run the same command to resume "
              "(partially-fetched dates pick up from their next page, not page 0).")

    if failures:
        with open(failed_log_path, "w") as f:
            for item in failures:
                f.write(json.dumps(item) + "\n")
        print(f"\n{len(failures)} (endpoint, date, page) failures -- see {failed_log_path}")
        print("Re-running this same script will retry only the incomplete (endpoint, date) pairs.")

    with open(summary_path, "w") as f:
        json.dump({"range": [start.isoformat(), end.isoformat()], "endpoints": endpoints, "counts": counts,
                   "failures": len(failures), "finished_at": datetime.now().isoformat()}, f, indent=2)

    errors = counts['api_error'] + counts['http_error'] + counts['network_error'] + counts['capped']
    print(f"\nDone. {counts['days_ok']} (endpoint,day) pairs completed, {counts['days_skipped']} already cached, "
          f"{errors} with failures.")
    print(f"Next step: python3 collate_climate.py --data-dir {args.out_dir}")


if __name__ == "__main__":
    main()
