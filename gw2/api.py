import logging
import threading
import time
from urllib.parse import quote

import requests

from . import cache, mapdata

log = logging.getLogger(__name__)

BASE = "https://api.guildwars2.com/v2"

# Pinned deliberately. The API serves the OLDEST schema when `v` is omitted, so
# leaving it unset means the contract can shift under us without a code change.
#
# This date is chosen to keep /v2/characters `equipment` as a flat list. Moving to
# 2019-12-19 or later adds legendary-armory entries with a `location` field, and
# _character_equipment() below must then filter on it or the equipment panel will
# list every armory item and inactive build tab. See fixes.md item 13.
SCHEMA = "2019-03-20T19:00:00.000Z"

BULK_LIMIT = 200  # documented maximum for ?ids=

_api_key = None


class GW2Error(Exception):
    """Base class for GW2 API failures."""


class GW2AuthError(GW2Error):
    """401 — the API key was rejected."""


class GW2ScopeError(GW2Error):
    """403 — the API key lacks a required permission."""


class GW2NotFound(GW2Error):
    """404 — no such resource."""


class GW2RateLimited(GW2Error):
    """429 — throttled upstream."""


class _Bucket:
    """Token bucket shared by every caller, including background jobs.

    The GW2 API allows a 300-request burst refilling at 5/second, enforced per IP
    rather than per key — so the budget is shared with anything else on this host.
    We stay deliberately under it.
    """

    def __init__(self, rate_per_sec: float, capacity: int):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = float(capacity)
        self.ts = time.monotonic()
        self.lock = threading.Lock()

    def take(self, n: int = 1):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.ts) * self.rate)
            self.ts = now
            if self.tokens < n:
                wait = (n - self.tokens) / self.rate
                time.sleep(wait)
                self.tokens = 0.0
                self.ts = time.monotonic()
            else:
                self.tokens -= n

    def drain(self):
        """Empty the bucket so everything queued behind a 429 backs off too."""
        with self.lock:
            self.tokens = 0.0
            self.ts = time.monotonic()


_bucket = _Bucket(rate_per_sec=4.0, capacity=100)


def init(api_key: str, rate_per_sec: float = 4.0, burst: int = 100):
    global _api_key, _bucket
    _api_key = api_key
    _bucket = _Bucket(rate_per_sec=rate_per_sec, capacity=burst)


def _headers():
    return {"Authorization": f"Bearer {_api_key}"}


RETRYABLE = {429, 500, 502, 503, 504}


def _request(path: str, params: dict, authenticated: bool) -> requests.Response:
    url = BASE + path
    headers = _headers() if authenticated else {}
    last = None

    for attempt in range(4):
        _bucket.take()
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
        except (requests.Timeout, requests.ConnectionError) as e:
            # A read timeout is not a status code, so it needs its own retry path.
            if attempt < 3:
                log.warning("%s: %s, retrying", path, e)
                time.sleep(2**attempt)
                continue
            raise GW2Error(f"{path}: {e}") from e

        if r.status_code in RETRYABLE and attempt < 3:
            if r.status_code == 429:
                _bucket.drain()
            # This API does not send Retry-After; the header read is opportunistic.
            delay = float(r.headers.get("Retry-After", 2**attempt))
            log.warning("%s -> %s, retrying in %.1fs", path, r.status_code, delay)
            time.sleep(min(delay, 30))
            last = r
            continue

        if r.status_code == 401:
            raise GW2AuthError("API key rejected — check config.toml")
        if r.status_code == 403:
            raise GW2ScopeError(f"API key lacks the permission needed for {path}")
        if r.status_code == 404:
            raise GW2NotFound(f"not found: {path}")
        if r.status_code == 429:
            raise GW2RateLimited("throttled by the GW2 API — try again shortly")
        r.raise_for_status()
        return r

    if last is not None:
        raise GW2Error(f"{path} failed after retries (HTTP {last.status_code})")
    raise GW2Error(f"{path} failed after retries")


def _get(
    path: str,
    params: dict = None,
    ttl: int = 300,
    authenticated: bool = True,
    cache_response: bool = True,
    stale_ttl: int = None,
):
    params = dict(params or {})
    params["v"] = SCHEMA
    key = path + str(sorted(params.items()))

    if cache_response:
        hit = cache.get(key, ttl)
        if hit is not None:
            return hit

    try:
        data = _request(path, params, authenticated).json()
    except Exception:
        # Stale data beats a 500 page for a personal dashboard.
        if cache_response:
            stale = cache.get(key, stale_ttl if stale_ttl is not None else ttl * 20)
            if stale is not None:
                log.warning("serving stale cache for %s", path)
                return stale
        raise

    if cache_response:
        cache.set(key, data)
    return data


