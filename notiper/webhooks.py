"""Public webhook endpoints (no login, optional per-converter token)."""
import json
import logging
import secrets

from flask import Blueprint, jsonify, request

from . import converters, engine

log = logging.getLogger(__name__)

bp = Blueprint('webhooks', __name__, url_prefix='/webhook')

FORM_MIMETYPES = {'application/x-www-form-urlencoded', 'multipart/form-data'}


def _sent_token() -> str:
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return request.headers.get('X-Notiper-Token') or request.args.get('token') or ''


# Handy test target: point a converter at /webhook/log and watch the container log.
@bp.route('/log', methods=['GET', 'POST', 'PUT'])
def webhook_log():
    raw = request.get_data(as_text=True)
    log.info('Log webhook received: %s', raw or dict(request.args))
    return jsonify({'status': 'logged'}), 200


@bp.route('/<slug>', methods=['GET', 'POST', 'PUT'])
def receive(slug):
    conv = converters.get_by_slug(slug)
    if conv is None:
        return jsonify({'error': 'Unknown converter'}), 404
    if not conv['enabled']:
        return jsonify({'error': 'Converter is disabled'}), 403
    if conv['token'] and not secrets.compare_digest(_sent_token().encode(), conv['token'].encode()):
        log.warning("Converter '%s': rejected request from %s (bad token)", slug, request.remote_addr)
        return jsonify({'error': 'Invalid token'}), 401

    if request.mimetype in FORM_MIMETYPES:
        payload = request.form.to_dict()
        raw = json.dumps(payload, ensure_ascii=False)
    else:
        raw = request.get_data(as_text=True)
        payload = engine.parse_incoming(raw)
    log.debug("Converter '%s' received: %s", slug, raw)
    result = engine.deliver(conv, payload, raw, engine.request_meta(request), 'webhook')
    if result['success']:
        return jsonify({'status': 'sent'}), 200
    return jsonify({'error': result['error']}), 502
