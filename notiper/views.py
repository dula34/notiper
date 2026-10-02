"""UI for converters (mapping) and the delivery log."""
import json

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import converters, engine, mapping, service_templates
from .auth import admin_required, login_required
from .db import get_db

bp = Blueprint('ui', __name__)

PAGE_SIZE = 50
TEST_OVERRIDABLE = ('template', 'target_url', 'body_format', 'method', 'headers', 'query_params', 'timeout',
                    'verify_tls', 'slug')
VISIBLE_HEADERS = {'content-type', 'user-agent'}


def webhook_url(slug: str) -> str:
    base = current_app.config['PUBLIC_URL'] or request.host_url.rstrip('/')
    return f'{base}/webhook/{slug}'


def _form_data() -> dict:
    raw = request.form.to_dict()
    raw['enabled'] = 'enabled' in request.form
    raw['verify_tls'] = 'verify_tls' in request.form
    raw.update(mapping.pairs_from_form(request.form))
    return raw


def _get_or_404(converter_id):
    conv = converters.get(converter_id)
    if conv is None:
        abort(404)
    return conv


@bp.route('/')
@login_required
def index():
    return redirect(url_for('ui.converter_list'))


# --- converters ----------------------------------------------------------------------------

@bp.route('/converters')
@login_required
def converter_list():
    return render_template('converters.html', items=converters.list_with_stats(), webhook_url=webhook_url)


@bp.route('/converters/new', methods=['GET', 'POST'])
@admin_required
def converter_new():
    conv = {**converters.DEFAULTS, 'id': None}
    errors = []
    from_template = service_templates.get(request.args.get('from', type=int)) if request.args.get('from') else None
    if from_template is not None:
        conv.update({k: from_template[k] for k in service_templates.CONVERTER_FIELDS})
    if request.method == 'POST':
        conv, errors = converters.normalize(_form_data())
        conv['id'] = None
        errors += converters.validate(conv)
        if not errors:
            new_id = converters.save(conv)
            flash(f'Converter "{conv["name"]}" created.', 'success')
            return redirect(url_for('ui.converter_edit', converter_id=new_id))
    return render_template('converter_form.html', conv=conv, errors=errors, webhook_url=None,
                           service_templates=service_templates.list_all(), from_template=from_template)


@bp.route('/converters/<int:converter_id>', methods=['GET', 'POST'])
@login_required
def converter_edit(converter_id):
    conv = dict(_get_or_404(converter_id))
    errors = []
    if request.method == 'POST':
        if g.user['role'] != 'admin':
            abort(403)
        conv, errors = converters.normalize(_form_data())
        conv['id'] = converter_id
        errors += converters.validate(conv, existing_id=converter_id)
        if not errors:
            converters.save(conv, converter_id)
            flash('Converter saved.', 'success')
            return redirect(url_for('ui.converter_edit', converter_id=converter_id))
    return render_template('converter_form.html', conv=conv, errors=errors, webhook_url=webhook_url(conv['slug']))


@bp.route('/converters/<int:converter_id>/toggle', methods=['POST'])
@admin_required
def converter_toggle(converter_id):
    conv = _get_or_404(converter_id)
    converters.set_enabled(converter_id, not conv['enabled'])
    flash(f'Converter "{conv["name"]}" {"disabled" if conv["enabled"] else "enabled"}.', 'success')
    return redirect(request.referrer or url_for('ui.converter_list'))


@bp.route('/converters/<int:converter_id>/delete', methods=['POST'])
@admin_required
def converter_delete(converter_id):
    conv = _get_or_404(converter_id)
    converters.delete(converter_id)
    flash(f'Converter "{conv["name"]}" deleted.', 'success')
    return redirect(url_for('ui.converter_list'))