# --- Authenticated endpoints -------------------------------------------------


def tokeninfo():
    return _get("/tokeninfo", ttl=3600)


def account():
    return _get("/account", ttl=60)


def wallet():
    return _get("/account/wallet", ttl=60)


def characters():
    return _get("/characters", ttl=120)


def character(name: str):
    # safe='' matters: the default safe='/' lets a name containing a slash
    # redirect this authenticated request to a different endpoint entirely.
    return _get(f"/characters/{quote(name, safe='')}", ttl=120)


def bank():
    return _get("/account/bank", ttl=60)


def materials():
    return _get("/account/materials", ttl=60)


def shared_inventory():
    return _get("/account/inventory", ttl=60)


def account_recipes() -> list[int]:
    return _get("/account/recipes", ttl=300)


def account_mastery_points() -> dict:
    """{"totals": [{"region","spent","earned"}], "unlocked": [insight ids]}"""
    return _get("/account/mastery/points", ttl=300)


def account_masteries() -> list[dict]:
    return _get("/account/masteries", ttl=300)


def account_achievements() -> list[dict]:
    """Only achievements with progress; untouched ones are absent entirely."""
    return _get("/account/achievements", ttl=300)


def masteries() -> list[dict]:
    return _get("/masteries", params={"ids": "all"}, ttl=86400, authenticated=False)


# --- Public endpoints --------------------------------------------------------


def currencies() -> dict[int, dict]:
    data = _get("/currencies", params={"ids": "all"}, ttl=86400, authenticated=False)
    return {c["id"]: c for c in data}


def world_name(world_id: int) -> str:
    """Resolve the numeric world id from /v2/account into a display name."""
    if not world_id:
        return "Unknown"
    try:
        data = _get(
            "/worlds", params={"ids": str(world_id)}, ttl=86400, authenticated=False
        )
        return data[0]["name"] if data else str(world_id)
    except Exception:
        return str(world_id)


def _bulk(
    endpoint: str, prefix: str, ids: list[int], ttl: int
) -> tuple[dict[int, dict], set[int]]:
    """Fetch bulk-expandable records, caching each id individually.

    Returns (results, failed_ids). Callers must treat failed ids as unknown rather
    than as an absence of data — see fixes.md item 6.
    """
    result: dict[int, dict] = {}
    missing: list[int] = []
    for iid in set(ids):
        hit = cache.get(f"{prefix}:{iid}", ttl, default=cache.MISS)
        if hit is cache.MISS:
            missing.append(iid)
        else:
            result[iid] = hit  # may be None: the API knows this id has no record

    failed: set[int] = set()
    for i in range(0, len(missing), BULK_LIMIT):
        chunk = missing[i : i + BULK_LIMIT]
        try:
            # cache_response=False: we cache per id below, so the chunk-keyed entry
            # would never be read again.
            data = _get(
                endpoint,
                params={"ids": ",".join(map(str, chunk))},
                ttl=ttl,
                authenticated=False,
                cache_response=False,
            )
        except GW2NotFound:
            # Not a failure. /commerce/prices 404s when none of the requested ids
            # are tradable at all — a real answer, and one worth caching so we
            # don't re-ask on every page load.
            data = []
        except Exception as e:
            log.warning("%s chunk of %d failed: %s", endpoint, len(chunk), e)
            failed.update(chunk)
            continue

        returned = set()
        for rec in data:
            cache.set(f"{prefix}:{rec['id']}", rec)
            result[rec["id"]] = rec
            returned.add(rec["id"])
        # Ids the API simply doesn't know (or that aren't tradable, for prices)
        # are absent by design, not failures.
        for iid in set(chunk) - returned:
            cache.set(f"{prefix}:{iid}", None)

    return result, failed


def items_bulk(ids: list[int]) -> tuple[dict[int, dict], set[int]]:
    """Item details, cached per id for 24h. Item data is effectively static."""
    result, failed = _bulk("/items", "item", ids, ttl=86400)
    return {k: v for k, v in result.items() if v is not None}, failed


def prices_bulk(ids: list[int]) -> tuple[dict[int, dict], set[int]]:
    """TP prices, cached per id for 2min. Untradeable ids cache as None."""
    result, failed = _bulk("/commerce/prices", "price", ids, ttl=120)
    return {k: v for k, v in result.items() if v is not None}, failed


