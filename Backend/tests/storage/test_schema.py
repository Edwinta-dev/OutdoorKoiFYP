"""Schema drift check: code against supabase/migrations.

Parses every migration under supabase/migrations/ into the set of tables,
columns, functions and storage buckets it defines, then scans the code
that talks to Supabase and fails if any of it names something no
migration defines:

- Python (the koi package under Backend/koi): string literals in
  .table("...") chains - .select() column lists, filter and order
  columns, dict keys passed to .insert()/.upsert(), on_conflict - plus
  .rpc() names and parameters and storage bucket names.
- Dart (MobileUI/mobile_app/lib): the same for .from('...') chains,
  .rpc() calls and storage .from() buckets.
- Firmware (Embedded/*/*.ino): the table and JSON keys of db.insert().

It also checks that get_bundled_dashboard_payload and
get_historical_graph_payload build every key the poller, the conftest
payload fixture and the Dart code read from them.

Pure text parsing: no database, no network. A new column or table goes in
a new migration file, never in code alone.
Run from Backend/: python -m pytest -q -k schema
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "supabase" / "migrations"
PYTHON_DIRS = [REPO / "Backend" / "koi"]
DART_LIB = REPO / "MobileUI" / "mobile_app" / "lib"
EMBEDDED = REPO / "Embedded"

# PostgREST methods whose first argument is a column name.
COLUMN_ARG_METHODS = {
    "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is_", "in_",
    "contains", "order", "not_", "match",
    # Dart spellings
    "inFilter", "isFilter", "not",
}
WRITE_METHODS = {"insert", "upsert", "update"}


# ---------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------
@dataclass
class Schema:
    tables: dict[str, set[str]] = field(default_factory=dict)
    functions: dict[str, list[str]] = field(default_factory=dict)
    function_bodies: dict[str, str] = field(default_factory=dict)
    buckets: set[str] = field(default_factory=set)


IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
QUALIFIED = rf"(?:{IDENT}\.)?({IDENT})"


def _ident(token: str) -> str:
    """SQL identifier as PostgREST sees it: quoted names keep their case,
    unquoted names fold to lower case."""
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    return token.lower()


def _split_top_level(text: str) -> list[str]:
    parts, depth, start, quote = [], 0, 0, ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced parenthesis at {open_index}")


_TABLE_CONSTRAINT = re.compile(r"(?i)^(constraint|primary\s+key|unique|foreign\s+key|check|exclude)\b")


def parse_sql(sql: str, schema: Schema | None = None) -> Schema:
    """Applies one migration's DDL to schema (a new one if None)."""
    schema = schema or Schema()

    # Function bodies first, so their text does not look like DDL.
    fn_re = re.compile(
        rf"(?is)create\s+(?:or\s+replace\s+)?function\s+{QUALIFIED}\s*\((.*?)\)\s*returns\b(.*?)\$(\w*)\$(.*?)\$\4\$"
    )
    for m in fn_re.finditer(sql):
        name = _ident(m.group(1))
        params = []
        for arg in _split_top_level(m.group(2)):
            tokens = arg.split()
            if tokens and tokens[0].lower() in ("in", "out", "inout", "variadic"):
                tokens = tokens[1:]
            if len(tokens) >= 2:
                params.append(_ident(tokens[0]))
        schema.functions[name] = params
        schema.function_bodies[name] = m.group(5)
    sql = fn_re.sub(";", sql)
    sql = re.sub(r"--[^\n]*", "", sql)

    for m in re.finditer(rf"(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?{QUALIFIED}\s*\(", sql):
        close = _matching_paren(sql, m.end() - 1)
        columns = set()
        for item in _split_top_level(sql[m.end():close]):
            if _TABLE_CONSTRAINT.match(item):
                continue
            columns.add(_ident(re.match(IDENT, item).group(0)))  # type: ignore[union-attr]
        schema.tables[_ident(m.group(1))] = columns

    alter_re = re.compile(rf"(?is)alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{QUALIFIED}\s+(.*?);")
    for m in alter_re.finditer(sql):
        table = _ident(m.group(1))
        for action in _split_top_level(m.group(2)):
            add = re.match(rf"(?is)add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?({IDENT})", action)
            drop = re.match(rf"(?is)drop\s+(?:column\s+)?(?:if\s+exists\s+)?({IDENT})", action)
            rename = re.match(rf"(?is)rename\s+(?:column\s+)?({IDENT})\s+to\s+({IDENT})", action)
            rename_table = re.match(rf"(?is)rename\s+to\s+({IDENT})", action)
            if rename_table:
                schema.tables[_ident(rename_table.group(1))] = schema.tables.pop(table)
            elif rename:
                schema.tables[table].discard(_ident(rename.group(1)))
                schema.tables[table].add(_ident(rename.group(2)))
            elif add and not _TABLE_CONSTRAINT.match(action[3:].strip()):
                schema.tables[table].add(_ident(add.group(1)))
            elif drop and not re.match(r"(?i)drop\s+constraint", action):
                schema.tables[table].discard(_ident(drop.group(1)))

    for m in re.finditer(rf"(?is)drop\s+table\s+(?:if\s+exists\s+)?{QUALIFIED}", sql):
        schema.tables.pop(_ident(m.group(1)), None)

    for m in re.finditer(r"(?is)insert\s+into\s+storage\.buckets\s*\([^)]*\)\s*values\s*(.*?)(?:on\s+conflict|;)", sql):
        schema.buckets.update(re.findall(r"\(\s*'([^']+)'", m.group(1)))
    return schema