@bp.route('/converters/<int:converter_id>/duplicate', methods=['POST'])
@admin_required
def converter_duplicate(converter_id):
    conv = dict(_get_or_404(converter_id))
    base = conv['slug']
    for i in range(2, 100):
        slug = f'{base}-copy' if i == 2 else f'{base}-copy{i}'
        if converters.get_by_slug(slug) is None and len(slug) <= 64:
            break
    data, _ = converters.normalize({**conv, 'slug': slug, 'name': f'{conv["name"]} (copy)', 'enabled': False,
                                     'token': converters.generate_token() if conv['token'] else ''})
    new_id = converters.save(data)
    flash('Converter duplicated (disabled until you enable it).', 'success')
    return redirect(url_for('ui.converter_edit', converter_id=new_id))


@bp.route('/converters/<int:converter_id>/save-as-template', methods=['POST'])
@admin_required
def converter_save_as_template(converter_id):
    conv = _get_or_404(converter_id)
    data, _ = service_templates.normalize({
        **{k: conv[k] for k in service_templates.CONVERTER_FIELDS},
        'name': service_templates.unique_name(conv['name']),
        'description': f'Created from converter "{conv["name"]}"',
    })
    new_id = service_templates.save(data)
    flash('Template created from the converter.', 'success')
    return redirect(url_for('library.template_edit', template_id=new_id))


@bp.route('/converters/<int:converter_id>/last-payload')
@login_required
def converter_last_payload(converter_id):
    row = get_db().execute(
        "SELECT request_body FROM deliveries WHERE converter_id = ? AND kind = 'webhook' ORDER BY id DESC LIMIT 1",
        (converter_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'No webhook received for this converter yet.'}), 404
    return jsonify({'payload': _pretty(row['request_body'])})


# --- test panel (preview / send) ------------------------------------------------------------

def _test_converter(data: dict) -> dict:
    """Saved converter, with unsaved form values applied when the user is an admin."""
    converter_id = data.get('converter_id') or None
    saved = _get_or_404(converter_id) if converter_id else None
    if saved is None and g.user['role'] != 'admin':
        abort(403)
    merged = {k: (saved[k] if saved else v) for k, v in converters.DEFAULTS.items()}
    if g.user['role'] == 'admin':
        merged.update({k: data[k] for k in TEST_OVERRIDABLE if k in data})
    conv, errors = converters.normalize(merged)
    if errors:
        raise engine.RenderError(errors[0])
    conv['id'] = saved['id'] if saved else None
    conv['slug'] = conv['slug'] or '(unsaved)'
    return conv


def _test_payload(data: dict):
    text = data.get('payload') or ''
    if not text.strip():
        return {}, ''
    try:
        return json.loads(text), text
    except ValueError as e:
        raise engine.RenderError(f'Sample payload is not valid JSON: {e}') from e


def _test_meta() -> dict:
    return {'method': 'POST', 'headers': {}, 'query': {}, 'remote_addr': request.remote_addr}


@bp.route('/converters/preview', methods=['POST'])
@login_required
def converter_preview():
    data = request.get_json(silent=True) or {}
    try:
        conv = _test_converter(data)
        payload, _ = _test_payload(data)
        prepared = engine.prepare(conv, payload, _test_meta())
    except engine.RenderError as e:
        return jsonify({'ok': False, 'error': str(e)})
    if prepared['value'] is not None:
        body = json.dumps(prepared['value'], indent=2, ensure_ascii=False, default=str)
    else:
        body = prepared['rendered']
    url = prepared['url']
    headers = dict(prepared['headers'])
    if g.user['role'] != 'admin':
        # Header values often carry credentials; operators only see the names.
        headers = {k: (v if k.lower() in VISIBLE_HEADERS else '***') for k, v in headers.items()}
    return jsonify({'ok': True, 'url': url, 'method': conv['method'], 'body': body, 'headers': headers,
                    'content_type': headers.get('Content-Type')})