# --- Recipe index ------------------------------------------------------------

_RECIPE_KEY = "recipe_index_v2"
_recipe_lock = threading.Lock()


def _empty_index() -> dict:
    return {"complete": False, "total": 0, "ingredients": {}, "outputs": {}, "auto": set()}


def _decode_index(raw: dict) -> dict:
    outputs = {int(k): v for k, v in raw.get("outputs", {}).items()}
    # recipe id -> the item it produces, so an ingredient can name what it makes.
    recipe_output = {rid: out for out, rids in outputs.items() for rid in rids}
    return {
        "complete": raw.get("complete", False),
        "total": raw.get("total", 0),
        "ingredients": {int(k): v for k, v in raw.get("ingredients", {}).items()},
        "outputs": outputs,
        "recipe_output": recipe_output,
        "auto": set(raw.get("auto", [])),
    }


_index_memo: tuple[float, dict] | None = None


def cached_recipe_index() -> dict | None:
    """Return the index only if a complete one is already cached. Never fetches.

    Memoized on the cache file's mtime: decoding is ~30k dict operations over a
    multi-megabyte payload, and it was running on every page render.
    """
    global _index_memo

    stamp = cache.mtime(_RECIPE_KEY)
    if stamp is None:
        return None
    if _index_memo is not None and _index_memo[0] == stamp:
        return _index_memo[1]

    raw = cache.get(_RECIPE_KEY, ttl=86400 * 7)
    if raw is None or not raw.get("complete"):
        return None
    decoded = _decode_index(raw)
    _index_memo = (stamp, decoded)
    return decoded


def build_recipe_index(force: bool = False) -> dict:
    """Crawl every recipe and build both lookup directions.

    ~66 upstream requests. Never call this from a request handler — it is driven by
    the scheduler and the warm-up path. Serialized so concurrent callers don't each
    start their own crawl.
    """
    with _recipe_lock:
        if not force:
            existing = cached_recipe_index()
            if existing is not None:
                return existing

        all_ids = _get("/recipes", ttl=86400, authenticated=False, cache_response=False)
        ingredients: dict[int, list[int]] = {}
        outputs: dict[int, list[int]] = {}
        auto: set[int] = set()
        failed = 0

        log.info("building recipe index from %d recipes", len(all_ids))
        for i in range(0, len(all_ids), BULK_LIMIT):
            chunk = all_ids[i : i + BULK_LIMIT]
            try:
                recipes = _get(
                    "/recipes",
                    params={"ids": ",".join(map(str, chunk))},
                    ttl=86400,
                    authenticated=False,
                    cache_response=False,
                )
            except Exception as e:
                log.warning("recipe chunk failed: %s", e)
                failed += 1
                continue

            for r in recipes:
                rid = r["id"]
                if "AutoLearned" in (r.get("flags") or []):
                    auto.add(rid)
                out = r.get("output_item_id")
                if out:
                    outputs.setdefault(out, []).append(rid)
                for ing in r.get("ingredients") or []:
                    # `item_id` on the pinned schema; `id` on newer ones.
                    iid = ing.get("item_id") or ing.get("id")
                    if iid:
                        ingredients.setdefault(iid, []).append(rid)

        index = {
            "complete": failed == 0,
            "total": len(all_ids),
            "ingredients": ingredients,
            "outputs": outputs,
            "auto": auto,
        }

        if failed:
            # Never persist a partial index — it would be trusted as authoritative
            # for a full day. See fixes.md item 12.
            log.warning("recipe index incomplete (%d chunks failed) — not caching", failed)
        else:
            cache.set(
                _RECIPE_KEY,
                {
                    "complete": True,
                    "total": index["total"],
                    "ingredients": {str(k): v for k, v in ingredients.items()},
                    "outputs": {str(k): v for k, v in outputs.items()},
                    "auto": sorted(auto),
                },
            )
            log.info(
                "recipe index built: %d ingredients, %d outputs, %d auto-learned",
                len(ingredients),
                len(outputs),
                len(auto),
            )
        return index


# --- Mastery-point achievement index ----------------------------------------

_MASTERY_KEY = "mastery_achievements_v2"  # v2 adds point_id
_mastery_lock = threading.Lock()
_mastery_memo: tuple[float, list] | None = None


