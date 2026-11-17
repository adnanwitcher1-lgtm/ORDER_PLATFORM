from django.core.cache import cache

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60


def _key(username, ip):
    return f"login-fail:{ip or 'unknown'}:{(username or '').strip().lower()[:150]}"


def is_locked(username, ip):
    return cache.get(_key(username, ip), 0) >= MAX_ATTEMPTS


def register_failure(username, ip):
    key = _key(username, ip)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, WINDOW_SECONDS)


def clear(username, ip):
    cache.delete(_key(username, ip))