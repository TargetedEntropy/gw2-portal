"""API client tests — rate limiting, retries, error typing, and URL construction."""

import time
from unittest import mock

import pytest
import requests

from gw2 import api, cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    api.init("test-key", rate_per_sec=1000.0, burst=1000)
    yield


def resp(status=200, json_data=None, headers=None):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = json_data if json_data is not None else {}
    r.raise_for_status.side_effect = (
        requests.HTTPError(f"{status}") if status >= 400 else None
    )
    return r


# --- URL construction (fixes.md item 20) ------------------------------------

def test_character_name_cannot_escape_the_endpoint():
    """quote()'s default safe='/' would let a name redirect the authenticated call."""
    with mock.patch("gw2.api._get") as g:
        api.character("Bob/../../account/bank")
    assert g.call_args[0][0] == "/characters/Bob%2F..%2F..%2Faccount%2Fbank"


def test_ordinary_character_names_still_work():
    with mock.patch("gw2.api._get") as g:
        api.character("Sir Reginald")
    assert g.call_args[0][0] == "/characters/Sir%20Reginald"


# --- Schema pinning (fixes.md item 13) --------------------------------------

def test_every_request_pins_the_schema_version():
    with mock.patch("gw2.api._request", return_value=resp(json_data={})) as r:
        api._get("/account", ttl=0)
    assert r.call_args[0][1]["v"] == api.SCHEMA


def test_schema_is_part_of_the_cache_key():
    """Bumping SCHEMA must invalidate entries fetched under the old contract."""
    with mock.patch("gw2.api._request", return_value=resp(json_data={"a": 1})):
        api._get("/account", ttl=300)
    with mock.patch.object(api, "SCHEMA", "9999-01-01T00:00:00.000Z"):
        with mock.patch("gw2.api._request", return_value=resp(json_data={"a": 2})):
            assert api._get("/account", ttl=300) == {"a": 2}


def test_legendary_armory_uses_a_schema_where_the_endpoint_exists():
    with mock.patch("gw2.api._get", return_value=[]) as g:
        api.legendary_armory()
    assert g.call_args.kwargs["schema"] == "latest"


# --- Error typing (fixes.md item 11) ----------------------------------------

@pytest.mark.parametrize("status,exc", [
    (401, api.GW2AuthError),
    (403, api.GW2ScopeError),
    (404, api.GW2NotFound),
])
def test_terminal_statuses_raise_typed_errors(status, exc):
    with mock.patch("requests.get", return_value=resp(status)):
        with pytest.raises(exc):
            api._request("/account", {}, True)


def test_429_is_retried_then_raises_rate_limited():
    with mock.patch("requests.get", return_value=resp(429)), \
         mock.patch("time.sleep"):
        with pytest.raises(api.GW2RateLimited):
            api._request("/account", {}, True)


def test_transient_5xx_is_retried_and_can_succeed():
    seq = [resp(503), resp(503), resp(200, {"ok": True})]
    with mock.patch("requests.get", side_effect=seq), mock.patch("time.sleep"):
        assert api._request("/account", {}, True).json() == {"ok": True}


def test_429_drains_the_bucket_so_queued_calls_back_off():
    api._bucket.tokens = 500
    with mock.patch("requests.get", return_value=resp(429)), mock.patch("time.sleep"):
        with pytest.raises(api.GW2RateLimited):
            api._request("/account", {}, True)
    assert api._bucket.tokens == 0


# --- Stale-on-error fallback (fixes.md item 15) -----------------------------

def test_stale_cache_is_served_when_upstream_fails():
    with mock.patch("gw2.api._request", return_value=resp(json_data={"a": 1})):
        api._get("/account", ttl=300)
    with mock.patch("gw2.api._request", side_effect=api.GW2Error("down")):
        assert api._get("/account", ttl=0, stale_ttl=10**9) == {"a": 1}


def test_error_propagates_when_there_is_no_stale_copy():
    with mock.patch("gw2.api._request", side_effect=api.GW2Error("down")):
        with pytest.raises(api.GW2Error):
            api._get("/nothing-cached", ttl=0)


# --- Bulk fetching (fixes.md item 6) ----------------------------------------

def test_bulk_reports_failed_ids_rather_than_dropping_them():
    with mock.patch("gw2.api._get", side_effect=api.GW2Error("boom")):
        result, failed = api.items_bulk([1, 2, 3])
    assert result == {} and failed == {1, 2, 3}


def test_bulk_caches_per_id_and_skips_refetch():
    with mock.patch("gw2.api._get", return_value=[{"id": 1, "name": "A"}]) as g:
        api.items_bulk([1])
        assert g.call_count == 1
        api.items_bulk([1])
        assert g.call_count == 1  # served from the per-id cache


