"""Converter validation and persistence."""
import re
import secrets

from . import mapping
from .db import get_db, hours_ago, now

SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
RESERVED_SLUGS = {'log'}

DEFAULTS = {
    'name': '',
    'slug': '',
    'description': '',
    'enabled': 1,
    **mapping.MAPPING_DEFAULTS,
    'token': '',
    'verify_tls': 1,
    'timeout': 10,
}
COLUMNS = tuple(DEFAULTS)


def generate_token() -> str:
    return secrets.token_urlsafe(24)


def normalize(raw: dict) -> tuple[dict, list[str]]:
    data, errors = mapping.normalize(raw, DEFAULTS, bool_fields=('enabled', 'verify_tls'), int_fields=('timeout',))
    data['slug'] = data['slug'].lower()
    if not data['name']:
        data['name'] = data['slug']
    return data, errors


def validate(data: dict, existing_id=None) -> list[str]:
    errors = []
    if not SLUG_RE.match(data['slug']):
        errors.append('Slug must be 1–64 chars: lowercase letters, digits, "-" or "_".')
    elif data['slug'] in RESERVED_SLUGS:
        errors.append(f'Slug "{data["slug"]}" is reserved.')
    else:
        row = get_db().execute('SELECT id FROM converters WHERE slug = ?', (data['slug'],)).fetchone()
        if row and row['id'] != existing_id:
            errors.append(f'Slug "{data["slug"]}" is already used by another converter.')
    if not 1 <= data['timeout'] <= 120:
        errors.append('Timeout must be between 1 and 120 seconds.')
    return errors + mapping.validate(data)


def get(converter_id):
    return get_db().execute('SELECT * FROM converters WHERE id = ?', (converter_id,)).fetchone()


def get_by_slug(slug):
    return get_db().execute('SELECT * FROM converters WHERE slug = ?', (slug,)).fetchone()


def list_with_stats():
    since = hours_ago(24)
    return get_db().execute(
        """SELECT c.*,
                  (SELECT d.success FROM deliveries d WHERE d.converter_id = c.id ORDER BY d.id DESC LIMIT 1)
                      AS last_success,
                  (SELECT d.received_at FROM deliveries d WHERE d.converter_id = c.id ORDER BY d.id DESC LIMIT 1)
                      AS last_at,
                  (SELECT COUNT(*) FROM deliveries d WHERE d.converter_id = c.id AND d.received_at >= ?)
                      AS count_24h,
                  (SELECT COUNT(*) FROM deliveries d
                    WHERE d.converter_id = c.id AND d.received_at >= ? AND d.success = 0) AS failed_24h
           FROM converters c ORDER BY c.name COLLATE NOCASE""",
        (since, since),
    ).fetchall()


def save(data: dict, converter_id=None) -> int:
    db = get_db()
    ts = now()
    values = [data[c] for c in COLUMNS]
    if converter_id is None:
        cur = db.execute(
            f"INSERT INTO converters ({', '.join(COLUMNS)}, created_at, updated_at) "
            f"VALUES ({', '.join('?' * len(COLUMNS))}, ?, ?)",
            (*values, ts, ts),
        )
        converter_id = cur.lastrowid
    else:
        db.execute(
            f"UPDATE converters SET {', '.join(f'{c} = ?' for c in COLUMNS)}, updated_at = ? WHERE id = ?",
            (*values, ts, converter_id),
        )
    db.commit()
    return converter_id


def set_enabled(converter_id, enabled: bool):
    db = get_db()
    db.execute('UPDATE converters SET enabled = ?, updated_at = ? WHERE id = ?', (int(enabled), now(), converter_id))
    db.commit()


def delete(converter_id):
    db = get_db()
    db.execute('DELETE FROM converters WHERE id = ?', (converter_id,))
    db.commit()
