"""Cache resilience tests.

A corrupt cache entry used to raise JSONDecodeError out of the view — which, with
the Werkzeug debugger enabled, turned a truncated file into an RCE surface.
"""

import json
import os
import threading

import pytest

from gw2 import cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    yield


def test_roundtrip():
    cache.set("k", {"a": 1})
    assert cache.get("k", ttl=60) == {"a": 1}


def test_miss_returns_none():
    assert cache.get("nope", ttl=60) is None


def test_expired_entry_is_a_miss():
    cache.set("k", {"a": 1})
    os.utime(cache._path("k"), (0, 0))
    assert cache.get("k", ttl=60) is None


def test_stale_entry_is_readable_with_a_longer_ttl():
    """Stale data beats a 500 page when the API is down."""
    cache.set("k", {"a": 1})
    os.utime(cache._path("k"), (0, 0))
    assert cache.get("k", ttl=60) is None
    assert cache.get("k", ttl=10**12) == {"a": 1}


def test_truncated_cache_is_a_miss_not_an_exception():
    p = cache._path("item:1")
    with open(p, "w") as f:
        f.write('{"v": {"id": 1, "na')
    assert cache.get("item:1", ttl=3600) is None  # must not raise


def test_corrupt_entry_is_removed_so_it_cannot_poison_the_whole_ttl():
    p = cache._path("item:1")
    with open(p, "w") as f:
        f.write("not json at all")
    cache.get("item:1", ttl=3600)
    assert not os.path.exists(p)


def test_legacy_bare_format_is_discarded_cleanly():
    """Pre-envelope files must degrade to a miss, not a KeyError."""
    with open(cache._path("k"), "w") as f:
        json.dump({"id": 1}, f)
    assert cache.get("k", ttl=3600) is None


def test_cached_null_is_distinguishable_from_a_miss():
    cache.set("k", None)
    assert cache.get("k", ttl=60) is None  # both None, but no re-fetch loop
    assert os.path.exists(cache._path("k"))


def test_cache_files_are_owner_only():
    cache.set("item:1", {"id": 1})
    assert os.stat(cache._path("item:1")).st_mode & 0o077 == 0


def test_writes_are_atomic_under_concurrency():
    """Readers must see the old value or the new one — never a partial file."""
    cache.set("k", {"n": 0})
    errors = []

    def writer():
        for i in range(200):
            try:
                cache.set("k", {"n": i, "pad": "x" * 5000})
            except Exception as e:  # pragma: no cover
                errors.append(e)

    def reader():
        for _ in range(200):
            try:
                v = cache.get("k", ttl=10**9)
                assert v is None or "n" in v
            except Exception as e:  # pragma: no cover
                errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_no_temp_files_left_behind():
    cache.set("k", {"a": 1})
    assert not [n for n in os.listdir(cache.CACHE_DIR) if n.endswith(".tmp")]


def test_prune_removes_only_old_entries():
    cache.set("old", {"a": 1})
    cache.set("new", {"a": 2})
    os.utime(cache._path("old"), (0, 0))
    assert cache.prune(max_age=3600) == 1
    assert cache.get("new", ttl=60) == {"a": 2}


def test_bust_is_idempotent():
    cache.set("k", 1)
    cache.bust("k")
    cache.bust("k")  # must not raise on a missing file
    assert cache.get("k", ttl=60) is None