def test_ids_the_api_omits_are_cached_as_absent_not_retried():
    """Untradeable items are absent from /commerce/prices by design."""
    with mock.patch("gw2.api._get", return_value=[]) as g:
        result, failed = api.prices_bulk([99])
        assert result == {} and failed == set()
        api.prices_bulk([99])
        assert g.call_count == 1  # the absence itself was cached


def test_bulk_respects_the_200_id_limit():
    calls = []

    def fake(endpoint, params=None, **kw):
        calls.append(params["ids"].split(","))
        return []

    with mock.patch("gw2.api._get", side_effect=fake):
        api.items_bulk(list(range(1, 451)))
    assert all(len(c) <= api.BULK_LIMIT for c in calls)
    assert sum(len(c) for c in calls) == 450


def test_itemstats_use_the_bulk_client_and_per_id_cache():
    with mock.patch(
        "gw2.api._get",
        return_value=[{"id": 161, "name": "Berserker's", "attributes": []}],
    ) as g:
        result, failed = api.itemstats_bulk([161])
        assert result[161]["name"] == "Berserker's" and failed == set()
        api.itemstats_bulk([161])
        assert g.call_count == 1


def test_professions_are_indexed_by_id():
    payload = [
        {"id": "Engineer", "weapons": {"Rifle": {}}},
        {"id": "Ranger", "weapons": {"LongBow": {}}},
    ]
    with mock.patch("gw2.api._get", return_value=payload):
        result = api.professions()
    assert set(result) == {"Engineer", "Ranger"}
    assert "Rifle" in result["Engineer"]["weapons"]


# --- Rate limiting (fixes.md item 9) ----------------------------------------

def test_bucket_throttles_once_the_burst_is_spent():
    b = api._Bucket(rate_per_sec=50.0, capacity=2)
    start = time.monotonic()
    for _ in range(5):
        b.take()
    assert time.monotonic() - start >= 0.05  # 3 tokens had to be refilled


def test_bucket_allows_the_full_burst_immediately():
    b = api._Bucket(rate_per_sec=1.0, capacity=10)
    start = time.monotonic()
    for _ in range(10):
        b.take()
    assert time.monotonic() - start < 0.1


# --- Recipe index (fixes.md items 3, 4, 12) ---------------------------------

RECIPES = [
    {"id": 100, "output_item_id": 900, "flags": [],
     "ingredients": [{"item_id": 10, "count": 2}, {"item_id": 11, "count": 1}]},
    {"id": 101, "output_item_id": 901, "flags": ["AutoLearned"],
     "ingredients": [{"item_id": 10, "count": 5}]},
]


def test_index_maps_ingredients_not_just_outputs():
    """The keep-signal needs 'what do I need', not 'what could I make'."""
    with mock.patch("gw2.api._get", side_effect=[[100, 101], RECIPES]):
        idx = api.build_recipe_index(force=True)
    assert idx["ingredients"][10] == [100, 101]
    assert idx["ingredients"][11] == [100]
    assert idx["outputs"][900] == [100]


def test_index_records_autolearned_recipes():
    with mock.patch("gw2.api._get", side_effect=[[100, 101], RECIPES]):
        idx = api.build_recipe_index(force=True)
    assert idx["auto"] == {101}


def test_partial_index_is_not_cached():
    """A truncated index would otherwise be trusted as authoritative for a day."""
    with mock.patch("gw2.api._get", side_effect=[[100, 101], api.GW2Error("boom")]):
        idx = api.build_recipe_index(force=True)
    assert idx["complete"] is False
    assert api.cached_recipe_index() is None


def test_complete_index_is_cached_and_reused():
    with mock.patch("gw2.api._get", side_effect=[[100, 101], RECIPES]) as g:
        api.build_recipe_index(force=True)
        calls = g.call_count
        api.build_recipe_index()
        assert g.call_count == calls  # served from cache
    assert api.cached_recipe_index()["ingredients"][10] == [100, 101]


def test_craftable_ids_union_autolearned_with_account_unlocks():
    """Verified live: /account/recipes contains 0 of the 2091 AutoLearned recipes."""
    with mock.patch("gw2.api._get", side_effect=[[100, 101], RECIPES]):
        api.build_recipe_index(force=True)
    with mock.patch("gw2.api.account_recipes", return_value=[555]):
        assert api.craftable_recipe_ids() == {555, 101}


def test_craftable_ids_survive_a_missing_unlocks_scope():
    with mock.patch("gw2.api._get", side_effect=[[100, 101], RECIPES]):
        api.build_recipe_index(force=True)
    with mock.patch("gw2.api.account_recipes", side_effect=api.GW2ScopeError("no scope")):
        assert api.craftable_recipe_ids() == {101}
