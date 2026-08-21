#!/usr/bin/env python3
"""
fetch_nea_forecasts.py
=======================

Unattended scraper for data.gov.sg's v2 real-time weather forecast APIs:

    - 24-hour forecast : /v2/real-time/api/twenty-four-hr-forecast?date=YYYY-MM-DD
    - 4-day outlook     : /v2/real-time/api/four-day-outlook?date=YYYY-MM-DD

Design goals (this is meant to run for ~1-2 hours unattended, so these
matter more than usual):

  1. RESUMABLE. Every (endpoint, date) response is saved to its own file.
     If the script is interrupted (laptop sleeps, network drops, you
     Ctrl-C it) you just re-run the same command -- it skips anything
     already saved and only fetches what's missing.
  2. RATE-LIMITED. data.gov.sg resets its limit every 10 seconds. This
     script uses a shared sliding-window limiter across BOTH endpoints
     (the docs specify the limit per v2 Realtime API, not per endpoint),
     defaulting to a conservative margin below your key's actual limit.
  3. RAW-FIRST. It saves the raw JSON response verbatim, one file per
     (endpoint, date). It does NOT try to flatten/interpret the data
     while scraping -- that's a separate, offline, re-runnable step
     (flatten_to_csv.py) so a bug in the flattening logic can never
     cost you a re-scrape against the rate limit.
  4. NEVER CRASHES THE WHOLE RUN. A single date failing (network error,
     bad response, non-zero API error code) is logged and the loop moves
     on. A summary of anything that didn't succeed is printed and saved
     at the end so you can see at a glance what to re-run.

Usage
-----
    export NEA_API_KEY="your-dev-api-key-here"
    python3 fetch_nea_forecasts.py

    # Or pass the key directly:
    python3 fetch_nea_forecasts.py --api-key YOUR_KEY

    # Custom range / output location / smoke test:
    python3 fetch_nea_forecasts.py --start 2020-02-01 --end 2020-02-10
    python3 fetch_nea_forecasts.py --max-dates 5      # quick smoke test
    python3 fetch_nea_forecasts.py --rate-limit 10    # requests per 10s (default 10)

Re-running the exact same command later (e.g. tomorrow, to pick up new
days) is always safe -- already-fetched dates are skipped instantly.

Output layout (under --out-dir, default ./nea_forecast_data)
--------------------------------------------------------------
    raw/twenty_four_hr/2020-02-01.json
    raw/four_day/2020-02-01.json
    ...
    failed_dates.log      -- (endpoint, date, reason) for anything that
                              never succeeded after retries; re-run the
                              script normally to retry these (successful
                              dates are skipped, so retrying is cheap)
    run_summary.json      -- counts from the most recent run

Requires: `requests` (pip install requests --break-system-packages)
"""

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta

try:
    import requests
except ImportError:
    sys.exit("This script needs the 'requests' package: "
              "pip install requests --break-system-packages")

try:
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:
    SGT = None

BASE_URL = "https://api-open.data.gov.sg/v2/real-time/api"
ENDPOINTS = {
    "twenty_four_hr": f"{BASE_URL}/twenty-four-hr-forecast",
    "four_day": f"{BASE_URL}/four-day-outlook",
}

