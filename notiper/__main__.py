"""Entry point.

    python -m notiper                       start the server
    python -m notiper reset-password USER   set a new password (and re-activate the user)
    python -m notiper create-admin USER     create an admin user
"""
import getpass
import logging
import os
import sys

from waitress import serve

from . import __version__, create_app


def _ask_password() -> str:
    password = os.environ.get('NOTIPER_NEW_PASSWORD') or getpass.getpass('New password: ')
    if not os.environ.get('NOTIPER_NEW_PASSWORD') and password != getpass.getpass('Repeat password: '):
        sys.exit('Passwords do not match.')
    return password


def _drop_root():
    # `docker exec` runs as root; files created by the CLI must stay owned by the app user.
    if os.name == 'posix' and os.geteuid() == 0:
        os.setgid(int(os.environ.get('PGID', '1000')))
        os.setuid(int(os.environ.get('PUID', '1000')))


def _cli(app, command: str, username: str) -> int:
    from .auth import create_user, hash_password, password_problem
    from .db import get_db

    with app.app_context():
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if command == 'create-admin' and user is not None:
            sys.exit(f"User '{username}' already exists, use reset-password.")
        if command == 'reset-password' and user is None:
            sys.exit(f"User '{username}' does not exist.")
        password = _ask_password()
        if problem := password_problem(password):
            sys.exit(problem)
        if command == 'create-admin':
            create_user(username, password, 'admin')
        else:
            db.execute('UPDATE users SET password_hash = ?, active = 1 WHERE id = ?',
                       (hash_password(password), user['id']))
            db.commit()
    print('Done.')
    return 0


def main() -> int:
    if sys.argv[1:]:
        _drop_root()
    level = getattr(logging, os.environ.get('NOTIPER_LOG_LEVEL', 'info').upper(), logging.INFO)
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)-7s %(name)s: %(message)s')
    app = create_app()
    logging.getLogger('waitress').setLevel(max(level, logging.INFO))

    args = sys.argv[1:]
    if args:
        if len(args) != 2 or args[0] not in ('reset-password', 'create-admin'):
            sys.exit(__doc__)
        return _cli(app, *args)

    logging.getLogger('notiper').info('Notiper %s is UP on %s:%s (uid %s, gid %s)', __version__,
                                      app.config['HOST'], app.config['PORT'], os.getuid(), os.getgid())
    serve(app, host=app.config['HOST'], port=app.config['PORT'], threads=app.config['THREADS'], ident='notiper')
    return 0


if __name__ == '__main__':
    sys.exit(main())
