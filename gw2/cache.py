import hashlib
import json
import os
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
DEFAULT_TTL = 300  # 5 minutes


def _path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    digest = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, digest + ".json")


def get(key: str, ttl: int = DEFAULT_TTL):
    p = _path(key)
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > ttl:
        return None
    with open(p) as f:
        return json.load(f)


def set(key: str, value):
    with open(_path(key), "w") as f:
        json.dump(value, f)


def bust(key: str):
    p = _path(key)
    if os.path.exists(p):
        os.remove(p)