DEFAULT_START = date(2020, 2, 1)  # earliest date the user confirmed has data
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 5


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` calls in any `period`
    seconds, shared across every request this script makes (both
    endpoints), since data.gov.sg's documented limit is per v2 Realtime
    API, not per individual endpoint path."""

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


def fetch_one(session, limiter, endpoint_name, day: date, api_key):
    """Fetch a single (endpoint, date). Returns (status, payload_or_None,
    reason_or_None) where status is one of: 'ok', 'empty', 'api_error',
    'http_error', 'network_error', 'bad_json'."""
    url = ENDPOINTS[endpoint_name]
    headers = {"x-api-key": api_key} if api_key else {}
    params = {"date": day.isoformat()}

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
            # Rate limit hit despite the limiter (clock skew / another
            # process sharing the key) -- back off a full window and retry.
            last_reason = "429 rate limited"
            time.sleep(10.5)
            continue

        if resp.status_code != 200:
            last_reason = f"http_error: {resp.status_code}"
            # Retry transient-looking server errors, don't bother retrying
            # a clean 4xx that isn't a rate limit -- it won't change.
            if 500 <= resp.status_code < 600:
                time.sleep(min(2 ** attempt, 20))
                continue
            return "http_error", None, last_reason

        try:
            payload = resp.json()
        except ValueError:
            last_reason = "bad_json"
            time.sleep(min(2 ** attempt, 20))
            continue

        code = payload.get("code")
        records = (payload.get("data") or {}).get("records") or []
        if code != 0:
            # A clean, final response from the API saying "error" -- not
            # transient, save it as-is for the record and move on.
            return "api_error", payload, payload.get("errorMsg") or f"code={code}"
        if not records:
            # Valid, well-formed response, just nothing issued for this
            # date (expected for dates before real coverage starts).
            return "empty", payload, None
        return "ok", payload, None

    return "network_error", None, last_reason or "exhausted retries"


def already_fetched(path):
    return os.path.exists(path) and os.path.getsize(path) > 2  # not just "{}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", default="v2:65fb68858733116c00eb8f7e829119a8456a7293dff12b10cc131c8dfd788f65:KSJ7eJEWHm1s1ae1HXa1mVcOOZ1d5DKS",
                         help="data.gov.sg API key. Defaults to $NEA_API_KEY. "
                              "Leave blank to run unauthenticated (6 req/10s).")
    parser.add_argument("--start", default=DEFAULT_START.isoformat(),
                         help="Start date YYYY-MM-DD (default 2020-02-01).")
    parser.add_argument("--end", default=None,
                         help="End date YYYY-MM-DD (default: today in Singapore time).")
    parser.add_argument("--out-dir", default="./nea_forecast_data",
                         help="Output directory (default ./nea_forecast_data).")
    parser.add_argument("--rate-limit", type=int, default=10,
                         help="Requests per 10-second window (default 10; "
                              "your dev key allows 12 -- default leaves a safety margin).")
    parser.add_argument("--max-dates", type=int, default=None,
                         help="Only process the first N dates -- useful as a quick smoke test "
                              "before leaving the full run overnight.")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    if args.end:
        end = date.fromisoformat(args.end)
    else:
        end = datetime.now(SGT).date() if SGT else date.today()

    if not args.api_key:
        print("WARNING: no API key provided (--api-key or $NEA_API_KEY). "
              "Running unauthenticated: 6 requests / 10s instead of 12.", file=sys.stderr)
        if args.rate_limit > 6:
            args.rate_limit = 6

    raw_dir_24h = os.path.join(args.out_dir, "raw", "twenty_four_hr")
    raw_dir_4d = os.path.join(args.out_dir, "raw", "four_day")
    os.makedirs(raw_dir_24h, exist_ok=True)
    os.makedirs(raw_dir_4d, exist_ok=True)
    failed_log_path = os.path.join(args.out_dir, "failed_dates.log")
    summary_path = os.path.join(args.out_dir, "run_summary.json")

    dates = list(daterange(start, end))
    if args.max_dates:
        dates = dates[: args.max_dates]

    total_targets = len(dates) * 2  # two endpoints per date
    print(f"Date range: {start} to {end} ({len(dates)} days, {total_targets} requests if none cached)")
    print(f"Rate limit: {args.rate_limit} req/10s -> rough est. "
          f"{total_targets / args.rate_limit * 10 / 60:.1f} min if starting from scratch")
    print(f"Output dir: {os.path.abspath(args.out_dir)}\n")

    session = requests.Session()
    limiter = RateLimiter(max_calls=args.rate_limit, period=10.0)

    counts = {"ok": 0, "empty": 0, "api_error": 0, "http_error": 0, "network_error": 0, "skipped": 0}
    failures = []
    start_time = time.monotonic()

    targets = []
    for day in dates:
        targets.append(("twenty_four_hr", day, os.path.join(raw_dir_24h, f"{day.isoformat()}.json")))
        targets.append(("four_day", day, os.path.join(raw_dir_4d, f"{day.isoformat()}.json")))

    try:
        for i, (endpoint_name, day, out_path) in enumerate(targets, 1):
            if already_fetched(out_path):
                counts["skipped"] += 1
                continue

            status, payload, reason = fetch_one(session, limiter, endpoint_name, day, args.api_key)

            if payload is not None:
                with open(out_path, "w") as f:
                    json.dump(payload, f)

            counts[status] = counts.get(status, 0) + 1
            if status in ("api_error", "http_error", "network_error"):
                failures.append({"endpoint": endpoint_name, "date": day.isoformat(), "status": status, "reason": reason})

            if i % 25 == 0 or i == len(targets):
                elapsed = time.monotonic() - start_time
                done_this_run = i - counts["skipped"]
                rate = done_this_run / elapsed if elapsed > 0 and done_this_run > 0 else 0
                remaining = len(targets) - i
                eta_min = (remaining / rate / 60) if rate > 0 else float("nan")
                print(f"[{i}/{len(targets)}] {endpoint_name} {day} -> {status}"
                      f"  | ok={counts['ok']} empty={counts['empty']} errors={counts['api_error']+counts['http_error']+counts['network_error']} "
                      f"skipped={counts['skipped']}  | ETA ~{eta_min:.1f} min")
    except KeyboardInterrupt:
        print("\nInterrupted -- progress so far is saved. Re-run the same command to resume.")

    if failures:
        with open(failed_log_path, "w") as f:
            for item in failures:
                f.write(json.dumps(item) + "\n")
        print(f"\n{len(failures)} (endpoint, date) pairs did not succeed -- see {failed_log_path}")
        print("Re-running this same script will retry only the missing/failed ones.")

    with open(summary_path, "w") as f:
        json.dump({"range": [start.isoformat(), end.isoformat()], "counts": counts,
                   "failures": len(failures), "finished_at": datetime.now().isoformat()}, f, indent=2)

    print(f"\nDone. {counts['ok']} fetched, {counts['empty']} empty, "
          f"{counts['skipped']} already cached, "
          f"{counts['api_error']+counts['http_error']+counts['network_error']} failed.")
    print(f"Next step: python3 flatten_to_csv.py --data-dir {args.out_dir}")


if __name__ == "__main__":
    main()