def cached_mastery_achievements() -> list[dict] | None:
    """Mastery-granting achievements, or None if the index isn't built yet."""
    global _mastery_memo

    stamp = cache.mtime(_MASTERY_KEY)
    if stamp is None:
        return None
    if _mastery_memo is not None and _mastery_memo[0] == stamp:
        return _mastery_memo[1]

    raw = cache.get(_MASTERY_KEY, ttl=86400 * 7)
    if raw is None or not raw.get("complete"):
        return None
    _mastery_memo = (stamp, raw["achievements"])
    return raw["achievements"]


def build_mastery_achievements(force: bool = False) -> list[dict]:
    """Scan every achievement for the ones that award a mastery point.

    There is no endpoint for "achievements that grant mastery points", so the full
    catalogue has to be walked: ~8,200 achievements, ~42 requests. Like the recipe
    index this is a background job, and the result only changes on game patches.
    """
    with _mastery_lock:
        if not force:
            existing = cached_mastery_achievements()
            if existing is not None:
                return existing

        ids = _get("/achievements", ttl=86400, authenticated=False, cache_response=False)
        log.info("scanning %d achievements for mastery rewards", len(ids))

        # Category and group names are the strongest hint the API gives about what
        # kind of content an achievement is, which is what the ease ranking uses.
        cats = _get(
            "/achievements/categories", params={"ids": "all"}, ttl=86400,
            authenticated=False, cache_response=False,
        )
        groups = _get(
            "/achievements/groups", params={"ids": "all"}, ttl=86400,
            authenticated=False, cache_response=False,
        )
        cat_by_id = {c["id"]: c for c in cats}
        cat_of, grp_of = {}, {}
        for c in cats:
            for a in c.get("achievements") or []:
                aid = a if isinstance(a, int) else (a or {}).get("id")
                if aid:
                    cat_of[aid] = c["name"]
        for g in groups:
            for cid in g.get("categories") or []:
                for a in (cat_by_id.get(cid) or {}).get("achievements") or []:
                    aid = a if isinstance(a, int) else (a or {}).get("id")
                    if aid:
                        grp_of[aid] = g["name"]

        found, failed = [], 0
        for i in range(0, len(ids), BULK_LIMIT):
            chunk = ids[i : i + BULK_LIMIT]
            try:
                batch = _get(
                    "/achievements", params={"ids": ",".join(map(str, chunk))},
                    ttl=86400, authenticated=False, cache_response=False,
                )
            except Exception as e:
                log.warning("achievement chunk failed: %s", e)
                failed += 1
                continue

            for a in batch:
                reward = next(
                    (r for r in (a.get("rewards") or []) if r.get("type") == "Mastery"),
                    None,
                )
                if not reward:
                    continue
                tiers = a.get("tiers") or [{}]
                found.append(
                    {
                        "id": a["id"],
                        "name": a.get("name", f"Achievement {a['id']}"),
                        "region": reward.get("region", ""),
                        # The mastery point this awards. Shares a namespace with
                        # /account/mastery/points `unlocked` and with the coords in
                        # the continents tree, so it joins the three together.
                        "point_id": reward.get("id"),
                        "requirement": a.get("requirement", ""),
                        "type": a.get("type", ""),
                        "flags": a.get("flags", []),
                        "bits": len(a.get("bits") or []),
                        "target": tiers[-1].get("count", 0),
                        "category": cat_of.get(a["id"], ""),
                        "group": grp_of.get(a["id"], ""),
                    }
                )

        if failed:
            log.warning("mastery index incomplete (%d chunks failed) — not caching", failed)
        else:
            cache.set(_MASTERY_KEY, {"complete": True, "achievements": found})
            log.info("mastery index built: %d point-granting achievements", len(found))
        return found


# --- Map / mastery-point coordinate index ------------------------------------

_MAPDATA_KEY = "map_mastery_coords_v2"  # v2 records a verified tile zoom
_mapdata_lock = threading.Lock()
_mapdata_memo: tuple[float, dict] | None = None


def _tile_exists(url: str) -> bool:
    """Tiles are on a CDN, not the rate-limited API, so they bypass the bucket."""
    try:
        return requests.head(url, timeout=15).status_code == 200
    except requests.RequestException:
        return False


