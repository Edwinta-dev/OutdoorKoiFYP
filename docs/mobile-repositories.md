# Mobile repositories and providers

`lib/data/repositories.dart` defines separate interfaces for pond profiles,
the dashboard, telemetry history, assessments, forecasts, events, camera
frames and ratings. `LivePondDataSource` implements those interfaces together
to reuse the app's existing transport and storage adapters. Each repository
has its own Riverpod provider and can be overridden independently.
`test/helpers/fake_pond_data_source.dart` implements the same interfaces with
recorded calls, synthetic values, pending futures and injected failures.

Screens and remote widgets read providers. Riverpod owns loading, data,
errors and refreshes; form inputs and carousel selection stay in widget
state. Startup installs a `ProviderScope`. The widget harness overrides
the remote repositories and local profile repository without initializing
Supabase or device preferences. Retries are explicit through the existing
retry controls, with automatic Riverpod retries disabled at the app scope.

The dashboard shares one `/v1/ponds/{pond}/dashboard` request and refreshes
every 30 seconds while mounted. Manual refresh, saved interventions and
ratings invalidate the affected providers. The response is parsed by the
existing `PondDashboard.fromJson` contract models. The presentation adapter
keeps the established card inputs; telemetry history belongs to the detail
screen's history repository rather than a second dashboard fetch.

`DigitalTwinApi` has an injectable HTTP client, base URL and access-token
callback. All calls use `/v1`, with the current Supabase session token when
available. The backend no longer serves the retired assessment, forecast,
event or rating aliases. Health and readiness probes retain their
unversioned paths. Camera `POST /upload` is unchanged.

Supabase remains responsible for operations the API does not cover:

| Operation | Storage access |
|---|---|
| Telemetry chart history | `get_historical_graph_payload` RPC |
| Chart intervention rows | `pondInterventions` insert |
| Onboarding location/account row | `UserData` upsert |
| Pond deletion | `UserData` delete |
| Species catalogue and keeper photos | `Fish_Database` and existing gallery bucket |
| Latest camera frame | `imageTable` read |

The event repository saves chart history first, then sends the same UUID
and time to `/v1`. The API saves the engine ledger, but does not write
`pondInterventions`. If the immediate API call fails, the worker can apply
the saved row. A failed history write stops the save. Successful event
responses parse the `chemistry` object rather than the entire envelope.
Profile chemistry values are written and read through `/v1/.../profile`.

`PreferencesProfileRepository` is the only adapter that obtains device
preferences. It caches that instance and reads both legacy string IDs and
integer IDs into one nullable integer pond identity. New onboarding writes
an integer. Typed profile, chart and camera parsers accept older rows whose
optional metadata is absent. No database migration is required.

Deploy the backend and release the updated mobile app together. Clients
that still use the retired aliases must update. The existing species-photo
bucket mismatch remains tracked by the schema tests; this issue does not
change that storage contract.
