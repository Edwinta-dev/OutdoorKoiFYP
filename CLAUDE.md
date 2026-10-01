# CLAUDE.md

Read README.md for the project, layout and run commands. This file is only
the working rules for agents.

## Before you commit
- Run `python tools/check.py all`. Everything must be PASS. Flutter and
  arduino-cli aren't installed on this machine, so their SKIPs are expected;
  CI runs them with --strict. After pushing, confirm with `gh run list`.
- One issue per commit (or a small series), message `issue #N: <summary>`.

## Git
- Work on `automation/outdoor-koi`. Never push to `main`, never force-push.
- Never commit `.env`, `secrets.h`, `env/*.json` or `.mcp.json`.

## Choosing issues
- Pick open `block:NEXT` issues, lowest number first; leave `stretch` for last.
- Skip `owner-action` and `hardware` issues, plus #74; they need the owner.
- When finished, comment on the issue: what changed, how it was verified,
  and what is still unverified. Leave closing to the owner.

## Hard limits
- Tests and CI must never contact the live Supabase project, NEA API or
  PythonAnywhere.
- Database changes follow supabase/README.md: new numbered migration only,
  never edit an applied one, never run `migration repair` or `db push`.
- A local Supabase stack is available for SQL tests (Docker Desktop must be
  running): `supabase start` from the repo root, database at
  `postgresql://postgres:postgres@127.0.0.1:54322/postgres`. It is
  disposable: `supabase db reset` rebuilds it from `supabase/migrations`.
  Use only the local stack in tests and scripts. If Docker is not running,
  report SQL tests as SKIP.
- A read-only Supabase MCP server for the live project may be configured on
  this machine (local scope, not committed). Use it only when the owner asks
  in an attended session, for read-only evidence; never in unattended runs.

## Stale material
- `claude/project-context.md` is a 2026-08-20 snapshot: `Backend/DigitalTwin/`
  is now `Backend/koi/` and the "uncommitted WIP" is committed. Treat it as
  history; the code and README win.
