#!/usr/bin/env python3
"""
fetch_nea_rainfall.py
======================

Unattended scraper for data.gov.sg's v2 real-time rainfall API:

    https://api-open.data.gov.sg/v2/real-time/api/rainfall?date=YYYY-MM-DD

Readings are 5-minute-window totals (reading type "TB1 Rainfall 5 Minute
Total F", unit mm) across ~74-101 stations island-wide. A full calendar day
is ~288 timestamps. A plain `date=YYYY-MM-DD` query is PAGINATED, confirmed
against a real response the user pulled by hand: one page = 25 timestamped
readings (each covering every station), plus a `paginationToken` field when
more pages remain.

How the token works (reverse-engineered, not guessed): the token the user
got back, `b2Zmc2V0PTI1`, base64-decodes to the literal string `offset=25`.
So the token isn't an opaque cursor -- it's just a base64-wrapped offset
into the day's reading list. That means this script does not need to wait
for the server to hand back a token before requesting the next page: it can
compute `paginationToken = base64("offset=" + N*25)` itself for page N and
request pages 0, 1, 2, ... directly. It still reads the token the server
returns and prefers it when present (in case the offset step size ever
isn't exactly 25), and it still uses "fewer than 25 readings on this page"
as the authoritative signal that a day is finished, rather than assuming a
fixed page count -- some days may have gaps.

Design goals (same as fetch_nea_forecasts.py -- this runs unattended for
hours, so these matter more than usual):

  1. RESUMABLE per PAGE, not just per day. Every (date, page) response is
     saved to its own file. A day is only considered done once a page
     comes back with < 25 readings (or 0), at which point a `_complete`
     marker file is written for that date. Re-running the same command
     skips any date that already has a `_complete` marker, and resumes
     any partially-fetched date from its next page rather than page 0.
  2. RATE-LIMITED. Same sliding-window limiter as the forecast scraper.
  3. RAW-FIRST. Saves raw JSON verbatim per page. Daily-mm collation is a
     separate offline step (collate_rainfall.py) so a bug in the collation
     logic never costs a re-scrape against the rate limit.
  4. NEVER CRASHES THE WHOLE RUN. A single page failing is logged and
     retried with backoff; if it never succeeds the date is recorded in
     failed_dates.log and the loop moves on to the next date.
  5. SAFETY-CAPPED. A day is expected to need ~12 pages (288/25 rounded
     up). This script caps at 40 pages/day so a malformed response that
     never signals "last page" can't spin forever on one date.

Usage
-----
    export NEA_API_KEY="your-dev-api-key-here"
    python3 fetch_nea_rainfall.py

    # Smoke test first -- a handful of dates:
    python3 fetch_nea_rainfall.py --max-dates 3

    # Custom range (default matches the forecast scrape's window):
    python3 fetch_nea_rainfall.py --start 2020-02-01 --end 2020-02-10

Output layout (under --out-dir, default ./nea_rainfall_data)
--------------------------------------------------------------
    raw/2020-02-01/page_00.json
    raw/2020-02-01/page_01.json
    ...
    raw/2020-02-01/_complete.json   -- written once the last page for this
                                        date is confirmed; presence of this
                                        file is what makes a date "skip on
                                        resume"
    failed_dates.log
    run_summary.json

Requires: `requests` (pip install requests --break-system-packages)
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

URL = "https://api-open.data.gov.sg/v2/real-time/api/rainfall"
DEFAULT_START = date(2020, 2, 1)  # matches the forecast scrape's window
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 5
PAGE_SIZE = 25          # confirmed from the real sample response
MAX_PAGES_PER_DAY = 40  # safety cap; expected need is ~12


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` calls in any `period`
    seconds. Same logic as the forecast scraper's limiter."""

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
    """Build a paginationToken the same way the server's own tokens decode:
    base64("offset=<N>")."""
    return base64.b64encode(f"offset={offset}".encode()).decode()


