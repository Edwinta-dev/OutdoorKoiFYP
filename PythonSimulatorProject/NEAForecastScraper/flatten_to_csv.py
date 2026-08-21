#!/usr/bin/env python3
"""
flatten_to_csv.py
==================

Offline, re-runnable companion to fetch_nea_forecasts.py. Reads every raw
JSON file fetch_nea_forecasts.py saved and flattens it into analysis-ready
CSVs. Does NOT touch the network -- safe to run as many times as you like
while the scrape is still in progress, or after it finishes.

Produces three CSVs in --data-dir:

  twenty_four_hr_general.csv
      One row per forecast ISSUANCE (a queried date can have several --
      NEA re-issues the 24hr forecast multiple times a day). Columns:
      query_date, issued_at, valid_from, valid_to, temp_low, temp_high,
      humidity_low, humidity_high, wind_speed_low, wind_speed_high,
      wind_direction, forecast_code, forecast_text.
      This is the general island-wide summary -- the simplest one to
      feed into the pond simulator as a same-day "rain expected" signal.

  twenty_four_hr_periods.csv
      One row per (issuance, 6-hour period, region) -- the granular
      region-level breakdown (west/east/central/south/north), in case you
      want to match a specific region to where the pond actually is
      (the NEA station used in Phase 1, Admiralty, sits in the north).

  four_day_outlook.csv
      One row per (issuance, forecast day 1-4). Columns: query_date,
      issued_at, forecast_for_date, day_name, temp_low, temp_high,
      humidity_low, humidity_high, wind_speed_low, wind_speed_high,
      wind_direction, forecast_code, forecast_text, forecast_summary,
      lead_days (1-4, i.e. how many days ahead of forecast_for_date this
      issuance was made -- this is the field the forecast-phase
      simulator arm will actually key off of).

Usage:
    python3 flatten_to_csv.py --data-dir ./nea_forecast_data
"""

import argparse
import csv
import glob
import json
import os
from datetime import datetime


def parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s)


def load_json_files(pattern):
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                yield path, json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ! skipping unreadable file {path}: {e}")


def flatten_twenty_four_hr(data_dir):
    general_rows = []
    period_rows = []
    pattern = os.path.join(data_dir, "raw", "twenty_four_hr", "*.json")
    n_files = 0
    for path, payload in load_json_files(pattern):
        n_files += 1
        records = ((payload.get("data") or {}).get("records")) or []
        query_date = os.path.splitext(os.path.basename(path))[0]
        for rec in records:
            issued_at = rec.get("timestamp")
            general = rec.get("general") or {}
            temp = general.get("temperature") or {}
            hum = general.get("relativeHumidity") or {}
            wind = general.get("wind") or {}
            wind_speed = wind.get("speed") or {}
            forecast = general.get("forecast") or {}
            valid = general.get("validPeriod") or {}

            general_rows.append({
                "query_date": query_date,
                "issued_at": issued_at,
                "valid_from": valid.get("start"),
                "valid_to": valid.get("end"),
                "temp_low": temp.get("low"),
                "temp_high": temp.get("high"),
                "humidity_low": hum.get("low"),
                "humidity_high": hum.get("high"),
                "wind_speed_low": wind_speed.get("low"),
                "wind_speed_high": wind_speed.get("high"),
                "wind_direction": wind.get("direction"),
                "forecast_code": forecast.get("code"),
                "forecast_text": forecast.get("text"),
            })

            for period in (rec.get("periods") or []):
                tp = period.get("timePeriod") or {}
                regions = period.get("regions") or {}
                for region_name, region_val in regions.items():
                    period_rows.append({
                        "query_date": query_date,
                        "issued_at": issued_at,
                        "period_start": tp.get("start"),
                        "period_end": tp.get("end"),
                        "region": region_name,
                        "forecast_code": (region_val or {}).get("code"),
                        "forecast_text": (region_val or {}).get("text"),
                    })
    print(f"  twenty_four_hr: read {n_files} files -> {len(general_rows)} issuances, {len(period_rows)} region-periods")
    return general_rows, period_rows


def flatten_four_day(data_dir):
    rows = []
    pattern = os.path.join(data_dir, "raw", "four_day", "*.json")
    n_files = 0
    for path, payload in load_json_files(pattern):
        n_files += 1
        records = ((payload.get("data") or {}).get("records")) or []
        query_date = os.path.splitext(os.path.basename(path))[0]
        for rec in records:
            issued_at = rec.get("timestamp")
            issued_dt = parse_dt(issued_at)
            for forecast_entry in (rec.get("forecasts") or []):
                temp = forecast_entry.get("temperature") or {}
                hum = forecast_entry.get("relativeHumidity") or {}
                wind = forecast_entry.get("wind") or {}
                wind_speed = wind.get("speed") or {}
                forecast = forecast_entry.get("forecast") or {}
                for_ts = forecast_entry.get("timestamp")
                for_dt = parse_dt(for_ts)

                lead_days = None
                if issued_dt and for_dt:
                    lead_days = (for_dt.date() - issued_dt.date()).days

                rows.append({
                    "query_date": query_date,
                    "issued_at": issued_at,
                    "forecast_for_date": for_dt.date().isoformat() if for_dt else None,
                    "day_name": forecast_entry.get("day"),
                    "lead_days": lead_days,
                    "temp_low": temp.get("low"),
                    "temp_high": temp.get("high"),
                    "humidity_low": hum.get("low"),
                    "humidity_high": hum.get("high"),
                    "wind_speed_low": wind_speed.get("low"),
                    "wind_speed_high": wind_speed.get("high"),
                    "wind_direction": wind.get("direction"),
                    "forecast_code": forecast.get("code"),
                    "forecast_text": forecast.get("text"),
                    "forecast_summary": forecast.get("summary"),
                })
    print(f"  four_day: read {n_files} files -> {len(rows)} (issuance x forecast-day) rows")
    return rows


def write_csv(path, rows):
    if not rows:
        print(f"  (no rows for {path}, skipping)")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="./nea_forecast_data",
                         help="Directory passed as --out-dir to fetch_nea_forecasts.py.")
    args = parser.parse_args()

    print("Flattening 24-hour forecast files...")
    general_rows, period_rows = flatten_twenty_four_hr(args.data_dir)
    write_csv(os.path.join(args.data_dir, "twenty_four_hr_general.csv"), general_rows)
    write_csv(os.path.join(args.data_dir, "twenty_four_hr_periods.csv"), period_rows)

    print("Flattening 4-day outlook files...")
    four_day_rows = flatten_four_day(args.data_dir)
    write_csv(os.path.join(args.data_dir, "four_day_outlook.csv"), four_day_rows)

    print("\nDone.")


if __name__ == "__main__":
    main()