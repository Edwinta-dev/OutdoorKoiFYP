#!/usr/bin/env python3
"""
Mocked end-to-end test for fetch_nea_rainfall.py + collate_rainfall.py.
No real network access -- patches requests.Session.get with a fake that
mimics the real API shape (25 readings/page, base64 "offset=N" token,
last page short) across 3 days: a normal full day, a day that gets
interrupted mid-fetch and resumed, and a day with a 429 that recovers.
"""
import base64
import json
import os
import shutil
import sys
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import fetch_nea_rainfall as scraper

TEST_DIR = "/tmp/koi_sim/nea_rainfall_scraper/_test_output"

STATIONS = [
    {"id": "S60", "deviceId": "S60", "name": "Alexandra Road", "location": {"latitude": 1.2937, "longitude": 103.8125}},
    {"id": "S100", "deviceId": "S100", "name": "Woodlands Road", "location": {"latitude": 1.4374, "longitude": 103.7864}},
]

TOTAL_SLOTS = 288  # a "full" day


def make_page(day_iso, offset, page_size, total_slots, fail_once_key=None, state=None):
    n = min(page_size, max(0, total_slots - offset))
    readings = []
    for i in range(n):
        slot = offset + i
        hh = (slot * 5) // 60
        mm = (slot * 5) % 60
        ts = f"{day_iso}T{hh:02d}:{mm:02d}:00+08:00"
        readings.append({
            "timestamp": ts,
            "data": [{"stationId": s["id"], "value": 0.5 if s["id"] == "S60" else 0.0} for s in STATIONS],
        })
    next_offset = offset + n
    token = scraper.offset_token(next_offset) if next_offset < total_slots else None
    payload = {
        "code": 0,
        "data": {
            "stations": STATIONS,
            "readings": readings,
            "readingType": "TB1 Rainfall 5 Minute Total F",
            "readingUnit": "mm",
            "paginationToken": token,
        },
        "errorMsg": "",
    }
    return payload


class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def build_fake_get(day_slot_counts, flaky_dates=None, call_log=None):
    """day_slot_counts: dict date_iso -> total_slots (simulate short/incomplete days).
    flaky_dates: set of date_iso that should 429 once before succeeding."""
    flaky_dates = flaky_dates or set()
    flaky_fired = set()

    def fake_get(url, params=None, headers=None, timeout=None):
        if call_log is not None:
            call_log.append(dict(params))
        day_iso = params["date"]
        total_slots = day_slot_counts.get(day_iso, TOTAL_SLOTS)

        if day_iso in flaky_dates and day_iso not in flaky_fired:
            flaky_fired.add(day_iso)
            return FakeResp(429, {})

        token = params.get("paginationToken")
        offset = scraper.decode_offset_token(token) if token else 0
        payload = make_page(day_iso, offset, scraper.PAGE_SIZE, total_slots)
        return FakeResp(200, payload)

    return fake_get


def reset_test_dir():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)


def run_scrape(dates, day_slot_counts, flaky_dates=None):
    out_root = os.path.join(TEST_DIR, "raw")
    os.makedirs(out_root, exist_ok=True)
    session = mock.MagicMock()
    call_log = []
    session.get = build_fake_get(day_slot_counts, flaky_dates, call_log)
    limiter = scraper.RateLimiter(max_calls=1000, period=10.0)  # don't actually sleep in the test
    counts = {"days_ok": 0, "days_skipped": 0, "pages_ok": 0, "readings_total": 0,
              "api_error": 0, "http_error": 0, "network_error": 0, "capped": 0}
    failures = []
    for d in dates:
        scraper.fetch_day(session, limiter, date.fromisoformat(d), out_root, "fake-key", counts, failures)
    return counts, failures, call_log


def test_full_day_and_short_day():
    reset_test_dir()
    dates = ["2020-02-01", "2020-02-02"]
    # day 1: full 288 slots (12 pages, last page has 288-11*25=13 readings)
    # day 2: short day, only 100 slots available (simulates a day with gaps) -> 4 pages
    counts, failures, _ = run_scrape(dates, {"2020-02-01": 288, "2020-02-02": 100})
    assert counts["days_ok"] == 2, counts
    assert failures == [], failures
    assert os.path.exists(os.path.join(TEST_DIR, "raw", "2020-02-01", "_complete.json"))
    assert os.path.exists(os.path.join(TEST_DIR, "raw", "2020-02-02", "_complete.json"))
    n_pages_day1 = len([p for p in os.listdir(os.path.join(TEST_DIR, "raw", "2020-02-01")) if p.startswith("page_")])
    n_pages_day2 = len([p for p in os.listdir(os.path.join(TEST_DIR, "raw", "2020-02-02")) if p.startswith("page_")])
    assert n_pages_day1 == 12, n_pages_day1  # ceil(288/25), last page short (13 readings) signals done
    assert n_pages_day2 == 4, n_pages_day2   # ceil(100/25) exactly -> last page's absent token signals done
    print("test_full_day_and_short_day: OK", counts)


