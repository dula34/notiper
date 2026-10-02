import json
import re

import pytest

from notiper import auth, create_app, engine

TEAMS_TEMPLATE = """{
  "@type": "MessageCard",
  "summary": "Unifi Event Notification",
  "sections": [
    {% for event in events -%}
    {
      "activityTitle": "Unifi Event {{ event.id }}",
      "activitySubtitle": {{ event.alert_key | json }},
    }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}"""


class FakeResponse:
    def __init__(self, status=200, text='1'):
        self.status_code = status
        self.text = text
        self.ok = status < 400


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({'method': method, 'url': url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(engine.requests, 'request', fake_request)
    return calls


@pytest.fixture
def app(tmp_path):
    auth._failures.clear()
    return create_app({'DATA_DIR': str(tmp_path), 'TESTING': True, 'LOG_RETENTION': 5})


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path='/login'):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def setup_admin(client):
    token = csrf(client, '/setup')
    resp = client.post('/setup', data={'username': 'admin', 'password': 'secret123', 'password_confirm': 'secret123',
                                       'csrf_token': token})
    assert resp.status_code == 302
    return csrf(client, '/converters')


def create_converter(client, csrf_tok, **overrides):
    data = {'name': 'UniFi', 'slug': 'unifi', 'target_url': 'https://teams.example/hook', 'method': 'POST',
            'body_format': 'json', 'template': TEAMS_TEMPLATE, 'timeout': '10', 'enabled': 'on',
            'verify_tls': 'on', 'csrf_token': csrf_tok, **overrides}
    # unchecked checkboxes are not sent by browsers
    data = {k: v for k, v in data.items() if v is not None}
    return client.post('/converters/new', data=data)


def test_first_run_redirects_to_setup(client):
    assert client.get('/converters').headers['Location'].endswith('/setup')


def test_full_flow(client, sent):
    token = setup_admin(client)
    assert create_converter(client, token).status_code == 302

    resp = client.post('/webhook/unifi', json={'events': [{'id': 1, 'alert_key': 'say "hi"'}, {'id': 2, 'alert_key': 'b'}]})
    assert resp.status_code == 200 and resp.json == {'status': 'sent'}
    body = json.loads(sent[0]['data'])
    assert sent[0]['url'] == 'https://teams.example/hook'
    assert sent[0]['headers']['Content-Type'] == 'application/json'
    assert [s['activitySubtitle'] for s in body['sections']] == ['say "hi"', 'b']

    html = client.get('/deliveries').get_data(as_text=True)
    assert 'HTTP 200' in html


def test_unknown_and_disabled(client, sent):
    token = setup_admin(client)
    create_converter(client, token, enabled=None)
    assert client.post('/webhook/nope', json={}).status_code == 404
    assert client.post('/webhook/unifi', json={}).status_code == 403
    assert not sent


def test_token_required(client, sent):
    token = setup_admin(client)
    create_converter(client, token, token='s3cret')
    assert client.post('/webhook/unifi', json={'events': []}).status_code == 401
    assert client.post('/webhook/unifi?token=s3cret', json={'events': []}).status_code == 200
    assert client.post('/webhook/unifi', json={'events': []}, headers={'X-Notiper-Token': 's3cret'}).status_code == 200


def test_target_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(engine.requests, 'request', lambda *a, **k: FakeResponse(500, 'boom'))
    token = setup_admin(client)
    create_converter(client, token)
    resp = client.post('/webhook/unifi', json={'events': []})
    assert resp.status_code == 502


def test_validation_keeps_input(client):
    token = setup_admin(client)
    resp = create_converter(client, token, slug='Bad Slug!', template='{% for x in %}')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'Slug must be' in html and 'Template:' in html
    assert 'bad slug!' in html


def test_preview(client):
    token = setup_admin(client)
    resp = client.post('/converters/preview', headers={'X-CSRF-Token': token}, json={
        'template': '{"text": {{ msg | json }}}', 'target_url': 'https://x.example/{{ room }}',
        'body_format': 'json', 'method': 'POST', 'headers': '', 'timeout': '10', 'slug': 'x',
        'payload': '{"msg": "hello", "room": "ops"}',
    })
    assert resp.json['ok'], resp.json
    assert resp.json['url'] == 'https://x.example/ops'
    assert json.loads(resp.json['body']) == {'text': 'hello'}


def test_csrf_enforced(client):
    setup_admin(client)
    assert client.post('/converters/new', data={'name': 'x'}).status_code == 400


def test_operator_permissions(client, app, sent):
    token = setup_admin(client)
    create_converter(client, token)
    client.post('/users/new', data={'username': 'op', 'password': 'operator1', 'role': 'operator',
                                    'csrf_token': token})
    client.post('/logout', data={'csrf_token': token})

    op = app.test_client()
    t = csrf(op)
    assert op.post('/login', data={'username': 'op', 'password': 'operator1', 'csrf_token': t}).status_code == 302
    t = csrf(op, '/converters')
    assert op.get('/converters/new').status_code == 403
    assert op.get('/users/').status_code == 403
    assert op.post('/converters/1', data={'csrf_token': t}).status_code == 403
    assert op.get('/converters/1').status_code == 200
    # operator can test with the saved converter, but cannot override its template
    resp = op.post('/converters/send-test', headers={'X-CSRF-Token': t},
                   json={'converter_id': 1, 'payload': '{"events": []}', 'template': 'evil'})
    assert resp.json['ok']
    assert json.loads(sent[0]['data'])['summary'] == 'Unifi Event Notification'


def test_last_admin_protected(client):
    token = setup_admin(client)
    resp = client.post('/users/1', data={'role': 'operator', 'active': 'on', 'csrf_token': token},
                       follow_redirects=True)
    assert 'At least one active admin' in resp.get_data(as_text=True)


def test_retention(client, app, sent):
    token = setup_admin(client)
    create_converter(client, token)
    for _ in range(8):
        client.post('/webhook/unifi', json={'events': []})
    with app.app_context():
        from notiper.db import get_db
        assert get_db().execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 5


def test_login_throttle(client):
    setup_admin(client)
    client.post('/logout', data={'csrf_token': csrf(client, '/converters')})
    for _ in range(10):
        client.post('/login', data={'username': 'admin', 'password': 'wrong', 'csrf_token': csrf(client)})
    resp = client.post('/login', data={'username': 'admin', 'password': 'secret123', 'csrf_token': csrf(client)})
    assert resp.status_code == 429


def test_text_body_and_form_input(client, sent):
    token = setup_admin(client)
    create_converter(client, token, slug='ntfy', body_format='text', template='Alert: {{ title }}',
                     header_name=['Priority'], header_value=['high'])
    assert client.post('/webhook/ntfy', data={'title': 'disk full'}).status_code == 200
    assert sent[0]['data'] == b'Alert: disk full'
    assert sent[0]['headers']['Priority'] == 'high'


def test_query_params_from_payload(client, sent):
    token = setup_admin(client)
    create_converter(client, token, slug='q', target_url='https://ntfy.example/topic?static=1',
                     qp_key=['title', 'prio', 'empty'],
                     qp_value=['{{ events[0].alert_key }}', 'high', '{% if false %}x{% endif %}'])
    client.post('/webhook/q', json={'events': [{'id': 1, 'alert_key': 'wan down & out'}]})
    assert sent[0]['url'] == 'https://ntfy.example/topic?static=1&title=wan+down+%26+out&prio=high'


def test_create_converter_from_template(client):
    token = setup_admin(client)
    html = client.get('/templates/').get_data(as_text=True)
    tid = re.search(r'/converters/new\?from=(\d+)', html).group(1)
    html = client.get(f'/converters/new?from={tid}').get_data(as_text=True)
    assert 'Prefilled from' in html


def test_static_urls_are_content_hashed(client):
    html = client.get('/setup').get_data(as_text=True)
    css = re.search(r'href="(/static/style\.css\?v=[0-9a-f]{12})"', html).group(1)
    resp = client.get(css)
    assert resp.status_code == 200 and resp.cache_control.max_age == 365 * 24 * 3600


def test_header_rows_are_templates(client, sent):
    token = setup_admin(client)
    create_converter(client, token, slug='h',
                     header_name=['Authorization', 'X-Device', 'X-Optional'],
                     header_value=['Bearer {{ request.headers["X-Token"] }}', '{{ device }}', '{{ missing }}'])
    assert client.post('/webhook/h', json={'device': 'nas01', 'events': []}, headers={'X-Token': 'abc'}).status_code == 200
    headers = sent[0]['headers']
    assert headers['X-Device'] == 'nas01'
    assert 'X-Optional' not in headers
    assert headers['Authorization'] == 'Bearer abc'


def test_invalid_header_name(client):
    token = setup_admin(client)
    html = create_converter(client, token, header_name=['Bad Name'], header_value=['x']).get_data(as_text=True)
    assert 'Header name' in html


def test_preview_with_templated_header_keeps_body(client):
    token = setup_admin(client)
    resp = client.post('/converters/preview', headers={'X-CSRF-Token': token}, json={
        'template': '{"text": {{ msg | json }}}', 'target_url': 'https://x.example', 'body_format': 'json',
        'method': 'POST', 'slug': 'x', 'payload': '{"msg": "hello", "key": "abc"}',
        'headers': [{'name': 'X-Key', 'value': '{{ key }}'}],
    })
    assert json.loads(resp.json['body']) == {'text': 'hello'}
    assert resp.json['headers']['X-Key'] == 'abc'


def test_operator_preview_hides_header_values(client, app):
    token = setup_admin(client)
    create_converter(client, token, header_name=['Authorization'], header_value=['Bearer tok-abcdef'])
    client.post('/users/new', data={'username': 'op', 'password': 'operator1', 'role': 'operator',
                                    'csrf_token': token})
    op = app.test_client()
    op.post('/login', data={'username': 'op', 'password': 'operator1', 'csrf_token': csrf(op)})
    t = csrf(op, '/converters')
    assert 'tok-abcdef' not in op.get('/converters/1').get_data(as_text=True)
    resp = op.post('/converters/preview', headers={'X-CSRF-Token': t},
                   json={'converter_id': 1, 'payload': '{"events": []}'})
    assert resp.json['ok'] and resp.json['headers']['Authorization'] == '***'


def test_builtin_templates_valid_and_renderable(client, app):
    setup_admin(client)
    from notiper import converters, service_templates
    with app.app_context():
        items = service_templates.list_all()
        assert len(items) == len(service_templates.BUILTIN)
        for t in items:
            conv = {**converters.DEFAULTS, **dict(t), 'id': None, 'slug': 'x'}
            prepared = engine.prepare(conv, json.loads(t['sample_payload']), {'headers': {}, 'query': {}})
            assert prepared['url'].startswith('http'), t['name']


def test_migration_from_first_release(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / 'notiper.db')
    conn.executescript("""CREATE TABLE converters (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, target_url TEXT NOT NULL,
        method TEXT NOT NULL DEFAULT 'POST', body_format TEXT NOT NULL DEFAULT 'json', headers TEXT NOT NULL DEFAULT '',
        template TEXT NOT NULL, token TEXT NOT NULL DEFAULT '', verify_tls INTEGER NOT NULL DEFAULT 1,
        timeout INTEGER NOT NULL DEFAULT 10, sample_payload TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL);
        INSERT INTO converters (slug, name, target_url, headers, template, created_at, updated_at)
        VALUES ('old', 'Old', 'https://x.example', 'Authorization: Bearer x:y
# comment
Priority: high', '{}', 'x', 'x');""")
    conn.commit()
    conn.close()
    app = create_app({'DATA_DIR': str(tmp_path)})
    with app.app_context():
        from notiper import converters
        row = converters.get_by_slug('old')
        assert row['query_params'] == '[]'
        assert json.loads(row['headers']) == [{'name': 'Authorization', 'value': 'Bearer x:y'},
                                              {'name': 'Priority', 'value': 'high'}]


def test_migration_inlines_prerelease_secrets(tmp_path):
    import sqlite3
    create_app({'DATA_DIR': str(tmp_path)})  # current schema + built-in templates
    conn = sqlite3.connect(tmp_path / 'notiper.db')
    conn.execute("ALTER TABLE converters ADD COLUMN secrets TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        "INSERT INTO converters (slug, name, target_url, headers, query_params, template, secrets, created_at, updated_at) "
        "VALUES ('tg', 'TG', 'https://api.telegram.org/bot{{ secrets.bot }}/send', ?, ?, ?, ?, 'x', 'x')",
        (json.dumps([{'name': 'Authorization', 'value': 'Bearer {{ secrets.tok }}'}]),
         json.dumps([{'key': 'k', 'value': '{% if secrets.tok %}yes{% endif %}'}]),
         '{"chat_id": {{ secrets.chat | json }}}',
         json.dumps([{'name': 'bot', 'value': 'B0T'}, {'name': 'tok', 'value': 'a"b'}, {'name': 'chat', 'value': '42'}])))
    conn.execute("UPDATE service_templates SET headers = ? WHERE name LIKE 'Gotify%'",
                 (json.dumps([{'name': 'X-Gotify-Key', 'value': '{{ secrets.app_token }}'}]),))
    conn.commit()
    conn.close()

    app = create_app({'DATA_DIR': str(tmp_path)})
    with app.app_context():
        from notiper import converters, service_templates
        conv = dict(converters.get_by_slug('tg'))
        assert conv['target_url'] == 'https://api.telegram.org/botB0T/send'
        prepared = engine.prepare(conv, {}, {'headers': {}, 'query': {}})
        assert prepared['headers']['Authorization'] == 'Bearer a"b'
        assert prepared['url'].endswith('?k=yes')
        assert json.loads(prepared['body']) == {'chat_id': '42'}
        gotify = [t for t in service_templates.list_all() if t['name'].startswith('Gotify')][0]
        assert 'secrets' not in gotify['headers']
        engine.prepare({**converters.DEFAULTS, **dict(gotify)}, {}, {'headers': {}, 'query': {}})
