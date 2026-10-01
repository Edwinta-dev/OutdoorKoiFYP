# mobile_app

Flutter client for OutdoorKoi.

## Configuration

Every environment value is passed at build time from a JSON file:

```bash
cp env/dev.json.example env/dev.json     # or env/prod.json.example
flutter run --dart-define-from-file=env/dev.json
```

| Key | Value |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase publishable (anon) key; never the secret key |
| `DIGITAL_TWIN_BASE_URL` | Base URL of the digital twin API (`python -m koi.api`) |
| `POND_IMAGE_BUCKET` | Storage bucket for camera frames; must match `POND_IMAGE_BUCKET` in Backend/.env |

The real `env/*.json` files are gitignored. There are no built-in
defaults: a build missing any value opens on a configuration error screen
that lists what is missing (see `lib/config/app_config.dart`).
