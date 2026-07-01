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
    """Fetch item details, caching each ID individually for 1h. Returns {id: item_detail}."""
    result = {}
    missing = []
    for iid in set(ids):
        hit = cache.get(f"item:{iid}", ttl=3600)
        if hit is not None:
            result[iid] = hit
        else:
            missing.append(iid)

    for i in range(0, len(missing), 200):
        chunk = missing[i:i+200]
        try:
            data = _get("/items", params={"ids": ",".join(map(str, chunk))}, ttl=3600, authenticated=False)
            for item in data:
                cache.set(f"item:{item['id']}", item)
                result[item["id"]] = item
        except Exception:
            pass

    return result


def prices_bulk(ids: list[int]) -> dict[int, dict]:
    """Fetch TP prices, caching each ID individually for 2min. Returns {id: price_detail}."""
    result = {}
    missing = []
    for iid in set(ids):
        hit = cache.get(f"price:{iid}", ttl=120)
        if hit is not None:
            result[iid] = hit
        else:
            missing.append(iid)

    for i in range(0, len(missing), 200):
        chunk = missing[i:i+200]
        try:
            data = _get("/commerce/prices", params={"ids": ",".join(map(str, chunk))}, ttl=120, authenticated=False)
            for p in data:
                cache.set(f"price:{p['id']}", p)
                result[p["id"]] = p
        except Exception:
            pass

    return result


def recipe_index() -> dict[int, list[int]]:
    """
    Build a full reverse-lookup: {output_item_id: [recipe_id, ...]}.
    Fetches all ~13k recipes and caches the index for 24h.
    """
    hit = cache.get("recipe_index", ttl=86400)
    if hit is not None:
        return {int(k): v for k, v in hit.items()}

    all_ids = _get("/recipes", ttl=86400, authenticated=False)
    index: dict[int, list[int]] = {}
    for i in range(0, len(all_ids), 200):
        chunk = all_ids[i:i+200]
        try:
            recipes = _get("/recipes", params={"ids": ",".join(map(str, chunk))}, ttl=86400, authenticated=False)
            for r in recipes:
                out = r.get("output_item_id")
                if out:
                    index.setdefault(out, []).append(r["id"])
        except Exception:
            pass

    cache.set("recipe_index", {str(k): v for k, v in index.items()})
    return index


def account_recipes() -> list[int]:
    return _get("/account/recipes", ttl=300)


def currencies() -> dict[int, dict]:
    data = _get("/currencies", params={"ids": "all"}, ttl=3600, authenticated=False)
    return {c["id"]: c for c in data}
