"""Rendering of incoming payloads through converter templates and delivery to the target."""
import functools
import json
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import yaml
from flask import current_app
from jinja2 import TemplateError, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from requests.structures import CaseInsensitiveDict

from .db import get_db, now

log = logging.getLogger(__name__)

MAX_STORED_BODY = 64 * 1024
MAX_STORED_RESPONSE = 4000
REDACTED_HEADERS = {'authorization', 'cookie', 'x-notiper-token', 'proxy-authorization'}

_env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=True)
_env.filters['json'] = lambda value: json.dumps(value, ensure_ascii=False, default=str)


class RenderError(Exception):
    pass


@functools.lru_cache(maxsize=256)
def _compile(source: str):
    return _env.from_string(source)


def check_template(source: str) -> str | None:
    """Return a human readable syntax error, or None when the template compiles."""
    try:
        _compile(source)
    except TemplateSyntaxError as e:
        return f'line {e.lineno}: {e.message}'
    return None


def render(source: str, context: dict) -> str:
    try:
        return _compile(source).render(context)
    except TemplateSyntaxError as e:
        raise RenderError(f'Template syntax error on line {e.lineno}: {e.message}') from e
    except TemplateError as e:
        raise RenderError(f'Template error: {e}') from e
    except Exception as e:
        raise RenderError(f'Template error: {type(e).__name__}: {e}') from e


def build_context(payload, meta: dict) -> dict:
    # Top-level payload keys are exposed directly, plus `payload` (the whole body)
    # and `request` (headers, query, method of the incoming request).
    ctx = dict(payload) if isinstance(payload, dict) else {}
    ctx['payload'] = payload
    ctx['request'] = meta
    return ctx


def add_query_params(url: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + params
    return urlunsplit(parts._replace(query=urlencode(query)))


def render_pairs(raw: str, key_field: str, label: str, context: dict) -> list[tuple[str, str]]:
    """Render each value of a header / query parameter list; entries rendering to '' are left out."""
    rendered = []
    for item in json.loads(raw or '[]'):
        try:
            value = render(item['value'], context).strip()
        except RenderError as e:
            raise RenderError(f'{label} "{item[key_field]}": {e}') from e
        if value:
            rendered.append((item[key_field], value))
    return rendered


def encode_body(rendered: str, body_format: str) -> tuple[bytes, str, object]:
    """Turn rendered template output into request body bytes, content type and parsed value."""
    if body_format == 'text':
        return rendered.encode(), 'text/plain; charset=utf-8', None
    stripped = rendered.strip()
    if not stripped:
        raise RenderError('Template rendered an empty body')
    try:
        value = json.loads(stripped)
    except ValueError:
        # Same as the original addon: YAML is a superset of JSON and tolerates trailing commas.
        try:
            value = yaml.safe_load(stripped)
        except yaml.YAMLError as e:
            raise RenderError(f'Rendered output is not valid JSON/YAML: {e}') from e
    if not isinstance(value, (dict, list)):
        raise RenderError('Rendered output must be a JSON object or array (or switch body format to "text")')
    body = json.dumps(value, ensure_ascii=False, default=str).encode()
    return body, 'application/json', value


def parse_incoming(raw: str):
    """Incoming body as JSON when possible, otherwise the raw text."""
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def request_meta(req) -> dict:
    headers = {k: ('***' if k.lower() in REDACTED_HEADERS else v) for k, v in req.headers.items()}
    query = {k: v for k, v in req.args.items() if k != 'token'}
    return {'method': req.method, 'headers': headers, 'query': query, 'remote_addr': req.remote_addr}


def prepare(conv, payload, meta: dict) -> dict:
    """Render a converter for a payload without sending anything."""
    ctx = build_context(payload, meta)
    rendered = render(conv['template'], ctx)
    url = render(conv['target_url'], ctx).strip()
    if not url.startswith(('http://', 'https://')):
        raise RenderError(f'Target URL must start with http:// or https:// (got "{url}")')
    url = add_query_params(url, render_pairs(conv['query_params'], 'key', 'Query parameter', ctx))
    body, content_type, value = encode_body(rendered, conv['body_format'])
    headers = CaseInsensitiveDict({'Content-Type': content_type, 'User-Agent': 'notiper'})
    headers.update(render_pairs(conv['headers'], 'name', 'Header', ctx))
    return {'url': url, 'rendered': rendered, 'body': body, 'value': value, 'headers': headers}


def deliver(conv, payload, raw_body: str, meta: dict, kind: str, user_id=None) -> dict:
    started = time.monotonic()
    result = {'success': False, 'status': None, 'url': None, 'rendered': None, 'response': None, 'error': None}
    try:
        prepared = prepare(conv, payload, meta)
        result['url'] = prepared['url']
        result['rendered'] = prepared['rendered']
        resp = requests.request(
            conv['method'],
            prepared['url'],
            data=prepared['body'],
            headers=prepared['headers'],
            timeout=conv['timeout'],
            verify=bool(conv['verify_tls']),
        )
        result['status'] = resp.status_code
        result['response'] = resp.text[:MAX_STORED_RESPONSE]
        result['success'] = resp.ok
        if not resp.ok:
            result['error'] = f'Target responded with HTTP {resp.status_code}'
    except RenderError as e:
        result['error'] = str(e)
    except requests.RequestException as e:
        result['error'] = f'Request to target failed: {e}'
    result['duration_ms'] = int((time.monotonic() - started) * 1000)

    level = logging.INFO if result['success'] else logging.WARNING
    log.log(level, "Converter '%s' (%s): %s", conv['slug'], kind,
            f"sent, HTTP {result['status']}" if result['success'] else result['error'])

    result['delivery_id'] = record_delivery(conv, raw_body, meta, kind, result, user_id)
    return result


def record_delivery(conv, raw_body: str, meta: dict, kind: str, result: dict, user_id=None) -> int:
    db = get_db()
    cur = db.execute(
        """INSERT INTO deliveries (converter_id, slug, kind, received_at, source_ip, request_body, request_meta,
                                   target_url, rendered, target_status, response_body, success, error, duration_ms,
                                   user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (conv['id'], conv['slug'], kind, now(), meta.get('remote_addr'), (raw_body or '')[:MAX_STORED_BODY],
         json.dumps(meta, ensure_ascii=False), result['url'], result['rendered'], result['status'],
         result['response'], int(result['success']), result['error'], result['duration_ms'], user_id),
    )
    retention = current_app.config['LOG_RETENTION']
    db.execute(
        'DELETE FROM deliveries WHERE id <= (SELECT id FROM deliveries ORDER BY id DESC LIMIT 1 OFFSET ?)',
        (retention,),
    )
    db.commit()
    return cur.lastrowid
