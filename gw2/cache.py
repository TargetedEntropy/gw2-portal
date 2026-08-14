import hashlib
import json
import logging
import os
import tempfile
import time

log = logging.getLogger(__name__)

CACHE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".cache"))
DEFAULT_TTL = 300  # 5 minutes

# The cache holds bank, wallet and inventory contents, so it is owner-only.
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
os.chmod(CACHE_DIR, 0o700)  # makedirs won't tighten an existing directory


def _path(key: str) -> str:
    digest = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, digest + ".json")


class _Miss:
    """Sentinel for 'not cached', so a cached null isn't mistaken for a miss.

    Without this, an id the API legitimately omits (an untradeable item has no
    /commerce/prices entry) caches as None, reads back as a miss, and is re-fetched
    on every single page load.
    """

    def __repr__(self):
        return "<cache.MISS>"

    def __bool__(self):
        return False


MISS = _Miss()


def get(key: str, ttl: int = DEFAULT_TTL, default=None):
    """Return the cached value, or `default` on miss/expiry/corruption."""
    p = _path(key)
    try:
        if time.time() - os.path.getmtime(p) > ttl:
            return default
        with open(p) as f:
            return json.load(f)["v"]
    except (FileNotFoundError, OSError):
        return default
    except (json.JSONDecodeError, KeyError, TypeError):
        # A truncated or pre-envelope file must never take down a request.
        log.warning("discarding unreadable cache entry for %r", key)
        try:
            os.unlink(p)
        except OSError:
            pass
        return default


def age(key: str) -> float | None:
    """Seconds since the entry was written, or None if it does not exist."""
    try:
        return time.time() - os.path.getmtime(_path(key))
    except OSError:
        return None


def set(key: str, value):
    p = _path(key)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"v": value}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)  # atomic: readers see the old file or the new one
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def bust(key: str):
    try:
        os.unlink(_path(key))
    except OSError:
        pass


def prune(max_age: int = 86400 * 7) -> int:
    """Delete entries older than max_age. Returns the number removed."""
    now = time.time()
    removed = 0
    for name in os.listdir(CACHE_DIR):
        p = os.path.join(CACHE_DIR, name)
        try:
            if now - os.path.getmtime(p) > max_age:
                os.unlink(p)
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("pruned %d expired cache entries", removed)
    return removed


def stats() -> dict:
    count = total = 0
    for name in os.listdir(CACHE_DIR):
        try:
            total += os.path.getsize(os.path.join(CACHE_DIR, name))
            count += 1
        except OSError:
            pass
    return {"entries": count, "bytes": total}
