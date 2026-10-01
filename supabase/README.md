# Supabase schema

`migrations/` contains the repository's numbered schema changes. Every schema
change belongs in a migration; do not make an unrecorded live-only change.

| File | What it contains | Deployment status |
|---|---|---|
| `0001_baseline.sql` | The committed baseline reconstructed before the supplied schema snapshot was reviewed. Its compatibility with that snapshot is being reconciled in [issue #5](https://github.com/Edwinta-dev/OutdoorKoiFYP/issues/5). | Do not assume it was applied or that it matches the deployed database. |
| `0002_worker_lease.sql` | `pond_chemistry_state.snapshot_version`, the `worker_lease` table, and `save_pond_snapshot` / `take_worker_lease` used by the separated API and poller. | Present in the repository; its absence from the supplied snapshot does not establish whether it was deployed. Track this in [issue #74](https://github.com/Edwinta-dev/OutdoorKoiFYP/issues/74). |

## Migration and snapshot rules

The supplied `schema(1).sql` is evidence of one exported schema state. It
reveals material differences from committed `0001_baseline.sql`, including
sensor pond identity, weather cache shapes, Singapore daily aggregation,
station resolution, image identifier/state types and RPC behaviour. Treat it
as observed state, including any defects. It does not establish live row
values, applied-migration history, storage bucket configuration, scheduled
jobs or deployed service revisions.

Before changing migration history, obtain the actual applied-migration ledger
and checksums. If a migration was applied, keep it immutable and add the next
numbered corrective migration. Only correct `0001_baseline.sql` if the owner
confirms from migration history that it has never been applied. Similar object
names or `IF NOT EXISTS` clauses are not proof that a migration is equivalent
or safe to mark applied. Never run the reconstructed baseline against an
existing database as a reconciliation method.

Keep the observed snapshot separate from the intended target. Compare tables,
columns and types, defaults, identity sequences, constraints, indexes, function
signatures and bodies, triggers, policies, grants, default privileges,
extensions and Realtime publication membership. Classify each difference as
existing behaviour, an intentional addition, a correction or an unknown that
needs owner evidence. Preserve legacy rows and identifiers through explicit
backfills and compatibility paths.

The committed `0002_worker_lease.sql` needs a rehearsal against the corrected
baseline. The supplied snapshot omits its objects; this alone is not a reason
to reopen its implementation issue or to mark it deployed. Record the
disposition and evidence in issue #74.

## Reconciliation workflow

Use disposable local databases for both paths:

1. Apply the reviewed migrations to a fresh database.
2. Restore the supplied schema snapshot with synthetic legacy rows, then apply
   the reviewed upgrade path.

Both paths must converge on the same reviewed target schema. Verify definitions,
grants, policies, publication membership and row/identifier preservation, not
just object names. SQL integration tests may use these disposable databases;
tests and CI must never contact the live Supabase project, NEA API or
PythonAnywhere service.

The schema-only dump does not include enough evidence to settle storage bucket
rows and object access, enabled API schemas, cron jobs, the external weather
writer, deployed revisions or actual migration history. Record those as
unknown until the owner supplies evidence. Do not infer a configured bucket or
scheduled job from a migration file or installed extension.

## Applying migrations

For a new, empty project, apply the reviewed migration sequence and verify the
result against the fresh-database rehearsal.

For an existing project, application is an owner deployment action tracked in
issue #74. Back up the database, verify the applied-migration ledger and
checksums, rehearse the exact upgrade from the supplied snapshot, review the
service revisions and external writers, then review the CLI dry-run. Apply
only the rehearsed sequence after owner approval. Do not use
`supabase migration repair --status applied` based only on objects appearing
to exist; migration repair requires confirmed history and checksums. After
application, collect a fresh schema export and verify row counts, ownership,
RPC access and service health.

Rules for new migrations:

- Name files `NNNN_short_description.sql`, using the next unused number on the
  implementation branch.
- Add nullable/default-compatible fields and explicit backfills. Preserve
  unknown legacy values instead of inventing them.
- Never edit an applied migration; add a later migration for corrections.
- Every changed RPC needs documented inputs, outputs, access rules and
  disposable SQL behaviour tests.

## Checks

`Backend/tests/storage/test_schema.py` parses the migration files and checks
that Python services, Flutter and firmware references are represented. It is a
static check; it does not prove SQL execution or live deployment state.

```bash
cd Backend && python -m pytest -q -k schema
```

Run the documented disposable-database migration and RPC suite separately.
If its database runtime is unavailable, report the skip and leave the SQL gate
incomplete. Never replace SQL execution tests with text parsing alone.