def load_schema() -> Schema:
    schema = Schema()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        parse_sql(path.read_text(encoding="utf-8"), schema)
    return schema


# ---------------------------------------------------------------------
# Code references
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class Ref:
    kind: str       # "table", "column", "rpc", "rpc_param", "bucket"
    name: str       # table/function/bucket name
    column: str     # column or parameter name ("" for table/rpc/bucket)
    where: str      # file:line

    def __str__(self) -> str:
        target = f"{self.name}.{self.column}" if self.column else self.name
        return f"{self.where}: {self.kind} {target}"


def select_columns(select: str) -> list[str]:
    """Column names in a PostgREST select string. Embedded resources
    (relation(...)) are not used anywhere yet and are rejected so the test
    is extended deliberately when they are."""
    columns = []
    for part in select.split(","):
        part = part.strip().strip('"')
        if not part or part == "*":
            continue
        if "(" in part:
            raise ValueError(f"embedded select not supported by the schema test: {select!r}")
        part = part.split(":")[-1].split("::")[0].strip().strip('"')  # alias:column, column::cast
        columns.append(part)
    return columns


# --- Python ----------------------------------------------------------
def _const_str(node: ast.AST | None, names: dict[str, ast.AST]) -> str | None:
    """A string literal, a name bound to one, or os.environ.get(name, default)."""
    seen = 0
    while isinstance(node, ast.Name) and node.id in names and seen < 10:
        node, seen = names[node.id], seen + 1
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and len(node.args) == 2):
        return _const_str(node.args[1], names)
    return None


def _chain(node: ast.AST) -> list[ast.Call]:
    """The calls of a method chain, innermost first."""
    calls = []
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        calls.append(node)
        node = node.func.value
    calls.reverse()
    return calls


def _chain_root(node: ast.AST) -> ast.AST:
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        node = node.func.value
    return node


