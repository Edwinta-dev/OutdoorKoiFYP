"""Local development stack: the API, the worker and the camera service on
seeded demo data, with no live service, phone or hardware (issue #14).

    python -m koi.dev                      # serve on :8080 (API) and :5000 (camera)
    python -m koi.dev --check              # start, check the API against the OpenAPI document, exit
    python -m koi.dev --profile supabase   # the same seed in the local Supabase stack

How to run it and point the app at it: docs/dev.md. The seed is
Backend/fixtures/demo_pond/seed.json (built by build_seed.py beside it);
koi/dev/seed.py moves it to the current time and koi/dev/replay.py runs
the worker over its history before the services start.
"""
