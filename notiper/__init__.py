import functools
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, render_template, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import admin, auth, db, library, service_templates, views, webhooks
from .config import load_config

__version__ = '1.1.0'

log = logging.getLogger(__name__)


def _persistent_secret(data_dir: Path) -> str:
    path = data_dir / 'secret_key'
    if path.exists() and (key := path.read_text().strip()):
        return key
    key = secrets.token_hex(32)
    path.write_text(key)
    path.chmod(0o600)
    return key


def _timezone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Unknown timezone '%s', using UTC", name)
        return timezone.utc


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(load_config())
    app.config.update(overrides or {})

    data_dir = Path(app.config['DATA_DIR'])
    data_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(data_dir, os.W_OK):
        raise SystemExit(
            f'Data directory {data_dir} is not writable for uid {os.getuid()} / gid {os.getgid()}. '
            'Set PUID/PGID to the owner of the mounted folder or fix its permissions.')
    app.config.setdefault('DATABASE', str(data_dir / 'notiper.db'))
    app.secret_key = app.config['SECRET_KEY'] or _persistent_secret(data_dir)
    app.config.update(
        SESSION_COOKIE_NAME='notiper_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=app.config['SECURE_COOKIES'],
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=app.config['MAX_BODY_KB'] * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=365),
    )
    if app.config['TRUST_PROXY']:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    tz = _timezone(app.config['TIMEZONE'])

    @app.template_filter('dt')
    def format_dt(value):
        if not value:
            return '—'
        parsed = datetime.strptime(value, db.TS_FORMAT).replace(tzinfo=timezone.utc)
        return parsed.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')

    @app.template_filter('fromjson')
    def from_json(value):
        try:
            return json.loads(value or '[]')
        except ValueError:
            return []

    @functools.lru_cache(maxsize=64)
    def _static_hash(filename: str) -> str:
        path = Path(app.static_folder) / filename
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return __version__

    def static_url(filename: str) -> str:
        # Content hash in the URL: a new build with changed CSS/JS gets a new URL, unchanged files stay cached.
        return url_for('static', filename=filename, v=_static_hash(filename))

    app.jinja_env.globals['version'] = __version__
    app.jinja_env.globals['static_url'] = static_url

    db.init_app(app)
    auth.init_app(app)
    app.register_blueprint(webhooks.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(library.bp)

    @app.route('/healthz')
    def healthz():
        return {'status': 'ok'}

    @app.errorhandler(HTTPException)
    def http_error(e):
        if request.blueprint == 'webhooks' or request.is_json:
            return {'error': e.description}, e.code
        return render_template('error.html', error=e), e.code

    with app.app_context():
        db.init_schema()
        auth.bootstrap_admin()
        service_templates.seed_builtin()

    return app