@bp.route('/converters/send-test', methods=['POST'])
@login_required
def converter_send_test():
    data = request.get_json(silent=True) or {}
    try:
        conv = _test_converter(data)
        payload, raw = _test_payload(data)
    except engine.RenderError as e:
        return jsonify({'ok': False, 'error': str(e)})
    result = engine.deliver(conv, payload, raw, _test_meta(), 'test', g.user['id'])
    return jsonify({'ok': result['success'], 'status': result['status'], 'error': result['error'],
                    'response': result['response'], 'duration_ms': result['duration_ms'],
                    'delivery_url': url_for('ui.delivery_detail', delivery_id=result['delivery_id'])})


# --- delivery log --------------------------------------------------------------------------

def _pretty(text):
    if not text:
        return text
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return text


@bp.route('/deliveries')
@login_required
def delivery_list():
    converter_id = request.args.get('converter', type=int)
    status = request.args.get('status', '')
    kind = request.args.get('kind', '')
    page = max(1, request.args.get('page', 1, type=int))

    where, params = [], []
    if converter_id:
        where.append('d.converter_id = ?')
        params.append(converter_id)
    if status in ('ok', 'failed'):
        where.append('d.success = ?')
        params.append(1 if status == 'ok' else 0)
    if kind in ('webhook', 'test', 'replay'):
        where.append('d.kind = ?')
        params.append(kind)
    sql = ('SELECT d.id, d.converter_id, d.slug, d.kind, d.received_at, d.source_ip, d.target_status, d.success, '
           'd.error, d.duration_ms, c.name AS converter_name '
           'FROM deliveries d LEFT JOIN converters c ON c.id = d.converter_id')
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY d.id DESC LIMIT ? OFFSET ?'
    rows = get_db().execute(sql, (*params, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)).fetchall()
    all_converters = get_db().execute('SELECT id, name FROM converters ORDER BY name COLLATE NOCASE').fetchall()
    filters = {'converter': converter_id or '', 'status': status, 'kind': kind}
    return render_template('deliveries.html', rows=rows[:PAGE_SIZE], page=page, has_next=len(rows) > PAGE_SIZE,
                           converters=all_converters, filters=filters)


@bp.route('/deliveries/<int:delivery_id>')
@login_required
def delivery_detail(delivery_id):
    row = get_db().execute(
        'SELECT d.*, c.name AS converter_name, u.username FROM deliveries d '
        'LEFT JOIN converters c ON c.id = d.converter_id LEFT JOIN users u ON u.id = d.user_id WHERE d.id = ?',
        (delivery_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return render_template('delivery.html', d=row, request_body=_pretty(row['request_body']),
                           request_meta=_pretty(row['request_meta']), rendered=row['rendered'],
                           response_body=_pretty(row['response_body']))


@bp.route('/deliveries/<int:delivery_id>/replay', methods=['POST'])
@login_required
def delivery_replay(delivery_id):
    row = get_db().execute('SELECT * FROM deliveries WHERE id = ?', (delivery_id,)).fetchone()
    if row is None:
        abort(404)
    conv = converters.get(row['converter_id']) if row['converter_id'] else None
    if conv is None:
        flash('The converter of this delivery no longer exists.', 'error')
        return redirect(url_for('ui.delivery_detail', delivery_id=delivery_id))
    raw = row['request_body'] or ''
    meta = json.loads(row['request_meta']) if row['request_meta'] else _test_meta()
    result = engine.deliver(conv, engine.parse_incoming(raw), raw, meta, 'replay', g.user['id'])
    flash('Replayed successfully.' if result['success'] else f'Replay failed: {result["error"]}',
          'success' if result['success'] else 'error')
    return redirect(url_for('ui.delivery_detail', delivery_id=result['delivery_id']))


@bp.route('/deliveries/clear', methods=['POST'])
@admin_required
def delivery_clear():
    db = get_db()
    db.execute('DELETE FROM deliveries')
    db.commit()
    flash('Delivery log cleared.', 'success')
    return redirect(url_for('ui.delivery_list'))