def python_refs(source: str, filename: str) -> list[Ref]:
    tree = ast.parse(source)
    module_names = _assignments(tree.body)
    refs: list[Ref] = []
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    module_level = [s for s in tree.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for scope in [*functions, ast.Module(body=module_level, type_ignores=[])]:
        nodes = list(ast.walk(scope))
        names = {**module_names, **_assignments(nodes)}
        # q = supabase.table("x")... lets a later q.gt("col") resolve to x.
        query_vars = {}
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                table = _chain_table(node.value, names)
                if table is not None:
                    query_vars[node.targets[0].id] = table
        inner = {id(n.func.value) for n in nodes if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for node in nodes:
            if isinstance(node, ast.Call) and id(node) not in inner:
                refs += _chain_refs(node, names, query_vars, f"{filename}:{node.lineno}")
    return sorted(set(refs), key=str)


def _assignments(nodes: list) -> dict[str, ast.AST]:
    return {t.id: n.value for node in nodes for n in ast.walk(node) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}


def _chain_table(node: ast.AST, names: dict[str, ast.AST]) -> str | None:
    for call in _chain(node):
        if call.func.attr == "table" and call.args:  # type: ignore[attr-defined]
            return _const_str(call.args[0], names)
    return None


def _chain_refs(node: ast.Call, names: dict[str, ast.AST], query_vars: dict[str, str], where: str) -> list[Ref]:
    """References made by one complete method chain."""
    refs: list[Ref] = []
    root = _chain_root(node)
    table = query_vars.get(root.id) if isinstance(root, ast.Name) else None
    for call in _chain(node):
        attr = call.func.attr  # type: ignore[attr-defined]
        if attr == "table" and call.args:
            table = _const_str(call.args[0], names)
            if table is not None:
                refs.append(Ref("table", table, "", where))
        elif attr == "from_" and call.args and _is_storage(call):
            bucket = _const_str(call.args[0], names)
            if bucket is not None:
                refs.append(Ref("bucket", bucket, "", where))
        elif attr == "rpc" and call.args:
            fn = _const_str(call.args[0], names)
            if fn is not None:
                refs.append(Ref("rpc", fn, "", where))
                if len(call.args) > 1 and isinstance(call.args[1], ast.Dict):
                    refs += [Ref("rpc_param", fn, k.value, where) for k in call.args[1].keys
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        elif table is not None:
            refs += _python_column_refs(call, table, names, where)
    return refs


def _is_storage(call: ast.Call) -> bool:
    target = call.func.value  # type: ignore[attr-defined]
    return isinstance(target, ast.Attribute) and target.attr == "storage"


def _python_column_refs(call: ast.Call, table: str, names: dict[str, ast.AST], where: str) -> list[Ref]:
    attr = call.func.attr  # type: ignore[attr-defined]
    refs = []
    if attr == "select" and call.args:
        select = _const_str(call.args[0], names)
        if select is not None:
            refs += [Ref("column", table, c, where) for c in select_columns(select)]
    elif attr in COLUMN_ARG_METHODS and call.args:
        column = _const_str(call.args[0], names)
        if column is not None:
            refs.append(Ref("column", table, column, where))
    elif attr in WRITE_METHODS and call.args:
        payload = call.args[0]
        rows = payload.elts if isinstance(payload, ast.List) else [payload]
        for row in rows:
            if isinstance(row, ast.Dict):
                refs += [Ref("column", table, k.value, where) for k in row.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        for kw in call.keywords:
            if kw.arg == "on_conflict":
                conflict = _const_str(kw.value, names)
                if conflict:
                    refs += [Ref("column", table, c.strip(), where) for c in conflict.split(",")]
    return refs


# --- Dart ------------------------------------------------------------
_DART_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""


def _dart_str(m: re.Match, first_group: int) -> str:
    return m.group(first_group) if m.group(first_group) is not None else m.group(first_group + 1)


def _chain_end(text: str, start: int) -> int:
    """Index of the ';' ending the statement that starts at start."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return i
        elif ch == ";" and depth == 0:
            return i
    return len(text)


def _dart_map_keys(text: str, var: str) -> list[str]:
    """Keys of `var = {'k': ...}` and `var['k'] = ...` in one Dart file."""
    keys = []
    m = re.search(rf"\b{re.escape(var)}\s*=\s*\{{", text)
    if m:
        body = text[m.end():_chain_end(text, m.end())]
        keys += [_dart_str(k, 1) for k in re.finditer(rf"{_DART_STR}\s*:", body)]
    keys += [_dart_str(k, 1) for k in re.finditer(rf"\b{re.escape(var)}\[{_DART_STR}\]\s*=", text)]
    return keys


def dart_refs(text: str, filename: str) -> list[Ref]:
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    constants = {m.group(1): _dart_str(m, 2)
                 for m in re.finditer(rf"\bconst\s+(?:String\s+)?(\w+)\s*=\s*{_DART_STR}\s*;", text)}
    refs: list[Ref] = []

    def line(pos: int) -> str:
        return f"{filename}:{text.count(chr(10), 0, pos) + 1}"

    for m in re.finditer(rf"\.from\(\s*(?:{_DART_STR}|(\w+))\s*\)", text):
        name = _dart_str(m, 1) if m.group(3) is None else constants.get(m.group(3))
        if name is None:
            continue  # bucket or table name from runtime configuration
        if re.search(r"\.storage\s*$", text[:m.start()]):
            refs.append(Ref("bucket", name, "", line(m.start())))
            continue
        refs.append(Ref("table", name, "", line(m.start())))
        chain = text[m.end():_chain_end(text, m.end())]
        where = line(m.start())
        for c in re.finditer(rf"\.select\(\s*{_DART_STR}", chain):
            refs += [Ref("column", name, col, where) for col in select_columns(_dart_str(c, 1))]
        for c in re.finditer(rf"\.(\w+)\(\s*{_DART_STR}", chain):
            if c.group(1) in COLUMN_ARG_METHODS:
                refs.append(Ref("column", name, _dart_str(c, 2), where))
        for c in re.finditer(r"\.(insert|upsert|update)\(\s*(\{|\w+)", chain):
            if c.group(2) == "{":
                body = chain[c.end():_chain_end(chain, c.end())]
                keys = [_dart_str(k, 1) for k in re.finditer(rf"{_DART_STR}\s*:", body)]
            else:
                keys = _dart_map_keys(text, c.group(2))
            refs += [Ref("column", name, k, where) for k in keys]

    for m in re.finditer(rf"\.rpc\(\s*{_DART_STR}", text):
        fn = _dart_str(m, 1)
        refs.append(Ref("rpc", fn, "", line(m.start())))
        call = text[m.end():_chain_end(text, m.end())]
        params = re.search(r"params:\s*\{", call)
        if params:
            body = call[params.end():_chain_end(call, params.end())]
            refs += [Ref("rpc_param", fn, _dart_str(k, 1), line(m.start()))
                     for k in re.finditer(rf"{_DART_STR}\s*:", body)]
    return refs


# --- Firmware --------------------------------------------------------
def firmware_refs(text: str, filename: str) -> list[Ref]:
    refs: list[Ref] = []
    constants = dict(re.findall(r'const\s+char\s*\*\s*(\w+)\s*=\s*"([^"]*)"', text))
    objects = set(re.findall(r"\bJsonObject\s+(\w+)", text))
    for m in re.finditer(r'\.insert\(\s*(?:"([^"]*)"|(\w+))\s*,', text):
        table = m.group(1) or constants.get(m.group(2))
        if table is None:
            continue
        where = f"{filename}:{text.count(chr(10), 0, m.start()) + 1}"
        refs.append(Ref("table", table, "", where))
        for obj in objects:
            refs += [Ref("column", table, k, where) for k in re.findall(rf'\b{obj}\["(\w+)"\]\s*=', text)]
    return refs


# ---------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------
def undefined_refs(schema: Schema, refs: list[Ref]) -> list[str]:
    problems = []
    for ref in refs:
        if ref.kind == "table" and ref.name not in schema.tables:
            problems.append(str(ref))
        elif ref.kind == "column" and ref.column not in schema.tables.get(ref.name, set()):
            problems.append(str(ref))
        elif ref.kind == "rpc" and ref.name not in schema.functions:
            problems.append(str(ref))
        elif ref.kind == "rpc_param" and ref.column not in schema.functions.get(ref.name, []):
            problems.append(str(ref))
        elif ref.kind == "bucket" and ref.name not in schema.buckets:
            problems.append(str(ref))
    return sorted(set(problems))


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def code_refs() -> list[Ref]:
    refs: list[Ref] = []
    for d in PYTHON_DIRS:
        for path in sorted(d.rglob("*.py")):
            if path.name.startswith("test_") or path.name == "conftest.py":
                continue
            refs += python_refs(path.read_text(encoding="utf-8"), _rel(path))
    for path in sorted(DART_LIB.rglob("*.dart")):
        refs += dart_refs(path.read_text(encoding="utf-8"), _rel(path))
    for path in sorted(EMBEDDED.glob("*/*.ino")):
        refs += firmware_refs(path.read_text(encoding="utf-8"), _rel(path))
    return refs


@pytest.fixture(scope="module")
def schema() -> Schema:
    return load_schema()


@pytest.fixture(scope="module")
def refs() -> list[Ref]:
    return code_refs()


# ---------------------------------------------------------------------
# Tests against the real repository
# ---------------------------------------------------------------------
# Tables the baseline must define (issue #5), whether or not code in this
# repository names them directly.
BASELINE_TABLES = {
    "SensorData", "imageTable", "UserData", "pondInterventions",
    "daily_sensor_averages", "weather_telemetry", "weather_forecasts",
    "Fish_Database", "pond_chemistry_state", "pond_chemistry_evaluations",
    "pond_evaporation_evaluations", "pond_algae_evaluations",
    "algae_severity_ratings",
}


def test_schema_migrations_exist_and_define_baseline(schema):
    assert (MIGRATIONS / "0001_baseline.sql").exists()
    missing = BASELINE_TABLES - set(schema.tables)
    assert not missing, f"tables missing from migrations: {sorted(missing)}"
    assert "get_bundled_dashboard_payload" in schema.functions
    assert schema.functions["get_bundled_dashboard_payload"] == ["p_user_id"]


def test_schema_scan_finds_every_client(refs):
    """Guards against the scanner silently finding nothing: each client
    that talks to Supabase must contribute references."""
    sources = {r.where.split(":")[0] for r in refs}
    for expected in ["Backend/koi/storage/supabase_storage.py",
                     "Embedded/sensor_node/sensor_node.ino",
                     "MobileUI/mobile_app/lib/data/pond_data_source.dart",
                     "MobileUI/mobile_app/lib/utils/pond_camera_storage.dart"]:
        assert expected in sources, f"no Supabase references found in {expected}"
    kinds = {r.kind for r in refs}
    assert kinds == {"table", "column", "rpc", "rpc_param", "bucket"}


# Objects code names that the live database does not have, so the observed
# baseline (0001) does not define them either. Each is a real defect
# recorded in docs/database-reconciliation.md; remove the entry when a
# migration or code change fixes it (the staleness test below enforces it).
KNOWN_UNDEFINED = {
    ("bucket", "pond-images"): "app uploads species photos to a bucket the live project "
                               "does not have (it has FishImages); report defect D10",
}


def test_schema_defines_everything_code_references(schema, refs):
    problems = [p for p in undefined_refs(schema, refs)
                if not any(p.endswith(f": {kind} {name}") for kind, name in KNOWN_UNDEFINED)]
    assert not problems, (
        "code references database objects no migration defines "
        "(add a migration under supabase/migrations/):\n  " + "\n  ".join(problems))


def test_schema_known_undefined_entries_are_still_undefined(schema, refs):
    """A KNOWN_UNDEFINED entry must still be referenced by code and still
    be missing from the migrations; otherwise delete it."""
    referenced = {(r.kind, r.name) for r in refs}
    for kind, name in KNOWN_UNDEFINED:
        assert (kind, name) in referenced, f"{kind} {name} is no longer referenced"
        if kind == "bucket":
            assert name not in schema.buckets, f"bucket {name} is now defined; remove it"


def test_schema_defines_camera_default_bucket(schema):
    """The camera service reads its bucket from POND_IMAGE_BUCKET (via
    koi.settings) with a default; the default must be a bucket the
    migrations create."""
    from koi.settings import Settings

    field = Settings.model_fields["pond_image_bucket"]
    assert "POND_IMAGE_BUCKET" in field.validation_alias.choices,         "settings no longer read POND_IMAGE_BUCKET"
    assert field.default in schema.buckets


def _dart_keys(path: Path, var_pattern: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {_dart_str(m, 1) for m in re.finditer(rf"\b(?:{var_pattern})\??\[{_DART_STR}\]", text)}


def _body_literals(schema: Schema, fn: str) -> set[str]:
    body = re.sub(r"--[^\n]*", "", schema.function_bodies[fn])
    return set(re.findall(r"'([^']*)'", body))


def bundled_payload_keys() -> set[str]:
    """Every key read from get_bundled_dashboard_payload's result."""
    from conftest import DASHBOARD_PAYLOAD

    keys = set(DASHBOARD_PAYLOAD) | {'telemetry_history', 'tempC'}
    for section in ("raw_sensor", "nea_telemetry"):
        keys |= set(DASHBOARD_PAYLOAD[section])
    keys |= {"forecast_2hr", "forecast_24hr", "outlook_4day"}  # forecast_utils.py

    screen = DART_LIB / "screens" / "dashboard_view.dart"
    keys |= _dart_keys(screen, r"_dashboardData|data|item")
    for path in (DART_LIB / "widgets").rglob("*.dart"):
        keys |= _dart_keys(path, r"sensorData|telemetryData|forecastData")
    return keys


def historical_payload_keys() -> set[str]:
    keys = _dart_keys(DART_LIB / "screens" / "detail_graph_screen.dart", r"data|e")
    keys |= _dart_keys(DART_LIB / "data" / "telemetry_history.dart", r"json")
    keys |= _dart_keys(DART_LIB / "widgets" / "detail_graph" / "historical_line_chart.dart",
                       r"points\[i\]|ev|points\.first")
    return keys


# raw_sensor's keys are SensorData.sensor_type values, built by
# jsonb_object_agg(sensor_type, data1) rather than written as literals.
SENSOR_TYPE_KEYS = {"pH", "TDS", "temp", "LUX"}

# Keys the app reads that the live get_bundled_dashboard_payload has never
# returned (the earlier reconstructed baseline invented them). The app
# falls back when they are absent. Report defect D11; remove an entry once
# a migration adds the key.
KNOWN_MISSING_PAYLOAD_KEYS = {
    "two_hr_forecast": "ph_outcome_card.dart and solar_outcome_card.dart; live returns forecast_2hr",
    "rainfall_mm": "ph_outcome_card.dart; live returns nea_telemetry.rainfall",
}


def test_schema_bundled_payload_builds_every_read_key(schema):
    keys = bundled_payload_keys()
    assert {"raw_sensor", "nea_telemetry", "nea_forecasts", "telemetry_history",
            "pH", "TDS", "temp", "LUX", "tempC", "uv_index"} <= keys
    body = schema.function_bodies["get_bundled_dashboard_payload"]
    assert re.search(r"jsonb_object_agg\(\s*sensor_type\s*,\s*data1\s*\)", body), \
        "raw_sensor is no longer keyed by sensor_type; update SENSOR_TYPE_KEYS"
    built = _body_literals(schema, "get_bundled_dashboard_payload") | SENSOR_TYPE_KEYS
    missing = keys - built - set(KNOWN_MISSING_PAYLOAD_KEYS)
    assert not missing, f"get_bundled_dashboard_payload does not build: {sorted(missing)}"


def test_schema_known_missing_payload_keys_are_still_missing(schema):
    built = _body_literals(schema, "get_bundled_dashboard_payload")
    keys = bundled_payload_keys()
    for key in KNOWN_MISSING_PAYLOAD_KEYS:
        assert key in keys, f"{key} is no longer read; remove it from KNOWN_MISSING_PAYLOAD_KEYS"
        assert key not in built, f"{key} is now built; remove it from KNOWN_MISSING_PAYLOAD_KEYS"


def test_schema_historical_payload_builds_every_read_key(schema):
    keys = historical_payload_keys()
    assert {"daily_trends", "interventions", "avg_value", "date", "event_type",
            "timestamp", "is_major_reset", "sensor_type"} <= keys
    missing = keys - _body_literals(schema, "get_historical_graph_payload")
    assert not missing, f"get_historical_graph_payload does not build: {sorted(missing)}"


def test_schema_has_no_stale_schema_additions_reference():
    """app.py and state_store.py used to point at a schema_additions.sql
    that was never committed."""
    for d in PYTHON_DIRS:
        for path in d.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            assert "schema_additions" not in path.read_text(encoding="utf-8"), path.name


# ---------------------------------------------------------------------
# Tests of the checker itself, on synthetic input
# ---------------------------------------------------------------------
SYNTHETIC_SQL = """
-- comment mentioning create table ghost (x int);
create table if not exists public."Pond" (
    id bigint generated by default as identity primary key,
    "userID" bigint not null,
    reading  double precision,
    note     text default 'a, b',
    unique ("userID", reading)
);
create table dropped (id int);
drop table dropped;
alter table public."Pond" add column if not exists added_later text,
                          drop column note;
alter table "Pond" rename column reading to value;
create or replace function public.pond_summary(p_user_id bigint, p_days integer default 7)
returns jsonb language sql as $$ select jsonb_build_object('key', 1) $$;
insert into storage.buckets (id, name, public) values ('frames', 'frames', true) on conflict (id) do nothing;
"""


def test_schema_parser_reads_ddl():
    s = parse_sql(SYNTHETIC_SQL)
    assert set(s.tables) == {"Pond"}
    assert s.tables["Pond"] == {"id", "userID", "value", "added_later"}
    assert s.functions == {"pond_summary": ["p_user_id", "p_days"]}
    assert s.buckets == {"frames"}
    assert "'key'" in s.function_bodies["pond_summary"]


def test_schema_checker_flags_unknown_python_references():
    code = '''
BUCKET = os.environ.get("B", "frames")
def f(user_id):
    supabase.table("Pond").select("id, value, missing_col").eq("userID", user_id).execute()
    supabase.table("Pond").insert({"userID": 1, "typo_col": 2}).execute()
    supabase.table("Pond").upsert({"userID": 1}, on_conflict="userID").execute()
    supabase.table("NoSuchTable").select("*").execute()
    supabase.rpc("pond_summary", {"p_user_id": 1, "p_bad": 2}).execute()
    supabase.storage.from_(BUCKET).upload(path="x", file=b"")
    supabase.storage.from_("nope").upload(path="x", file=b"")
    q = supabase.table("Pond").select("id").eq("userID", 1)
    q = q.gt("late_col", 3)
'''
    problems = undefined_refs(parse_sql(SYNTHETIC_SQL), python_refs(code, "x.py"))
    assert problems == sorted([
        "x.py:4: column Pond.missing_col",
        "x.py:5: column Pond.typo_col",
        "x.py:7: table NoSuchTable",
        "x.py:8: rpc_param pond_summary.p_bad",
        "x.py:10: bucket nope",
        "x.py:12: column Pond.late_col",
    ])


def test_schema_checker_flags_unknown_dart_and_firmware_references():
    dart = '''
static const String tableName = 'Pond';
final Map<String, dynamic> payload = {'userID': 1, 'value': 2};
payload['bad_key'] = 3;
await client.from('Pond').insert(payload);
await client.from(tableName).select('id, "userID", gone').eq('userID', 1);
await client.from('Pond').upsert({'userID': 1, 'nope': 2}).select().single();
await client.rpc('pond_summary', params: {'p_user_id': 1, 'p_wrong': 2});
await client.storage.from('frames').list();
await client.storage
    .from('elsewhere').list();
'''
    problems = undefined_refs(parse_sql(SYNTHETIC_SQL), dart_refs(dart, "x.dart"))
    assert problems == sorted([
        "x.dart:5: column Pond.bad_key",
        "x.dart:6: column Pond.gone",
        "x.dart:7: column Pond.nope",
        "x.dart:8: rpc_param pond_summary.p_wrong",
        "x.dart:11: bucket elsewhere",
    ])

    ino = '''
const char *TABLE = "Pond";
JsonObject a = doc.createNestedObject(); a["value"] = 1; a["unknown"] = 2;
db.insert(TABLE, json, false);
'''
    problems = undefined_refs(parse_sql(SYNTHETIC_SQL), firmware_refs(ino, "x.ino"))
    assert problems == ["x.ino:4: column Pond.unknown"]
