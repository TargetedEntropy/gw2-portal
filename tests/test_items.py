"""Classification tests.

Every P0 bug in fixes.md is represented here — these are the regression tests that
would have caught them. Fixtures mirror real payload shapes taken from .cache/.
"""

from unittest import mock

import pytest

from gw2 import items


# The exact flag enum from the API docs. The casing is inconsistent on purpose:
# `SoulbindOnAcquire` has a lowercase 'b', `SoulBindOnUse` an uppercase one.
API_ITEM_FLAGS = {
    "AccountBindOnUse", "AccountBound", "Attuned", "BulkConsume", "DeleteWarning",
    "HideSuffix", "Infused", "MonsterOnly", "NoMysticForge", "NoSalvage", "NoSell",
    "NotUpgradeable", "NoUnderwater", "SoulbindOnAcquire", "SoulBindOnUse", "Tonic",
    "Unique",
}


def item(**kw):
    base = {"id": 1, "name": "Thing", "type": "Trophy", "rarity": "Basic",
            "vendor_value": 10, "flags": []}
    base.update(kw)
    return base


def price(sell=0, buy=0):
    return {"id": 1, "buys": {"unit_price": buy}, "sells": {"unit_price": sell}}


def verdict(it, p=None, recipe=False, threshold=100, rarity_min=4, **kw):
    return items.classify(it, p, recipe, threshold, rarity_min, **kw)["verdict"]


# --- Flag matching (fixes.md items 1, 2) ------------------------------------

def test_bound_flags_are_all_real_api_flags():
    """Guards the typo that started this: SoulboundOnUse is not an API value."""
    lowered = {f.lower() for f in API_ITEM_FLAGS}
    assert items.BOUND_FLAGS <= lowered


@pytest.mark.parametrize("flag", [
    "AccountBound", "AccountBindOnUse", "SoulbindOnAcquire", "SoulBindOnUse",
])
def test_every_binding_flag_is_kept(flag):
    assert verdict(item(flags=[flag]), price(sell=100000)) == "keep"


def test_soulbind_on_use_is_kept_despite_a_juicy_tp_price():
    # The original bug: this exact item was advised as "sell".
    assert verdict(item(flags=["SoulBindOnUse"]), price(sell=500000)) == "keep"


def test_unique_is_kept():
    assert verdict(item(flags=["Unique"]), price(sell=100000)) == "keep"


# --- Slot binding (fixes.md item 5) -----------------------------------------

def test_slot_binding_overrides_clean_item_flags():
    """Binding lives on the slot; the item's own flags can be empty."""
    v = verdict(item(flags=[]), price(sell=100000), slot={"binding": "Account"})
    assert v == "keep"


def test_character_bound_names_the_character():
    r = items.classify(item(), price(sell=99999), False, 100, 4,
                       slot={"binding": "Character", "bound_to": "Karidan"})
    assert r["verdict"] == "keep" and "Karidan" in r["reason"]


def test_unbound_slot_still_sells():
    assert verdict(item(vendor_value=0), price(sell=100000), slot={"count": 1}) == "sell"


# --- Missing data (fixes.md item 6) -----------------------------------------

def test_missing_item_detail_is_unknown_not_toss():
    """A failed API lookup must never look like 'this item is worthless'."""
    assert verdict({}, None) == "unknown"
    assert verdict(item(), None, known=False) == "unknown"


def test_unknown_verdict_carries_no_price_claim():
    r = items.classify({}, None, False, 100, 4, known=False)
    assert r["sell_price"] == 0 and r["vendor_value"] == 0
    assert "unavailable" in r["reason"].lower()


# --- NoSell / NoSalvage (fixes.md item 7) -----------------------------------

def test_nosell_item_is_never_advised_to_vendor():
    r = items.classify(item(flags=["NoSell"], vendor_value=500), None, False, 100, 4)
    assert r["verdict"] == "salvage"
    assert "Vendor for" not in r["reason"]


def test_nosell_vendor_value_is_zeroed():
    """vendor_value is non-zero on NoSell items but is not real money."""
    assert items.vendor_value(item(flags=["NoSell"], vendor_value=500)) == 0
    assert items.vendor_value(item(vendor_value=500)) == 500


def test_no_vendor_value_but_salvageable_is_salvage():
    assert verdict(item(vendor_value=0, flags=[])) == "salvage"


def test_nosell_and_nosalvage_can_only_be_destroyed():
    r = items.classify(item(vendor_value=0, flags=["NoSell", "NoSalvage"]),
                       None, False, 100, 4)
    assert r["verdict"] == "toss"
    assert "destroy" in r["reason"].lower()


def test_delete_warning_is_surfaced():
    r = items.classify(item(flags=["DeleteWarning"], vendor_value=5),
                       None, False, 100, 4)
    assert "warns" in r["reason"]


# --- Stack-aware sell math (fixes.md item 8) --------------------------------

