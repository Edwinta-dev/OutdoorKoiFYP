# Supabase schema

`migrations/` holds the database schema as numbered SQL files. They are
the only way the schema changes: a new table, column, function or bucket
goes in a new file, never in the live project alone.

| File | What it does |
|---|---|
| `0001_baseline.sql` | Every table, the two storage buckets, the `ClosestStations` trigger and the `get_bundled_dashboard_payload` / `get_historical_graph_payload` functions, reconstructed from the code |

## How 0001 was built, and what to trust

Until issue #5 no SQL was committed, so `0001_baseline.sql` was written
from how the code uses the database (`Backend/koi/storage/state_store.py`,
`Backend/koi/camera/camera.py`, `Embedded/sensor_node/sensor_node.ino` and the
Flutter app). Table and column names come straight from the code and are
reliable. Column types, indexes, the trigger and the function bodies are
reasoned from what the code reads and writes; every part that could not
be recovered is marked `-- RECONSTRUCTED: verify against live`.

Not in 0001 at all, because the code cannot show them:

- row-level security policies and grants;
- the job that fills `weather_telemetry` and `weather_forecasts` from the
  NEA APIs, and any `pg_cron` schedule (including whatever runs
  `refresh_daily_sensor_averages`).

## Comparing 0001 with the live project (owner)

Do this once, before applying any later migration to the live project.
It needs the Supabase CLI and Docker, and the live database password.

```bash
# from the repository root
supabase login
supabase link --project-ref <your-project-ref>

# 1. Dump what the live project actually has
supabase db dump --linked --schema public -f build/live_public.sql

# 2. Show what the live project has that the migrations do not
#    (starts a throwaway local database, applies migrations/, diffs)
supabase db diff --linked --schema public -f live_differences
```

The diff does not cover storage buckets. Compare them by running this in
the dashboard SQL editor:

```sql
select id, public, file_size_limit, allowed_mime_types from storage.buckets;
```

`supabase db diff` writes the SQL needed to make the migrations match the
live database into a new file under `migrations/`. Read it rather than
applying it blind:

- Where live differs from a line marked `RECONSTRUCTED`, live is right.
  Correct `0001_baseline.sql` so a fresh project matches live, and delete
  the marker for that item.
- Where live has objects 0001 does not (RLS policies, grants, extra
  columns, triggers), keep them in a new numbered migration such as
  `0002_live_policies.sql` so they are reviewable.
- Where 0001 has something live does not, either drop it from 0001 or
  keep it and apply it to live as a later migration.

Report the differences in the issue so they are on record. `build/` is
gitignored, so the dumps are not committed; do not commit them, as they
can contain data.

## Applying migrations

The live project already has the objects in 0001 (it was reconstructed
from it), so 0001 is recorded as applied rather than run:

```bash
supabase migration repair --status applied 0001
```

After that, every later migration is applied with

```bash
supabase db push --linked --dry-run   # lists what would run
supabase db push --linked
```

For a new, empty project run all of them in order with `supabase db push`
or paste each file into the SQL editor in number order. 0001 is written
with `if not exists` / `or replace` / `on conflict do nothing`, so
replaying it against a project that already has the tables does not fail.

Rules for new migrations:

- Name them `NNNN_short_description.sql`, next number up.
- New columns are nullable or have a default, so rows written before the
  change still load.
- Never edit a migration that has been applied to live; write a new one.

## Checks

`Backend/tests/storage/test_schema.py` parses every file here and fails if
the Python services, the Flutter app or the sensor sketch name a table,
column, function parameter or bucket that no migration defines, or if
either payload function stops building a key the app or poller reads. It
reads files only and never contacts Supabase.

```bash
cd Backend && python -m pytest -q -k schema
```
