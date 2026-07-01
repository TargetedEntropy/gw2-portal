import requests
from . import cache

BASE = "https://api.guildwars2.com/v2"
_api_key = None


def init(api_key: str):
    global _api_key
    _api_key = api_key


def _headers():
    return {"Authorization": f"Bearer {_api_key}"}


def _get(path: str, params: dict = None, ttl: int = 300, authenticated: bool = True):
    key = path + str(sorted((params or {}).items()))
    cached = cache.get(key, ttl)
    if cached is not None:
        return cached

    headers = _headers() if authenticated else {}
    r = requests.get(BASE + path, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    cache.set(key, data)
    return data


def account():
    return _get("/account", ttl=60)


def wallet():
    return _get("/account/wallet", ttl=60)


def characters():
    return _get("/characters", ttl=120)


def character(name: str):
    return _get(f"/characters/{requests.utils.quote(name)}", ttl=120)


def bank():
    return _get("/account/bank", ttl=60)


def materials():
    return _get("/account/materials", ttl=60)


def items_bulk(ids: list[int]) -> dict[int, dict]:
    """Fetch item details in chunks of 200. Returns {id: item_detail}."""
    result = {}
    ids = list(set(ids))
    for i in range(0, len(ids), 200):
        chunk = ids[i:i+200]
        key = f"/items_bulk_{'_'.join(map(str, sorted(chunk)))}"
        cached = cache.get(key, ttl=3600)
        if cached is not None:
            result.update({int(k): v for k, v in cached.items()})
            continue
        data = _get("/items", params={"ids": ",".join(map(str, chunk))}, ttl=3600, authenticated=False)
        chunk_map = {item["id"]: item for item in data}
        cache.set(key, {str(k): v for k, v in chunk_map.items()})
        result.update(chunk_map)
    return result


def prices_bulk(ids: list[int]) -> dict[int, dict]:
    """Fetch TP prices in chunks of 200. Returns {id: price_detail}."""
    result = {}
    ids = list(set(ids))
    for i in range(0, len(ids), 200):
        chunk = ids[i:i+200]
        key = f"/prices_bulk_{'_'.join(map(str, sorted(chunk)))}"
        cached = cache.get(key, ttl=120)
        if cached is not None:
            result.update({int(k): v for k, v in cached.items()})
            continue
        try:
            data = _get("/commerce/prices", params={"ids": ",".join(map(str, chunk))}, ttl=120, authenticated=False)
            chunk_map = {item["id"]: item for item in data}
            cache.set(key, {str(k): v for k, v in chunk_map.items()})
            result.update(chunk_map)
        except Exception:
            pass
    return result


def recipes_for_output(item_id: int) -> list:
    return _get("/recipes/search", params={"output": item_id}, ttl=3600, authenticated=False)


def account_recipes() -> list[int]:
    return _get("/account/recipes", ttl=300)


def currencies() -> dict[int, dict]:
    data = _get("/currencies", params={"ids": "all"}, ttl=3600, authenticated=False)
    return {c["id"]: c for c in data}
