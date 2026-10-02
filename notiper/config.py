import os


def _bool(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def load_config() -> dict:
    env = os.environ.get
    return {
        'DATA_DIR': env('NOTIPER_DATA_DIR', '/data'),
        'SECRET_KEY': env('NOTIPER_SECRET_KEY') or None,
        'ADMIN_USERNAME': env('NOTIPER_ADMIN_USERNAME') or None,
        'ADMIN_PASSWORD': env('NOTIPER_ADMIN_PASSWORD') or None,
        'LOG_LEVEL': env('NOTIPER_LOG_LEVEL', 'info'),
        'HOST': env('NOTIPER_HOST', '0.0.0.0'),
        'PORT': int(env('NOTIPER_PORT', '12353')),
        'THREADS': int(env('NOTIPER_THREADS', '8')),
        'LOG_RETENTION': int(env('NOTIPER_LOG_RETENTION', '1000')),
        'TRUST_PROXY': _bool(env('NOTIPER_TRUST_PROXY', '0')),
        'SECURE_COOKIES': _bool(env('NOTIPER_SECURE_COOKIES', '0')),
        'PUBLIC_URL': env('NOTIPER_PUBLIC_URL', '').rstrip('/'),
        'TIMEZONE': env('TZ', 'UTC'),
        'MAX_BODY_KB': int(env('NOTIPER_MAX_BODY_KB', '1024')),
    }