def test_threshold_applies_to_the_stack_not_the_unit():
    """250 materials at 40c each is a gold, even though no unit clears 100c."""
    cheap = item(vendor_value=1)  # non-zero, so the fallback is vendor not salvage
    assert verdict(cheap, price(sell=40), count=1) == "toss"
    assert verdict(cheap, price(sell=40), count=250) == "sell"


def test_vendor_value_is_multiplied_by_the_stack_too():
    r = items.classify(item(vendor_value=10), price(sell=100), False, 100, 4, count=100)
    # 100 * 100 = 10000 gross, minus 15% fees, minus 100 * 10 vendor
    assert r["net_total"] == items.tp_net(100, 100) - 1000


def test_buy_basis_uses_the_standing_bid():
    """A wide spread means "sell" advice may not be actionable; "buy" is honest."""
    it = item(vendor_value=1)
    r = items.classify(it, price(sell=1000, buy=10), False, 100, 4,
                       count=1, price_basis="buy")
    assert r["verdict"] == "toss"  # 10c instant-sell doesn't clear the bar
    r2 = items.classify(it, price(sell=1000, buy=10), False, 100, 4,
                        count=1, price_basis="sell")
    assert r2["verdict"] == "sell"


# --- Fees --------------------------------------------------------------------

def test_tp_net_applies_both_fees():
    assert items.tp_net(10000, 1) == 10000 - 500 - 1000


def test_tp_net_enforces_the_one_copper_minimum_fee():
    # Both fees round up to 1c, so a 1c sale nets -1c, not 0.85c.
    assert items.tp_net(1, 1) == 1 - 1 - 1


def test_tp_net_of_nothing_is_nothing():
    assert items.tp_net(0, 100) == 0


# --- Formatting (fixes.md item 8) -------------------------------------------

def test_copper_to_gold_preserves_sign():
    assert items.copper_to_gold(-12345) == "-1g 23s 45c"
    assert items.copper_to_gold(12345) == "1g 23s 45c"


def test_copper_to_gold_zero():
    assert items.copper_to_gold(0) == "0c"


# --- Rarity ------------------------------------------------------------------

def test_rare_and_above_is_kept():
    assert verdict(item(rarity="Rare")) == "keep"
    assert verdict(item(rarity="Legendary")) == "keep"


def test_below_threshold_is_not_auto_kept():
    assert verdict(item(rarity="Fine", vendor_value=5)) == "toss"


def test_unknown_rarity_does_not_crash():
    assert verdict(item(rarity="Mythic", vendor_value=5)) == "toss"


# --- Recipe signal (fixes.md items 3, 4) ------------------------------------

def test_crafting_ingredient_is_kept():
    assert verdict(item(vendor_value=5), recipe=True) == "keep"


def test_recipe_signal_does_not_override_a_good_sale():
    """Ordering matters: keep wins, so this must stay a keep, not flip to sell."""
    assert verdict(item(vendor_value=0), price(sell=100000), recipe=True) == "keep"


# --- Grouping / totals -------------------------------------------------------

def test_grouping_sorts_by_stack_value_not_unit_price():
    cheap_stack = items.classify(item(vendor_value=0), price(sell=40), False, 100, 4,
                                 count=250)
    pricey_one = items.classify(item(vendor_value=0), price(sell=500), False, 100, 4,
                                count=1)
    rows = [dict(verdict="sell", **{k: v for k, v in pricey_one.items() if k != "verdict"}),
            dict(verdict="sell", **{k: v for k, v in cheap_stack.items() if k != "verdict"})]
    grouped = items.group_by_verdict(rows)
    assert grouped["sell"][0]["stack_value"] == 10000  # the 250-stack leads


def test_group_by_verdict_has_every_verdict_key():
    grouped = items.group_by_verdict([])
    assert set(grouped) == set(items.VERDICTS)


# --- Wiki links and "used in" (item lookup) ---------------------------------

def test_wiki_url_uses_the_search_form_so_odd_names_still_resolve():
    u = items.wiki_url("Fine Fish Fillet")
    assert u == "https://wiki.guildwars2.com/index.php?search=Fine%20Fish%20Fillet"


def test_wiki_url_escapes_characters_that_would_break_the_query():
    # Em dashes and ampersands appear in real item names.
    assert "&" not in items.wiki_url("Dye Canister—Red & Blue").split("search=")[1]
    assert "%E2%80%94" in items.wiki_url("Dye Canister—Red")


def test_wiki_url_of_a_nameless_item_is_empty_not_a_broken_link():
    assert items.wiki_url("") == ""
    assert items.wiki_url(None) == ""


def test_used_in_lists_what_an_ingredient_makes():
    index = {
        "ingredients": {10: [100, 101]},
        "recipe_output": {100: 900, 101: 901},
    }
    names = {
        900: {"id": 900, "name": "Bowl of Soup", "rarity": "Fine"},
        901: {"id": 901, "name": "Fish Pie", "rarity": "Basic"},
    }
    with mock.patch("gw2.api.items_bulk", return_value=(names, set())):
        got = items._resolve_used_in({10}, index, craftable=set())
    assert [e["name"] for e in got[10]] == ["Bowl of Soup", "Fish Pie"]
    assert got[10][0]["wiki_url"].endswith("Bowl%20of%20Soup")