def test_resume_mid_day():
    reset_test_dir()
    out_root = os.path.join(TEST_DIR, "raw")
    os.makedirs(out_root, exist_ok=True)
    day_iso = "2020-02-01"

    # Manually pre-seed pages 0 and 1 as if a previous run got partway through.
    day_dir = os.path.join(out_root, day_iso)
    os.makedirs(day_dir, exist_ok=True)
    for offset in (0, 25):
        payload = make_page(day_iso, offset, scraper.PAGE_SIZE, 288)
        page_num = offset // 25
        with open(os.path.join(day_dir, f"page_{page_num:02d}.json"), "w") as f:
            json.dump(payload, f)

    session = mock.MagicMock()
    call_log = []
    session.get = build_fake_get({day_iso: 288}, call_log=call_log)
    limiter = scraper.RateLimiter(max_calls=1000, period=10.0)
    counts = {"days_ok": 0, "days_skipped": 0, "pages_ok": 0, "readings_total": 0,
              "api_error": 0, "http_error": 0, "network_error": 0, "capped": 0}
    failures = []
    scraper.fetch_day(session, limiter, date.fromisoformat(day_iso), out_root, "fake-key", counts, failures)

    assert counts["days_ok"] == 1, counts
    # Only pages 2..11 (10 pages) should have been fetched over the network -- 0 and 1 were cached.
    assert counts["pages_ok"] == 10, counts
    first_call_offset = scraper.decode_offset_token(call_log[0].get("paginationToken")) if call_log[0].get("paginationToken") else 0
    assert first_call_offset == 50, f"resume should start at offset 50 (page 2), got {first_call_offset}"
    print("test_resume_mid_day: OK", counts, "first fetched offset:", first_call_offset)


def test_flaky_429_recovers():
    reset_test_dir()
    with mock.patch("time.sleep", return_value=None):  # don't actually wait 10.5s in the test
        counts, failures, _ = run_scrape(["2020-02-01"], {"2020-02-01": 50}, flaky_dates={"2020-02-01"})
    assert counts["days_ok"] == 1, counts
    assert failures == [], failures
    print("test_flaky_429_recovers: OK", counts)


def test_second_run_skips_completed_day():
    reset_test_dir()
    dates = ["2020-02-01"]
    counts1, failures1, call_log1 = run_scrape(dates, {"2020-02-01": 50})
    assert counts1["days_ok"] == 1
    # Re-run against the same output dir -- should skip entirely, zero network calls.
    out_root = os.path.join(TEST_DIR, "raw")
    session = mock.MagicMock()
    call_log2 = []
    session.get = build_fake_get({"2020-02-01": 50}, call_log=call_log2)
    limiter = scraper.RateLimiter(max_calls=1000, period=10.0)
    counts2 = {"days_ok": 0, "days_skipped": 0, "pages_ok": 0, "readings_total": 0,
               "api_error": 0, "http_error": 0, "network_error": 0, "capped": 0}
    failures2 = []
    scraper.fetch_day(session, limiter, date.fromisoformat("2020-02-01"), out_root, "fake-key", counts2, failures2)
    assert counts2["days_skipped"] == 1
    assert len(call_log2) == 0, "resume-complete day should make zero network calls"
    print("test_second_run_skips_completed_day: OK")


def test_collate_correctness():
    reset_test_dir()
    dates = ["2020-02-01", "2020-02-02"]
    run_scrape(dates, {"2020-02-01": 288, "2020-02-02": 100})

    import collate_rainfall
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sys.argv = ["collate_rainfall.py", "--data-dir", TEST_DIR]
        collate_rainfall.main()

    import csv as csvmod
    with open(os.path.join(TEST_DIR, "daily_rainfall_mm.csv")) as f:
        rows = list(csvmod.DictReader(f))

    by_key = {(r["date"], r["station_id"]): r for r in rows}

    # S60 gets 0.5mm per 5-min slot in the fake data.
    d1_s60 = by_key[("2020-02-01", "S60")]
    assert d1_s60["slots_present"] == "288", d1_s60
    assert d1_s60["completeness_pct"] == "100.0", d1_s60
    assert abs(float(d1_s60["total_mm"]) - 288 * 0.5) < 0.01, d1_s60

    d2_s60 = by_key[("2020-02-02", "S60")]
    assert d2_s60["slots_present"] == "100", d2_s60
    assert d2_s60["completeness_pct"] == "34.7", d2_s60  # 100/288
    assert abs(float(d2_s60["total_mm"]) - 100 * 0.5) < 0.01, d2_s60

    # S100 is always 0.0mm/slot in the fake data -- should still show full completeness, not be dropped.
    d1_s100 = by_key[("2020-02-01", "S100")]
    assert d1_s100["total_mm"] == "0.0", d1_s100
    assert d1_s100["slots_present"] == "288", d1_s100

    with open(os.path.join(TEST_DIR, "stations.csv")) as f:
        station_rows = list(csvmod.DictReader(f))
    assert len(station_rows) == 2
    names = {r["station_id"]: r["name"] for r in station_rows}
    assert names["S60"] == "Alexandra Road"
    assert names["S100"] == "Woodlands Road"

    print("test_collate_correctness: OK ->", len(rows), "rows,", len(station_rows), "stations")


if __name__ == "__main__":
    test_full_day_and_short_day()
    test_resume_mid_day()
    test_flaky_429_recovers()
    test_second_run_skips_completed_day()
    test_collate_correctness()
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    print("\nAll mocked tests passed.")