def cached_map_index() -> dict | None:
    """{"maps": {map_id: {...}}, "points": {point_id: map_id}} or None."""
    global _mapdata_memo

    stamp = cache.mtime(_MAPDATA_KEY)
    if stamp is None:
        return None
    if _mapdata_memo is not None and _mapdata_memo[0] == stamp:
        return _mapdata_memo[1]

    raw = cache.get(_MAPDATA_KEY, ttl=86400 * 30)
    if raw is None or not raw.get("complete"):
        return None
    decoded = {
        "maps": {int(k): v for k, v in raw["maps"].items()},
        "points": {int(k): v for k, v in raw["points"].items()},
    }
    _mapdata_memo = (stamp, decoded)
    return decoded


def build_map_index(map_names: set[str], force: bool = False) -> dict:
    """Locate mastery points on their maps, with coordinates.

    Only the maps named by insight requirements are scanned — walking every map on
    every floor would be thousands of requests for data we'd throw away.

    The floor carrying `mastery_points` is not the floor serving tiles and is not
    predictable, so each map's floors are tried in turn until one yields points.
    """
    with _mapdata_lock:
        if not force:
            existing = cached_map_index()
            if existing is not None:
                return existing

        catalogue = _get(
            "/maps", params={"ids": "all"}, ttl=86400 * 30,
            authenticated=False, cache_response=False,
        )
        by_name = {m["name"]: m for m in catalogue}

        maps: dict[int, dict] = {}
        points: dict[int, int] = {}
        missing = []

        for name in sorted(map_names):
            # The requirement text says "in the Desolation" and "in the Domain of
            # Vabbi", but the map names are "The Desolation" and "Domain of Vabbi" --
            # the article is part of one name and not the other, so try both forms.
            meta = by_name.get(name) or by_name.get(f"The {name}")
            if not meta or not meta.get("continent_rect"):
                missing.append(name)
                continue

            continent = meta.get("continent_id")
            region = meta.get("region_id")
            floors = [meta.get("default_floor")] + list(meta.get("floors") or [])
            found = None
            for floor in dict.fromkeys(f for f in floors if f is not None):
                try:
                    data = _get(
                        f"/continents/{continent}/floors/{floor}/regions/{region}/maps/{meta['id']}",
                        ttl=86400 * 30, authenticated=False, cache_response=False,
                    )
                except Exception:
                    continue
                if data.get("mastery_points"):
                    found = data
                    break

            if not found:
                missing.append(name)
                continue

            # Confirm the map is actually renderable before storing a zoom for it.
            tile_floor = meta.get("default_floor", 1)
            continent_info = continent_meta(continent)
            tile_zoom = mapdata.probe_zoom(
                meta["continent_rect"], continent, tile_floor,
                continent_info["max_zoom"], _tile_exists,
            )
            if tile_zoom is None:
                log.info("no tiles published for %s — pins only", name)

            entry = {
                "id": meta["id"],
                # Keyed by the name the route cards use, not the canonical one, so
                # the two sides still join.
                "name": name,
                "map_name": meta["name"],
                "continent_id": continent,
                # Tiles come from default_floor, NOT the floor the coords were on.
                "tile_floor": tile_floor,
                "tile_zoom": tile_zoom,
                "max_zoom": continent_info["max_zoom"],
                "continent_rect": meta["continent_rect"],
                "points": [
                    {"id": p["id"], "coord": p["coord"], "region": p.get("region", "")}
                    for p in found["mastery_points"]
                ],
            }
            maps[meta["id"]] = entry
            for p in entry["points"]:
                points[p["id"]] = meta["id"]

        if missing:
            log.info("no map coordinates for %d location(s): %s",
                     len(missing), ", ".join(missing[:5]))

        index = {"maps": maps, "points": points}
        cache.set(
            _MAPDATA_KEY,
            {
                "complete": True,
                "maps": {str(k): v for k, v in maps.items()},
                "points": {str(k): v for k, v in points.items()},
            },
        )
        log.info("map index built: %d maps, %d located mastery points",
                 len(maps), len(points))
        return index


def continent_meta(continent_id: int) -> dict:
    return _get(f"/continents/{continent_id}", ttl=86400 * 30, authenticated=False)


def craftable_recipe_ids() -> set[int]:
    """Recipes the account can actually make.

    /v2/account/recipes only returns sheet-unlocked recipes — verified live, it
    contains 0 of the 2091 AutoLearned recipes — so auto-learned ones must be
    unioned in or the signal covers a tiny fraction of the crafting graph.
    """
    index = cached_recipe_index()
    auto = index["auto"] if index else set()
    try:
        unlocked = set(account_recipes())
    except Exception as e:
        log.warning("could not read account recipes: %s", e)
        unlocked = set()
    return unlocked | auto
