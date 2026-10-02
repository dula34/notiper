import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g

TS_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'operator')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS converters (
    id             INTEGER PRIMARY KEY,
    slug           TEXT    NOT NULL UNIQUE,
    name           TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    enabled        INTEGER NOT NULL DEFAULT 1,
    target_url     TEXT    NOT NULL,
    method         TEXT    NOT NULL DEFAULT 'POST',
    body_format    TEXT    NOT NULL DEFAULT 'json',
    headers        TEXT    NOT NULL DEFAULT '[]',
    query_params   TEXT    NOT NULL DEFAULT '[]',
    template       TEXT    NOT NULL,
    token          TEXT    NOT NULL DEFAULT '',
    verify_tls     INTEGER NOT NULL DEFAULT 1,
    timeout        INTEGER NOT NULL DEFAULT 10,
    sample_payload TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id            INTEGER PRIMARY KEY,
    converter_id  INTEGER REFERENCES converters (id) ON DELETE SET NULL,
    slug          TEXT    NOT NULL,
    kind          TEXT    NOT NULL,
    received_at   TEXT    NOT NULL,
    source_ip     TEXT,
    request_body  TEXT,
    request_meta  TEXT,
    target_url    TEXT,
    rendered      TEXT,
    target_status INTEGER,
    response_body TEXT,
    success       INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    duration_ms   INTEGER,
    user_id       INTEGER
);

CREATE INDEX IF NOT EXISTS ix_deliveries_converter ON deliveries (converter_id, id);

CREATE TABLE IF NOT EXISTS service_templates (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    service        TEXT    NOT NULL DEFAULT '',
    description    TEXT    NOT NULL DEFAULT '',
    target_url     TEXT    NOT NULL,
    method         TEXT    NOT NULL DEFAULT 'POST',
    body_format    TEXT    NOT NULL DEFAULT 'json',
    headers        TEXT    NOT NULL DEFAULT '[]',
    query_params   TEXT    NOT NULL DEFAULT '[]',
    template       TEXT    NOT NULL,
    sample_payload TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ('converters', 'query_params', "TEXT NOT NULL DEFAULT '[]'"),
]


def now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(TS_FORMAT)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def close_db(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def init_schema():
    conn = get_db()
    conn.execute('PRAGMA journal_mode = WAL')
    conn.executescript(SCHEMA)
    for table, column, definition in MIGRATIONS:
        existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        if column not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    _migrate_header_lines(conn)
    _migrate_secrets(conn)
    conn.commit()


def _migrate_header_lines(conn):
    # The first release stored headers as "Name: value" lines; they are now a JSON list of rows.
    from .mapping import headers_text_to_json
    for table in ('converters', 'service_templates'):
        rows = conn.execute(f"SELECT id, headers FROM {table} WHERE headers NOT LIKE '[%'").fetchall()
        for row in rows:
            conn.execute(f'UPDATE {table} SET headers = ? WHERE id = ?', (headers_text_to_json(row['headers']), row['id']))


def get_meta(key: str, default=None):
    row = get_db().execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_meta(key: str, value: str):
    db = get_db()
    db.execute('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value',
               (key, value))
    db.commit()


_SECRET_OUTPUT_RE = re.compile(r'\{\{\s*secrets\.(\w+)\s*(\|\s*json\s*)?\}\}')
_SECRET_REF_RE = re.compile(r'\bsecrets\.(\w+)')
_SECRET_FIELDS = ('target_url', 'headers', 'query_params', 'template')


def _inline_secrets(text: str, values: dict, in_json: bool = False) -> str:
    def output(m):
        value = values.get(m.group(1), '')
        if m.group(2):
            return json.dumps(value, ensure_ascii=False)
        # Inside a JSON-encoded field (headers / query params) the value must stay JSON-escaped.
        return json.dumps(value, ensure_ascii=False)[1:-1] if in_json else value

    def reference(m):
        literal = json.dumps(values.get(m.group(1), ''), ensure_ascii=False)
        return json.dumps(literal, ensure_ascii=False)[1:-1] if in_json else literal

    return _SECRET_REF_RE.sub(reference, _SECRET_OUTPUT_RE.sub(output, text))


def _migrate_secrets(conn):
    """A pre-release had per-converter `secrets`; inline their values where they were referenced."""
    from .service_templates import builtin_row
    for table in ('converters', 'service_templates'):
        columns = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        has_values = 'secrets' in columns
        rows = conn.execute(f"SELECT * FROM {table} WHERE {' OR '.join(f'{c} LIKE ?' for c in _SECRET_FIELDS)}",
                            ['%secrets.%'] * len(_SECRET_FIELDS)).fetchall()
        for row in rows:
            builtin = builtin_row(row['name']) if table == 'service_templates' else None
            if builtin:
                updates = {c: builtin[c] for c in _SECRET_FIELDS}
                updates['description'] = builtin['description']
            else:
                values = {}
                if has_values:
                    values = {s['name']: s['value'] for s in json.loads(row['secrets'] or '[]')}
                updates = {c: _inline_secrets(row[c], values, in_json=c in ('headers', 'query_params'))
                           for c in _SECRET_FIELDS}
            conn.execute(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in updates)} WHERE id = ?",
                         (*updates.values(), row['id']))
