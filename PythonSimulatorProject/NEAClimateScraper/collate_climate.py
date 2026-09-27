#!/usr/bin/env python3
"""
collate_climate.py
====================

Offline, re-runnable companion to fetch_nea_climate.py. Reads every raw page
JSON the scraper saved and flattens it into analysis-ready CSVs. Does NOT
touch the network -- safe to run repeatedly while the scrape is still going,
or after it finishes.

Produces, in --csv-out-dir (defaults to --data-dir if not given -- pass a
different one to, for example, write straight into NEA_Data_Analysis/):

  daily_climate.csv
      One row per calendar date: t_mean, t_max, t_min, rh_mean, rh_min,
      n_samples_t, n_samples_rh, stations_used, source. Same column layout
      as the notebook's existing daily_climate.csv, but with a documented
      aggregation rule (the old file's generating script isn't in the repo,
      so its exact convention is unverifiable):

        1. Every station's 1-minute reading is placed on the calendar date
           of ITS OWN timestamp (Singapore time), not the query-date folder
           it was fetched under -- same reasoning as collate_rainfall.py's
           midnight handling.
        2. At each minute, average across every reporting station (a
           network-wide mean -- there's no Woodlands-specific temperature
           station the way there is for rainfall; only S104 of the 4-
           station rain cluster exists in this ~12-station network at all).
        3. t_mean/t_max/t_min are the mean/max/min of that per-minute
           network-mean series across the day (i.e. spatial average first,
           then the daily statistic -- consistent with how the notebook
           handles hourly_rain). rh_mean/rh_min are the analogous humidity
           stats (no rh_max: only min is used downstream, per the existing
           schema).
        4. n_samples_t / n_samples_rh = number of distinct minute-timestamps
           that had at least one reporting station that day (NOT scaled by
           station count) -- comparable to daily_climate.csv's existing
           n_samples_t/n_samples_rh columns, which is why filtering short
           days out (see MIN_SAMPLES below) is left to you, same as the
           notebook's own MIN_HOURS_DAY pattern elsewhere.
        5. source = "network_mean(<n> stations)" -- explicit and different
           from the old file's undocumented "cluster/cluster" label, so the
           two are never silently confused.

  two_hr_forecast.csv
      One row per (issuance timestamp, area): query_date, issued_at,
      valid_from, valid_to, valid_text, area, forecast_text. This is the
      2-hour nowcast the user proposed as a closer-to-reality reference --
      filter to area == "Woodlands" for the pond's proxy location. Far more
      frequent (every ~30 min) and short-lead than the 4-day outlook, so it
      is NOT a forecast target in the pipeline's sense -- it's closer to a
      second, area-resolved observation than a prediction.

Usage:
    python3 collate_climate.py --data-dir ./nea_climate_data
    python3 collate_climate.py --data-dir ./nea_climate_data --csv-out-dir ../../NEA_Data_Analysis
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

MIN_SAMPLES_PER_DAY = 0  # informational only here; filtering is left to the notebook, same as MIN_HOURS_DAY there


def to_sgt_date(timestamp_str):
    dt = datetime.fromisoformat(timestamp_str)
    if SGT is not None:
        dt = dt.astimezone(SGT)
    return dt.date().isoformat()


def load_pages(data_dir, endpoint):
    pattern = os.path.join(data_dir, "raw", endpoint, "*", "page_*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                yield path, json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ! skipping unreadable file {path}: {e}")


def collate_station_reading_endpoint(data_dir, endpoint):
    """Shared logic for air-temperature / relative-humidity: dedup readings
    by (station, timestamp), then group by (date, minute-timestamp) to build
    a per-minute network-mean series."""
    seen = {}  # (station_id, timestamp) -> value
    for _, payload in load_pages(data_dir, endpoint):
        readings = ((payload.get("data") or {}).get("readings")) or []
        for r in readings:
            ts = r.get("timestamp")
            if not ts:
                continue
            for entry in (r.get("data") or []):
                sid = entry.get("stationId") or entry.get("station_id")
                val = entry.get("value")
                if sid is None or val is None:
                    continue
                seen[(sid, ts)] = val
    print(f"  {endpoint}: {len(seen):,} unique (station, timestamp) readings")

    # (date, timestamp) -> [sum, count] across stations, for the per-minute network mean
    per_minute = defaultdict(lambda: [0.0, 0])
    for (sid, ts), val in seen.items():
        d = to_sgt_date(ts)
        agg = per_minute[(d, ts)]
        agg[0] += val
        agg[1] += 1

    minute_means = defaultdict(list)  # date -> [network-mean values, one per minute]
    stations_seen_per_day = defaultdict(set)
    for (d, ts), (total, n) in per_minute.items():
        minute_means[d].append(total / n)
    for (sid, ts) in seen:
        stations_seen_per_day[to_sgt_date(ts)].add(sid)

    return minute_means, stations_seen_per_day


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="./nea_climate_data",
                         help="Directory passed as --out-dir to fetch_nea_climate.py.")
    parser.add_argument("--csv-out-dir", default=None,
                         help="Where to write the flattened CSVs (default: same as --data-dir). "
                              "Point this at NEA_Data_Analysis/ to feed the notebook directly.")
    args = parser.parse_args()
    csv_out_dir = args.csv_out_dir or args.data_dir
    os.makedirs(csv_out_dir, exist_ok=True)

    print("Collating air-temperature and relative-humidity...")
    t_minute_means, t_stations = collate_station_reading_endpoint(args.data_dir, "air-temperature")
    rh_minute_means, rh_stations = collate_station_reading_endpoint(args.data_dir, "relative-humidity")

    all_dates = sorted(set(t_minute_means) | set(rh_minute_means))
    rows = []
    for d in all_dates:
        t_vals = t_minute_means.get(d, [])
        rh_vals = rh_minute_means.get(d, [])
        n_t_stations = len(t_stations.get(d, set()))
        n_rh_stations = len(rh_stations.get(d, set()))
        rows.append({
            "date": d,
            "t_mean": round(sum(t_vals) / len(t_vals), 2) if t_vals else "",
            "t_max": round(max(t_vals), 2) if t_vals else "",
            "t_min": round(min(t_vals), 2) if t_vals else "",
            "rh_mean": round(sum(rh_vals) / len(rh_vals), 2) if rh_vals else "",
            "rh_min": round(min(rh_vals), 2) if rh_vals else "",
            "n_samples_t": len(t_vals),
            "n_samples_rh": len(rh_vals),
            "stations_used": f"{n_t_stations}/{n_rh_stations}",
            "source": f"network_mean({max(n_t_stations, n_rh_stations)} stations)",
        })

    daily_path = os.path.join(csv_out_dir, "daily_climate.csv")
    with open(daily_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "t_mean", "t_max", "t_min", "rh_mean", "rh_min",
                                                 "n_samples_t", "n_samples_rh", "stations_used", "source"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {daily_path} ({len(rows)} days)")

    print("\nCollating two-hr-forecast...")
    two_hr_rows = []
    n_files = 0
    for path, payload in load_pages(args.data_dir, "two-hr-forecast"):
        n_files += 1
        query_date = os.path.basename(os.path.dirname(path))
        items = ((payload.get("data") or {}).get("items")) or []
        for item in items:
            valid = item.get("valid_period") or {}
            for fc in (item.get("forecasts") or []):
                two_hr_rows.append({
                    "query_date": query_date,
                    "issued_at": item.get("timestamp"),
                    "update_timestamp": item.get("update_timestamp"),
                    "valid_from": valid.get("start"),
                    "valid_to": valid.get("end"),
                    "valid_text": valid.get("text"),
                    "area": fc.get("area"),
                    "forecast_text": fc.get("forecast"),
                })
    two_hr_path = os.path.join(csv_out_dir, "two_hr_forecast.csv")
    if two_hr_rows:
        with open(two_hr_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["query_date", "issued_at", "update_timestamp",
                                                     "valid_from", "valid_to", "valid_text", "area", "forecast_text"])
            writer.writeheader()
            writer.writerows(two_hr_rows)
        print(f"  read {n_files} files -> wrote {two_hr_path} ({len(two_hr_rows):,} (issuance x area) rows)")
    else:
        print(f"  (no two-hr-forecast pages found under {args.data_dir}, skipping)")

    print("\nDone.")


if __name__ == "__main__":
    main()
