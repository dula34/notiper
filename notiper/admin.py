"""User management (admin only)."""
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import ROLES, admin_required, create_user, hash_password, password_problem
from .db import get_db

bp = Blueprint('admin', __name__, url_prefix='/users')


def _get_or_404(user_id):
    user = get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user is None:
        abort(404)
    return user


def _other_active_admins(user_id) -> int:
    return get_db().execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?", (user_id,)
    ).fetchone()[0]


@bp.route('/')
@admin_required
def user_list():
    users = get_db().execute('SELECT * FROM users ORDER BY username COLLATE NOCASE').fetchall()
    return render_template('users.html', users=users)


@bp.route('/new', methods=['GET', 'POST'])
@admin_required
def user_new():
    form = {'username': '', 'role': 'operator'}
    if request.method == 'POST':
        form = {'username': request.form.get('username', '').strip(), 'role': request.form.get('role', '')}
        password = request.form.get('password', '')
        if not form['username']:
            flash('Username is required.', 'error')
        elif form['role'] not in ROLES:
            flash('Invalid role.', 'error')
        elif problem := password_problem(password):
            flash(problem, 'error')
        else:
            try:
                create_user(form['username'], password, form['role'])
            except sqlite3.IntegrityError:
                flash('A user with this username already exists.', 'error')
            else:
                flash(f'User "{form["username"]}" created.', 'success')
                return redirect(url_for('admin.user_list'))
    return render_template('user_form.html', user=None, form=form, roles=ROLES)


@bp.route('/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def user_edit(user_id):
    user = _get_or_404(user_id)
    form = {'username': user['username'], 'role': user['role'], 'active': bool(user['active'])}
    if request.method == 'POST':
        form = {'username': user['username'], 'role': request.form.get('role', ''),
                'active': 'active' in request.form}
        password = request.form.get('password', '')
        losing_admin = user['role'] == 'admin' and user['active'] and (form['role'] != 'admin' or not form['active'])
        if form['role'] not in ROLES:
            flash('Invalid role.', 'error')
        elif user['id'] == g.user['id'] and not form['active']:
            flash('You cannot deactivate your own account.', 'error')
        elif losing_admin and not _other_active_admins(user_id):
            flash('At least one active admin must remain.', 'error')
        elif password and (problem := password_problem(password)):
            flash(problem, 'error')
        else:
            db = get_db()
            db.execute('UPDATE users SET role = ?, active = ? WHERE id = ?',
                       (form['role'], int(form['active']), user_id))
            if password:
                db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hash_password(password), user_id))
            db.commit()
            flash('User updated.', 'success')
            return redirect(url_for('admin.user_list'))
    return render_template('user_form.html', user=user, form=form, roles=ROLES)


@bp.route('/<int:user_id>/delete', methods=['POST'])
@admin_required
def user_delete(user_id):
    user = _get_or_404(user_id)
    if user['id'] == g.user['id']:
        flash('You cannot delete your own account.', 'error')
    elif user['role'] == 'admin' and user['active'] and not _other_active_admins(user_id):
        flash('At least one active admin must remain.', 'error')
    else:
        db = get_db()
        db.execute('DELETE FROM users WHERE id = ?', (user_id,))
        db.commit()
        flash(f'User "{user["username"]}" deleted.', 'success')
    return redirect(url_for('admin.user_list'))
