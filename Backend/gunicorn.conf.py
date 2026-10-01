"""gunicorn settings for the digital twin API. From Backend/:

    pip install -e ".[wsgi]"
    gunicorn -c gunicorn.conf.py

Each worker process builds its own app, storage and EngineRegistry. That is
safe because the registry reloads a pond whenever the stored
snapshot_version is newer than its own and saves only against the version
it loaded (see koi/registry.py). The API never runs the poller; run
`python -m koi.worker` as its own process.
"""
wsgi_app = "koi.api:create_app()"
bind = "0.0.0.0:8080"
workers = 2
# Request threads per worker; the per-pond lock covers them.
threads = 4
worker_class = "gthread"
# The forecast endpoints read several tables in turn.
timeout = 60
accesslog = "-"
