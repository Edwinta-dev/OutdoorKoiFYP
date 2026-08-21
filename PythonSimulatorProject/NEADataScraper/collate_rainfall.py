#!/usr/bin/env python3
"""
collate_rainfall.py
====================

Offline, re-runnable companion to fetch_nea_rainfall.py. Reads every raw
page JSON the scraper saved and compiles the daily-mm-per-station dataset
by hand (data.gov.sg has no bulk "daily total" endpoint for the real-time
network -- Phase 1's downloadable historical CSV is a different, older
dataset that stops in 2017, which is the whole reason this scraper exists).

Fidelity rules (agreed with the user before building this):

  1. Each reading is a 5-minute-window total (mm), not cumulative -- a
     daily total is the SUM of that day's readings per station, not the
     last value or an average.
  2. A day's calendar attribution comes from each reading's OWN timestamp
     (converted to Singapore time), not from the query-date folder it was
     fetched under. Near-midnight pages can legitimately contain a
     23:55-ish reading that belongs to the next calendar day or a
     00:00-ish reading that belongs to the previous one -- trusting the
     folder name instead of the timestamp would misattribute those.
  3. Readings are de-duplicated by (station, timestamp) before summing, in
     case adjacent pages ever overlap.
  4. Missing slots are NOT treated as zero rainfall. Every output row
     carries slots_present alongside slots_expected (288/day) and a
     completeness_pct, so "0.0mm, fully sampled" and "0.0mm, half the day
     missing" stay distinguishable downstream -- the same principle
     Phase 1's UI already applies to its own gap-day disclosure.

Produces, in --data-dir:

  stations.csv
      One row per station: station_id, name, latitude, longitude. Pulled
      from whichever raw pages mention each station (station metadata is
      repeated on every page, so this is just a dedup).

  daily_rainfall_mm.csv
      One row per (date, station_id): total_mm, slots_present,
      slots_expected (288), completeness_pct. This is the ground-truth
      table Phase 2's confidence-by-lead-time calibration needs -- filter
      to whichever station(s) you decide to use as the pond's proxy and
      join against the forecast CSVs on date.

Usage:
    python3 collate_rainfall.py --data-dir ./nea_rainfall_data
"""

import argparse
import csv
import glob
import json
import os
from collections import defaultdict
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:
    SGT = None

EXPECTED_SLOTS_PER_DAY = 288  # 24h * 60 / 5min


def to_sgt_date(timestamp_str):
    """Parse an ISO timestamp (carries its own UTC+8 offset in the real
    responses) and return the Singapore calendar date it falls on."""
    dt = datetime.fromisoformat(timestamp_str)
    if SGT is not None:
        dt = dt.astimezone(SGT)
    return dt.date().isoformat()


def load_pages(data_dir):
    pattern = os.path.join(data_dir, "raw", "*", "page_*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                yield path, json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ! skipping unreadable file {path}: {e}")


def extract_readings(payload):
    """Locate the per-timestamp reading list defensively (see
    fetch_nea_rainfall.py's count_readings for why: field name confirmed
    as 'readings' from the sample response, with a couple of fallbacks in
    case a page's shape ever differs)."""
    data = payload.get("data") or {}
    for key in ("readings", "items", "records"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    return []


def extract_stations(payload):
    data = payload.get("data") or {}
    return data.get("stations") or []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=r"\OutdoorKoiFYP\NEADataScraper\nea_rainfall_data",
                         help="Directory passed as --out-dir to fetch_nea_rainfall.py.")
    args = parser.parse_args()

    stations = {}  # station_id -> {name, latitude, longitude}
    # (station_id, timestamp) -> value_mm, for de-duplication before summing
    seen_readings = {}
    unknown_shape_pages = []

    n_pages = 0
    for path, payload in load_pages(args.data_dir):
        n_pages += 1

        for st in extract_stations(payload):
            sid = st.get("id") or st.get("deviceId")
            if sid and sid not in stations:
                loc = st.get("location") or {}
                stations[sid] = {
                    "station_id": sid,
                    "name": st.get("name"),
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                }

        readings = extract_readings(payload)
        if not readings and (payload.get("data") or {}).get("stations"):
            # Stations present but no readings at all on a non-empty page
            # is unexpected -- flag it rather than silently treating as ok.
            unknown_shape_pages.append(path)

        for r in readings:
            ts = r.get("timestamp")
            if not ts:
                continue
            for entry in (r.get("data") or []):
                sid = entry.get("stationId") or entry.get("station_id")
                val = entry.get("value")
                if sid is None or val is None:
                    continue
                seen_readings[(sid, ts)] = val

    print(f"Read {n_pages} page files -> {len(stations)} stations, {len(seen_readings)} unique (station, timestamp) readings")
    if unknown_shape_pages:
        print(f"  ! {len(unknown_shape_pages)} pages had stations but no recognized reading list -- "
              f"check field names, e.g. {unknown_shape_pages[0]}")

    # Aggregate to (calendar_date, station_id) -> sum(mm), count(slots)
    daily = defaultdict(lambda: [0.0, 0])  # [total_mm, slots_present]
    for (sid, ts), val in seen_readings.items():
        d = to_sgt_date(ts)
        agg = daily[(d, sid)]
        agg[0] += val
        agg[1] += 1

    # stations.csv
    stations_path = os.path.join(args.data_dir, "stations.csv")
    with open(stations_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["station_id", "name", "latitude", "longitude"])
        writer.writeheader()
        for sid in sorted(stations):
            writer.writerow(stations[sid])
    print(f"wrote {stations_path} ({len(stations)} stations)")

    # daily_rainfall_mm.csv
    daily_path = os.path.join(args.data_dir, "daily_rainfall_mm.csv")
    rows = []
    for (d, sid), (total_mm, slots_present) in sorted(daily.items()):
        rows.append({
            "date": d,
            "station_id": sid,
            "station_name": stations.get(sid, {}).get("name"),
            "total_mm": round(total_mm, 2),
            "slots_present": slots_present,
            "slots_expected": EXPECTED_SLOTS_PER_DAY,
            "completeness_pct": round(100.0 * slots_present / EXPECTED_SLOTS_PER_DAY, 1),
        })
    with open(daily_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "station_id", "station_name", "total_mm",
                                                 "slots_present", "slots_expected", "completeness_pct"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {daily_path} ({len(rows)} date x station rows)")

    if rows:
        n_dates = len(set(r["date"] for r in rows))
        avg_completeness = sum(r["completeness_pct"] for r in rows) / len(rows)
        low_completeness = sum(1 for r in rows if r["completeness_pct"] < 90.0)
        print(f"\n{n_dates} distinct calendar dates covered, {len(stations)} stations.")
        print(f"Average completeness: {avg_completeness:.1f}%. "
              f"{low_completeness} (date, station) rows below 90% completeness -- "
              f"treat those as partial-day data, not missing-equals-zero.")

    print("\nDone.")


if __name__ == "__main__":
    main()
