"""Session login, roles, CSRF protection and first-run setup."""
import functools
import logging
import secrets
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, now

log = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)

ROLES = ('admin', 'operator')
MIN_PASSWORD_LENGTH = 8
PUBLIC_BLUEPRINTS = {'webhooks'}
PUBLIC_ENDPOINTS = {'static', 'healthz'}

_FAILURE_WINDOW = 15 * 60
_MAX_FAILURES = 10
_failures: dict[str, deque] = defaultdict(deque)
_failures_lock = threading.Lock()


# --- helpers -------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return generate_password_hash(password)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f'Password must have at least {MIN_PASSWORD_LENGTH} characters.'
    return None


def create_user(username: str, password: str, role: str) -> int:
    db = get_db()
    cur = db.execute(
        'INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)',
        (username, hash_password(password), role, now()),
    )
    db.commit()
    return cur.lastrowid


def user_count() -> int:
    return get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0]


def bootstrap_admin():
    """Create the first admin from env variables when the database has no users yet."""
    username = current_app.config['ADMIN_USERNAME']
    password = current_app.config['ADMIN_PASSWORD']
    if not (username and password) or user_count():
        return
    if problem := password_problem(password):
        log.error('NOTIPER_ADMIN_PASSWORD rejected: %s', problem)
        return
    create_user(username, password, 'admin')
    log.info("Created initial admin user '%s' from environment", username)


def _session_stamp(user) -> str:
    # Changing the password invalidates existing sessions of that user.
    return user['password_hash'][-16:]


def _login(user):
    session.clear()
    session['uid'] = user['id']
    session['stamp'] = _session_stamp(user)
    session.permanent = True
    db = get_db()
    db.execute('UPDATE users SET last_login_at = ? WHERE id = ?', (now(), user['id']))
    db.commit()


def _safe_next(target: str | None) -> str:
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return url_for('ui.index')


def _is_public() -> bool:
    return request.blueprint in PUBLIC_BLUEPRINTS or request.endpoint in PUBLIC_ENDPOINTS


def _throttled(key: str) -> bool:
    with _failures_lock:
        attempts = _failures[key]
        cutoff = time.monotonic() - _FAILURE_WINDOW
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return len(attempts) >= _MAX_FAILURES


def _record_failure(key: str):
    with _failures_lock:
        _failures[key].append(time.monotonic())


def csrf_token() -> str:
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


# --- request hooks -------------------------------------------------------------------------

def _load_user():
    g.user = None
    if _is_public():
        return
    uid = session.get('uid')
    if uid is None:
        return
    user = get_db().execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
    if user and user['active'] and session.get('stamp') == _session_stamp(user):
        g.user = user
    else:
        session.clear()


def _csrf_protect():
    if request.method in ('GET', 'HEAD', 'OPTIONS') or _is_public():
        return
    sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token') or ''
    expected = session.get('csrf', '')
    if not expected or not secrets.compare_digest(sent.encode(), expected.encode()):
        abort(400, description='Invalid or missing CSRF token. Reload the page and try again.')


def _require_setup():
    if _is_public() or request.endpoint == 'auth.setup':
        return
    if not user_count():
        return redirect(url_for('auth.setup'))


def init_app(app):
    app.before_request(_load_user)
    app.before_request(_csrf_protect)
    app.before_request(_require_setup)
    app.jinja_env.globals['csrf_token'] = csrf_token


# --- decorators ----------------------------------------------------------------------------

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.is_json:
                return {'error': 'Not logged in'}, 401
            return redirect(url_for('auth.login', next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user['role'] != 'admin':
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# --- views ---------------------------------------------------------------------------------

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user is not None:
        return redirect(url_for('ui.index'))
    if request.method == 'POST':
        key = request.remote_addr or '?'
        if _throttled(key):
            flash('Too many failed attempts. Try again in a few minutes.', 'error')
            return render_template('login.html'), 429
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = get_db().execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and user['active'] and check_password_hash(user['password_hash'], password):
            _login(user)
            log.info("User '%s' logged in from %s", user['username'], key)
            return redirect(_safe_next(request.args.get('next')))
        _record_failure(key)
        log.warning("Failed login for '%s' from %s", username, key)
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/setup', methods=['GET', 'POST'])
def setup():
    if user_count():
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username:
            flash('Username is required.', 'error')
        elif problem := password_problem(password):
            flash(problem, 'error')
        elif password != request.form.get('password_confirm', ''):
            flash('Passwords do not match.', 'error')
        else:
            create_user(username, password, 'admin')
            user = get_db().execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            _login(user)
            flash('Admin account created. Welcome!', 'success')
            return redirect(url_for('ui.index'))
    return render_template('setup.html')


@bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        if not check_password_hash(g.user['password_hash'], current):
            flash('Current password is wrong.', 'error')
        elif problem := password_problem(new):
            flash(problem, 'error')
        elif new != request.form.get('new_password_confirm', ''):
            flash('Passwords do not match.', 'error')
        else:
            db = get_db()
            db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(new), g.user['id']))
            db.commit()
            _login(db.execute('SELECT * FROM users WHERE id = ?', (g.user['id'],)).fetchone())
            flash('Password changed.', 'success')
            return redirect(url_for('auth.account'))
    return render_template('account.html')
