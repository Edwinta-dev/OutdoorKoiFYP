# Supabase schema

`migrations/` contains the repository's numbered schema changes. Every schema
change belongs in a migration; do not make an unrecorded live-only change.

| File | What it contains | Live status |
|---|---|---|
| `0001_baseline.sql` | The live schema as observed: the supplied dump (`schema.sql`) plus one comment line, so every public function matches live exactly. Defects included. | Recorded as applied on 2026-10-01 (`migration repair`); never run it there. |
| `0002_worker_lease.sql` | `pond_chemistry_state.snapshot_version`, the `worker_lease` table, and `save_pond_snapshot` / `take_worker_lease` used by the separated API and poller (issue #8). | Applied 2026-10-01. |
| `0003_worker_status.sql` | The `worker_status` table: the poller's last-cycle report, read by the API's `/ready` and `/metrics` (issue #10). | Applied 2026-10-01. |
| `0004_observed_storage.sql` | The live storage buckets (`imageAnalysisBucket`, `FishImages`) and their eight anon policies, as observed. | Applied 2026-10-01 (no change on live). |
| `0005_algae_severity_ratings.sql` | The `algae_severity_ratings` table for the app's rating card. | Applied 2026-10-01. |
| `0006_hourly_history_utc_instants.sql` | `get_pond_telemetry_history` returns UTC instants whatever the session time zone (defect D9, issue #15). | Not yet applied. |
| `0007_auth_link.sql` | `UserData.auth_uid` (nullable, unique: one pond per account until #67), a trigger refusing app-role changes to the link, `auth_session_active` (backend only) and `link_pond_to_account` (owner only, SQL editor) for the API's sign-in check (issue #11). Access per function is listed in its header. | Not yet applied. |
| `0008_pond_profile.sql` | The effective-dated `pond_profile` table (volume, depth, biomass in grams, fish, tap water, aeration), seeded from `UserData` with biomass converted from kg to g, and a trigger recording later `UserData` volume or biomass writes as new rows (issue #16). Backend only; the trigger function is security definer so the app's onboarding upsert keeps working. | Not yet applied. |

How each file was derived, the live evidence and every known difference
and defect are in
[docs/database-reconciliation.md](../docs/database-reconciliation.md).

## The observed snapshot

`schema.sql` at the repository root is the owner-supplied schema-only dump
(`schema(1).sql`), committed unmodified (SHA-256 in the reconciliation
report). Do not edit or regenerate it. `.gitattributes` keeps its bytes and
those of `0001_baseline.sql`, whose function bodies use CRLF as on live.

The live project had no migration history when it was checked, so
`0001_baseline.sql` was corrected in place to match it; the earlier
reconstruction is kept in `archive/pre-refactor/supabase/`. From now on,
never edit an applied migration; add the next numbered one instead.

## Local stack and tests

Requires Docker Desktop and the Supabase CLI. From the repository root:

```bash
supabase start                   # first run downloads the images
supabase db reset                # rebuild the local database from migrations/
python tools/db_rehearsal.py     # fresh path and dump-upgrade path must converge
cd Backend && python -m pytest -q tests/sql      # SQL behaviour tests
cd Backend && python -m pytest -q -k schema      # text-based reference check
```

`tools/db_rehearsal.py` restores `schema.sql` into a clean local database,
loads synthetic legacy rows, applies every later migration, and compares
the result with a fresh `supabase db reset`: public schema (definitions,
grants, policies, publication membership), storage buckets and policies,
and every legacy row. It leaves the local database at the current
migrations.

The SQL tests run each case in a rolled-back transaction, refuse any
`KOI_TEST_DB_URL` that is not local, and skip when no local database
answers (as in CI). If Docker is not running, report them as SKIP; text
parsing does not replace them.

Tests, scripts and CI never contact the live project, the NEA API or
PythonAnywhere.

## Bringing the live project in line (owner, issue #74)

Done on 2026-10-01 with the steps below, after a full backup of live was
taken, restored and checked locally, the upgrade rehearsed on that copy,
and a rollback script tested (backups and rollback are kept off-repo by
the owner). Verified afterwards: history shows `0001`-`0005`, the new
objects exist with RLS on and closed to `anon`, the existing snapshot has
`snapshot_version` 1, and all seven public function checksums are
unchanged. Keep the steps for any future project built from the same
dump.

Live needs `0002`, `0003` and `0005` (`0004` is a no-op there). `0001`
must not run on live; it is recorded as applied because it matches live,
which the reconciliation report shows (function checksums equal, dump
restored and compared).

1. Back up the database (Supabase dashboard, Database, Backups).
2. Link the CLI: `supabase link --project-ref mkzfdxhzmrnapvrhshte`.
3. Record the baseline as applied, without running it:
   `supabase migration repair --status applied 0001`.
4. Review what would run: `supabase db push --dry-run`. It must list
   exactly `0002`-`0005`.
5. Apply: `supabase db push`.
6. Check: `supabase migration list --linked` shows `0001`-`0005` applied;
   `select snapshot_version from pond_chemistry_state;` returns 1 for the
   existing row; the API's `/ready` returns 200 once the worker has
   written its first status.

Agents must not run `migration repair` or `db push`; these steps are the
owner's.

## Rules for new migrations

- Name files `NNNN_short_description.sql`, using the next unused number.
- Add nullable or defaulted columns and explicit backfills. Preserve
  unknown legacy values instead of inventing them.
- The live default privileges grant `anon` and `authenticated` everything on
  new tables and functions. Until issue #12 changes that, revoke explicitly
  and enable RLS on backend-only objects, as `0002`, `0003` and `0005` do.
- Every changed RPC needs documented inputs, outputs and access rules, and
  a test in `Backend/tests/sql`.
- After adding a migration, run `supabase db reset`, the rehearsal and the
  SQL tests.
