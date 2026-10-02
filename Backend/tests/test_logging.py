"""koi.logs: JSON log lines, request ids, pond ids and no print calls (issue #10)."""
import ast
import io
import json
import logging
from pathlib import Path

import pytest

from conftest import DASHBOARD_PAYLOAD, USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.logs import JsonFormatter, configure_logging, log_event, pond_context
from koi.registry import EngineRegistry
from koi.worker import poller

KOI = Path(__file__).resolve().parent.parent / "koi"
FIELDS = {"time", "level", "service", "pond_id", "request_id", "event"}


@pytest.fixture
def json_log():
    """Routes the root logger's JSON output into a buffer; returns a
    function giving the lines written so far, parsed."""
    buf = io.StringIO()
    handler = configure_logging(make_settings(log_level="DEBUG"), "api", stream=buf)
    yield lambda: [json.loads(line) for line in buf.getvalue().splitlines()]
    logging.getLogger().removeHandler(handler)


def _format(record_fn, service="api"):
    """Formats the one record record_fn logs, through JsonFormatter."""
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("koi.test.logging")
    keep = Keep()
    logger.addHandler(keep)
    logger.setLevel(logging.DEBUG)
    try:
        record_fn(logger)
    finally:
        logger.removeHandler(keep)
    [record] = records
    return json.loads(JsonFormatter(service).format(record))


def test_logging_line_has_every_field_and_the_extra_ones():
    entry = _format(lambda log: log_event(log, "pond_polled", level=logging.WARNING, algae="Green"))
    assert FIELDS <= set(entry)
    assert entry["event"] == "pond_polled" and entry["level"] == "WARNING" and entry["algae"] == "Green"
    assert entry["service"] == "api" and entry["pond_id"] is None and entry["request_id"] is None
    assert entry["time"].endswith("+00:00")


def test_logging_pond_context_and_pond_id_field_set_pond_id():
    with pond_context(455):
        entry = _format(lambda log: log_event(log, "x"))
    assert entry["pond_id"] == "455"
    entry = _format(lambda log: log_event(log, "x", pond_id=12))
    assert entry["pond_id"] == "12" and "pond_id" not in {k for k in entry if k not in FIELDS}


def test_logging_service_follows_the_koi_subpackage():
    record = logging.LogRecord("koi.worker.poller", logging.INFO, "", 0, "hello", None, None)
    entry = json.loads(JsonFormatter("api").format(record))
    assert entry["service"] == "worker" and entry["event"] == "hello" and entry["logger"] == "koi.worker.poller"


def test_logging_exception_stays_on_one_line():
    def boom(log):
        try:
            raise ValueError("bad\nvalue")
        except ValueError:
            log_event(log, "failed", level=logging.ERROR, exc_info=True)

    record_text = None

    class Keep(logging.Handler):
        def emit(self, record):
            nonlocal record_text
            record_text = JsonFormatter("api").format(record)

    logger = logging.getLogger("koi.test.logging.exc")
    logger.addHandler(Keep())
    boom(logger)
    assert "\n" not in record_text
    assert "ValueError: bad" in json.loads(record_text)["exception"]


def test_logging_level_comes_from_settings():
    buf = io.StringIO()
    handler = configure_logging(make_settings(log_level="warning"), "worker", stream=buf)
    try:
        assert logging.getLogger().level == logging.WARNING
        logging.getLogger("koi.worker").info("hidden")
        logging.getLogger("koi.worker").warning("shown")
        again = configure_logging(make_settings(log_level="WARNING"), "worker", stream=buf)
        assert handler not in logging.getLogger().handlers, "a second call replaces the handler"
        handler = again
    finally:
        logging.getLogger().removeHandler(handler)
    assert [json.loads(line)["event"] for line in buf.getvalue().splitlines()] == ["shown"]


def test_logging_request_id_is_taken_from_the_header_and_returned():
    client = api_client(create_app(make_settings(), storage=make_storage()))
    resp = client.get(f"/assessment/{USER}", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["X-Request-ID"] == "abc-123"
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32
    unsafe = client.get("/health", headers={"X-Request-ID": 'bad id "quoted"'}).headers["X-Request-ID"]
    assert unsafe != 'bad id "quoted"' and len(unsafe) == 32
    too_long = client.get("/health", headers={"X-Request-ID": "a" * 65}).headers["X-Request-ID"]
    assert too_long != "a" * 65


def test_logging_request_lines_carry_request_and_pond_ids():
    app = create_app(make_settings(), storage=make_storage())
    buf = io.StringIO()
    handler = configure_logging(make_settings(log_level="DEBUG"), "api", stream=buf)
    try:
        resp = api_client(app).get(f"/assessment/{USER}", headers={"X-Request-ID": "req-1"})
    finally:
        logging.getLogger().removeHandler(handler)
    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    [request_line] = [e for e in lines if e["event"] == "request"]
    assert request_line["request_id"] == "req-1" and request_line["pond_id"] == str(USER)
    assert request_line["route"] == "/assessment/<int:user_id>" and request_line["status"] == resp.status_code
    assert all(FIELDS <= set(e) for e in lines)


def test_logging_a_poll_cycle_writes_json_lines_without_row_dumps(json_log):
    storage = make_storage()
    worker = poller.Worker(make_settings(), EngineRegistry(storage), holder="w1")
    assert worker.run_cycle() is True
    lines = json_log()
    events = [e["event"] for e in lines]
    assert "pond_polled" in events and "poll_cycle_finished" in events
    assert all(FIELDS <= set(e) for e in lines)
    [polled] = [e for e in lines if e["event"] == "pond_polled"]
    assert polled["pond_id"] == str(USER) and polled["service"] == "worker"
    text = json.dumps(lines)
    # Nothing from the dashboard payload or UserData row is logged.
    for key in ("raw_sensor", "nea_forecasts", "outlook_4day", "biomass", "volume_litres"):
        assert key not in text
    assert str(DASHBOARD_PAYLOAD["raw_sensor"]["LUX"]) not in text


def test_logging_no_print_calls_in_the_koi_package():
    calls = []
    for path in KOI.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                calls.append(f"{path.relative_to(KOI)}:{node.lineno}")
    assert calls == []
    # The issue's acceptance check is a plain text search, so "print(" may
    # not appear anywhere in the package, not even inside a longer name.
    assert [p.name for p in KOI.rglob("*.py") if "print(" in p.read_text(encoding="utf-8")] == []
