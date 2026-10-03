"""NEA weather ingestion and weather history reads (issue #24).

    nea.py      client (retry, backoff, rate limits, pagination) and parsers
    cache.py    latest-cache rows in the dashboard's exact contract, and
                the guards that keep older values out
    history.py  as-of selection, rainfall totals with coverage, UV status
    job.py      live cycle and resumable backfill
    python -m koi.weather live|backfill   the command line

Schema, keys and rules: supabase/migrations/0009_weather_history.sql.
Endpoint assumptions and the owner's switch-over steps:
docs/weather-ingestion.md.
"""