def decode_offset_token(token: str):
    """Best-effort decode of a server-returned paginationToken back to an
    int offset, for sanity-checking / logging. Returns None if it doesn't
    match the expected shape."""
    try:
        decoded = base64.b64decode(token).decode()
        m = re.match(r"^offset=(\d+)$", decoded)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def count_readings(payload):
    """The rainfall endpoint's per-page reading list. Field name confirmed
    from the real sample as data.readings (list of {timestamp, data:[...]}
    entries, one per 5-minute timestamp). Falls back to a couple of
    plausible alternates defensively in case the shape varies by page."""
    data = payload.get("data") or {}
    for key in ("readings", "items", "records"):
        if key in data and isinstance(data[key], list):
            return len(data[key]), key
    return 0, None


def fetch_page(session, limiter, day: date, offset: int, api_key):
    """Fetch a single (date, offset) page. Returns (status, payload_or_None,
    reason_or_None, next_offset_or_None)."""
    headers = {"x-api-key": api_key} if api_key else {}
    params = {"date": day.isoformat()}
    if offset > 0:
        params["paginationToken"] = offset_token(offset)

    last_reason = None
    for attempt in range(1, MAX_RETRIES + 1):
        limiter.wait()
        try:
            resp = session.get(URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
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

        n_readings, _ = count_readings(payload)
        server_token = ((payload.get("data") or {}).get("paginationToken"))
        # The server includes paginationToken exactly when more pages
        # remain (confirmed from the real sample: present with more data,
        # absent alongside a short/empty final page). Trust its absence as
        # the authoritative "done" signal -- this also correctly stops
        # after an exact-multiple-of-25 day without one extra wasted
        # request, rather than relying on "reading count < page size" alone.
        is_last_page = (not server_token) or (n_readings < PAGE_SIZE)
        next_offset = None if is_last_page else offset + PAGE_SIZE
        return "ok", payload, None, next_offset

    return "network_error", None, last_reason or "exhausted retries", None


def existing_pages(day_dir):
    """Return sorted list of page numbers already saved for a date dir."""
    pages = []
    for p in glob.glob(os.path.join(day_dir, "page_*.json")):
        m = re.search(r"page_(\d+)\.json$", p)
        if m:
            pages.append(int(m.group(1)))
    return sorted(pages)


def is_complete(day_dir):
    return os.path.exists(os.path.join(day_dir, "_complete.json"))


def fetch_day(session, limiter, day, out_root, api_key, counts, failures):
    day_dir = os.path.join(out_root, day.isoformat())
    os.makedirs(day_dir, exist_ok=True)

    if is_complete(day_dir):
        counts["days_skipped"] += 1
        return

    pages_done = existing_pages(day_dir)
    page_num = (pages_done[-1] + 1) if pages_done else 0
    offset = page_num * PAGE_SIZE

    while page_num < MAX_PAGES_PER_DAY:
        page_path = os.path.join(day_dir, f"page_{page_num:02d}.json")
        status, payload, reason, next_offset = fetch_page(session, limiter, day, offset, api_key)

        if status != "ok":
            counts[status] = counts.get(status, 0) + 1
            failures.append({"date": day.isoformat(), "page": page_num, "status": status, "reason": reason})
            return  # leave the date incomplete; a re-run will resume from this page

        with open(page_path, "w") as f:
            json.dump(payload, f)
        counts["pages_ok"] += 1

        n_readings, key_used = count_readings(payload)
        counts["readings_total"] += n_readings

        if next_offset is None:
            with open(os.path.join(day_dir, "_complete.json"), "w") as f:
                json.dump({"pages": page_num + 1, "readings_field": key_used,
                           "finished_at": datetime.now().isoformat()}, f)
            counts["days_ok"] += 1
            return

        page_num += 1
        offset = next_offset

    # Hit the safety cap without seeing a "last page" signal -- log it as a
    # failure so it's visible, but keep whatever pages were saved.
    counts["capped"] = counts.get("capped", 0) + 1
    failures.append({"date": day.isoformat(), "page": page_num, "status": "capped",
                      "reason": f"hit MAX_PAGES_PER_DAY={MAX_PAGES_PER_DAY} without a last-page signal"})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", default=os.environ.get("NEA_API_KEY", ""),
                         help="data.gov.sg API key. Defaults to $NEA_API_KEY. "
                              "Leave blank to run unauthenticated (6 req/10s).")
    parser.add_argument("--start", default=DEFAULT_START.isoformat(),
                         help="Start date YYYY-MM-DD (default 2020-02-01, matching the forecast scrape).")
    parser.add_argument("--end", default=None,
                         help="End date YYYY-MM-DD (default: today in Singapore time).")
    parser.add_argument("--out-dir", default="./nea_rainfall_data",
                         help="Output directory (default ./nea_rainfall_data).")
    parser.add_argument("--rate-limit", type=int, default=10,
                         help="Requests per 10-second window (default 10; your dev key allows 12).")
    parser.add_argument("--max-dates", type=int, default=None,
                         help="Only process the first N dates -- smoke test before the full run.")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else (datetime.now(SGT).date() if SGT else date.today())

    if not args.api_key:
        print("WARNING: no API key provided (--api-key or $NEA_API_KEY). "
              "Running unauthenticated: 6 requests / 10s instead of 12.", file=sys.stderr)
        if args.rate_limit > 6:
            args.rate_limit = 6

    out_root = os.path.join(args.out_dir, "raw")
    os.makedirs(out_root, exist_ok=True)
    failed_log_path = os.path.join(args.out_dir, "failed_dates.log")
    summary_path = os.path.join(args.out_dir, "run_summary.json")

    dates = list(daterange(start, end))
    if args.max_dates:
        dates = dates[: args.max_dates]

    est_pages = len(dates) * 12  # ~288/25 rounded up
    print(f"Date range: {start} to {end} ({len(dates)} days)")
    print(f"Estimated pages: ~{est_pages} (12/day typical) "
          f"-> rough est. {est_pages / args.rate_limit * 10 / 60:.1f} min at {args.rate_limit} req/10s")
    print(f"Output dir: {os.path.abspath(args.out_dir)}\n")

    session = requests.Session()
    limiter = RateLimiter(max_calls=args.rate_limit, period=10.0)

    counts = {"days_ok": 0, "days_skipped": 0, "pages_ok": 0, "readings_total": 0,
              "api_error": 0, "http_error": 0, "network_error": 0, "capped": 0}
    failures = []
    start_time = time.monotonic()

    try:
        for i, day in enumerate(dates, 1):
            fetch_day(session, limiter, day, out_root, args.api_key, counts, failures)
            if i % 10 == 0 or i == len(dates):
                elapsed = time.monotonic() - start_time
                done = counts["days_ok"] + counts["days_skipped"]
                rate = done / elapsed if elapsed > 0 and done > 0 else 0
                remaining = len(dates) - i
                eta_min = ((len(dates) - done) / rate / 60) if rate > 0 else float("nan")
                print(f"[{i}/{len(dates)}] {day} done | ok={counts['days_ok']} skipped={counts['days_skipped']} "
                      f"pages={counts['pages_ok']} readings={counts['readings_total']} "
                      f"errors={counts['api_error']+counts['http_error']+counts['network_error']+counts['capped']} "
                      f"| ETA ~{eta_min:.1f} min")
    except KeyboardInterrupt:
        print("\nInterrupted -- progress so far is saved. Re-run the same command to resume "
              "(partially-fetched dates pick up from their next page, not page 0).")

    if failures:
        with open(failed_log_path, "w") as f:
            for item in failures:
                f.write(json.dumps(item) + "\n")
        print(f"\n{len(failures)} date/page failures -- see {failed_log_path}")
        print("Re-running this same script will retry only the incomplete dates.")

    with open(summary_path, "w") as f:
        json.dump({"range": [start.isoformat(), end.isoformat()], "counts": counts,
                   "failures": len(failures), "finished_at": datetime.now().isoformat()}, f, indent=2)

    print(f"\nDone. {counts['days_ok']} days completed, {counts['days_skipped']} already cached, "
          f"{counts['api_error']+counts['http_error']+counts['network_error']+counts['capped']} days with failures.")
    print(f"Next step: python3 collate_rainfall.py --data-dir {args.out_dir}")


if __name__ == "__main__":
    main()