def test_used_in_puts_craftable_recipes_first():
    """The recipes you can actually make are the actionable ones."""
    index = {
        "ingredients": {10: [100, 101]},
        "recipe_output": {100: 900, 101: 901},
    }
    names = {
        900: {"id": 900, "name": "Cannot Make", "rarity": "Fine"},
        901: {"id": 901, "name": "Can Make", "rarity": "Fine"},
    }
    with mock.patch("gw2.api.items_bulk", return_value=(names, set())):
        got = items._resolve_used_in({10}, index, craftable={101})
    assert got[10][0]["name"] == "Can Make"


def test_used_in_is_capped_and_deduplicated():
    index = {
        "ingredients": {10: list(range(100, 120))},
        # several recipes produce the same item; it should appear once
        "recipe_output": {r: (900 if r < 110 else r) for r in range(100, 120)},
    }
    names = {i: {"id": i, "name": f"Out {i}", "rarity": "Fine"} for i in range(900, 921)}
    with mock.patch("gw2.api.items_bulk", return_value=(names, set())):
        got = items._resolve_used_in({10}, index, craftable=set())
    outs = [e["id"] for e in got[10]]
    assert len(outs) <= items.MAX_USED_IN
    assert len(outs) == len(set(outs))


def test_items_with_no_recipes_get_no_used_in_entry():
    index = {"ingredients": {}, "recipe_output": {}}
    with mock.patch("gw2.api.items_bulk", return_value=({}, set())):
        assert items._resolve_used_in({10}, index, craftable=set()) == {}


# --- Account-wide aggregation (search page) ---------------------------------

def held(id, name, location, count, stack_value=0, **kw):
    base = dict(
        id=id, name=name, location=location, count=count, stack_value=stack_value,
        icon="", rarity="Basic", rarity_color="#fff", type="CraftingMaterial",
        verdict="sell", reason="", wiki_url="", recipe_count=0,
        sell_price=0, sell_price_fmt="0c",
    )
    base.update(kw)
    return base


def test_multiple_stacks_in_one_place_merge_into_a_single_tag():
    """Stacks are per-slot; two slots on one character is still one place."""
    rows = items.aggregate_by_item([
        held(1, "Unidentified Gear", "Scrollios", 250),
        held(1, "Unidentified Gear", "Scrollios", 73),
    ])
    assert len(rows) == 1
    assert rows[0]["locations"] == [{"where": "Scrollios", "count": 323}]
    assert rows[0]["total_count"] == 323


def test_stack_count_is_preserved_after_merging():
    rows = items.aggregate_by_item([
        held(1, "Thing", "Scrollios", 250),
        held(1, "Thing", "Scrollios", 73),
    ])
    assert rows[0]["stacks"] == 2


def test_a_single_stack_reports_one_stack():
    rows = items.aggregate_by_item([held(1, "Thing", "Bank", 5)])
    assert rows[0]["stacks"] == 1


def test_distinct_locations_stay_separate():
    rows = items.aggregate_by_item([
        held(1, "Thing", "Bank", 10),
        held(1, "Thing", "Scrollios", 5),
        held(1, "Thing", "Bank", 2),
    ])
    assert rows[0]["locations"] == [
        {"where": "Bank", "count": 12},
        {"where": "Scrollios", "count": 5},
    ]


def test_locations_sort_by_count_then_name():
    rows = items.aggregate_by_item([
        held(1, "Thing", "Zeta", 5),
        held(1, "Thing", "Alpha", 5),
        held(1, "Thing", "Middle", 99),
    ])
    assert [l["where"] for l in rows[0]["locations"]] == ["Middle", "Alpha", "Zeta"]


def test_rows_sort_by_total_value_descending():
    rows = items.aggregate_by_item([
        held(1, "Cheap", "Bank", 1, stack_value=10),
        held(2, "Pricey", "Bank", 1, stack_value=9999),
    ])
    assert [r["name"] for r in rows] == ["Pricey", "Cheap"]


def test_values_accumulate_across_locations():
    rows = items.aggregate_by_item([
        held(1, "Thing", "Bank", 10, stack_value=100),
        held(1, "Thing", "Scrollios", 10, stack_value=100),
    ])
    assert rows[0]["total_value"] == 200


def test_missing_location_is_labelled_not_blank():
    rows = items.aggregate_by_item([held(1, "Thing", "", 3)])
    assert rows[0]["locations"] == [{"where": "Unknown", "count": 3}]


def test_internal_accumulator_is_not_leaked_to_the_template():
    rows = items.aggregate_by_item([held(1, "Thing", "Bank", 1)])
    assert "_by_location" not in rows[0]


def test_aggregating_nothing_yields_nothing():
    assert items.aggregate_by_item([]) == []
