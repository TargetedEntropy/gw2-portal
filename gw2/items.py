import math
from urllib.parse import quote

# Official wiki. The ?search= form is used rather than /wiki/<Name> because
# MediaWiki redirects an exact title match straight to the article and falls back
# to search results otherwise — so unusual names (em dashes, punctuation) still
# land somewhere useful instead of on a "page does not exist" stub.
WIKI_SEARCH = "https://wiki.guildwars2.com/index.php?search="

# How many "used in" entries to show before truncating.
MAX_USED_IN = 6

RARITY_ORDER = ["Junk", "Basic", "Fine", "Masterwork", "Rare", "Exotic", "Ascended", "Legendary"]
RARITY_COLORS = {
    "Junk": "#aaaaaa",
    "Basic": "#ffffff",
    "Fine": "#62a4da",
    "Masterwork": "#1a9306",
    "Rare": "#fcd00b",
    "Exotic": "#ffa405",
    "Ascended": "#fb3e8d",
    "Legendary": "#4c139d",
}
UNKNOWN_COLOR = "#777777"

# Compared lowercased, because the API is internally inconsistent about casing:
# `SoulbindOnAcquire` has a lowercase 'b' while `SoulBindOnUse` has an uppercase
# one. Matching exact strings is how the original SoulboundOnUse typo went
# unnoticed — every soulbound-on-use item was silently advised as sellable.
BOUND_FLAGS = {"accountbound", "accountbindonuse", "soulbindonacquire", "soulbindonuse"}

# Trading Post: 5% listing fee charged on posting, 10% exchange fee on sale.
LISTING_FEE = 0.05
EXCHANGE_FEE = 0.10

VERDICTS = ("keep", "sell", "salvage", "toss", "unknown")


def wiki_url(name: str) -> str:
    return WIKI_SEARCH + quote(name or "", safe="") if name else ""


def rarity_index(rarity: str) -> int:
    try:
        return RARITY_ORDER.index(rarity)
    except ValueError:
        return 0


def copper_to_gold(copper: int) -> str:
    copper = int(copper or 0)
    sign = "-" if copper < 0 else ""
    g, rem = divmod(abs(copper), 10000)
    s, c = divmod(rem, 100)
    parts = []
    if g:
        parts.append(f"{g}g")
    if s:
        parts.append(f"{s}s")
    if c or not parts:
        parts.append(f"{c}c")
    return sign + " ".join(parts)


def tp_net(unit_price: int, count: int = 1) -> int:
    """Copper actually received after both Trading Post fees.

    Each fee is rounded up with a one-copper minimum, which matters for cheap
    items — exactly the ones sitting near the sell/toss boundary.
    """
    gross = int(unit_price or 0) * max(1, int(count or 1))
    if gross <= 0:
        return 0
    listing = max(1, math.ceil(gross * LISTING_FEE))
    exchange = max(1, math.ceil(gross * EXCHANGE_FEE))
    return gross - listing - exchange


def _flags(item: dict) -> set:
    return {f.lower() for f in (item.get("flags") or [])}


def vendor_value(item: dict) -> int:
    """Vendor price, or 0 when the item cannot be sold to a vendor at all.

    The API returns a non-zero vendor_value even for NoSell items, where it is
    informational only. Treating it as real money distorts the sell/toss boundary.
    """
    if "nosell" in _flags(item):
        return 0
    return int(item.get("vendor_value") or 0)


def classify(
    item: dict,
    price: dict | None,
    in_unlocked_recipe: bool,
    sell_threshold: int,
    keep_rarity_min: int,
    slot: dict | None = None,
    count: int = 1,
    price_basis: str = "sell",
    known: bool = True,
) -> dict:
    """Return verdict ('keep'|'sell'|'salvage'|'toss'|'unknown') plus display fields."""
    slot = slot or {}
    count = max(1, int(count or 1))

    # An item we couldn't fetch must never produce actionable advice. Before this
    # guard existed, a failed lookup fell through every branch and came out as
    # "toss" — so one upstream hiccup advised vendoring a screen of legendaries.
    if not known or not item:
        return _verdict("unknown", "Item data unavailable — retry", None, 0, count=count)

    flags = _flags(item)
    rarity = item.get("rarity", "Basic")
    vv = vendor_value(item)
    rid = rarity_index(rarity)

    # --- KEEP -------------------------------------------------------------
    # Binding lives on the slot, not the item: something can be personally bound
    # from having been equipped even when its own flags are clean.
    binding = slot.get("binding")
    if binding:
        who = slot.get("bound_to") or binding
        return _verdict("keep", f"Bound to {who} — cannot be traded", price, vv, count=count)

    if flags & BOUND_FLAGS:
        return _verdict("keep", "Account/soulbound — can't be traded", price, vv, count=count)

    if "unique" in flags:
        return _verdict("keep", "Unique item", price, vv, count=count)

    if rid >= keep_rarity_min:
        return _verdict("keep", f"{rarity} rarity (≥ keep threshold)", price, vv, count=count)

    if in_unlocked_recipe:
        return _verdict("keep", "Ingredient in a craftable recipe", price, vv, count=count)

    # --- SELL -------------------------------------------------------------
    # Threshold is judged on the whole stack: 250 materials at 40c each is a gold,
    # even though no single unit clears a 100c bar.
    if price:
        unit = _unit_price(price, price_basis)
        net = tp_net(unit, count) - (vv * count)
        if net > sell_threshold:
            basis = "instant-sell" if price_basis == "buy" else "TP"
            return _verdict(
                "sell",
                f"{basis} nets {copper_to_gold(net)} over vendor",
                price,
                vv,
                count=count,
                price_basis=price_basis,
            )

    # --- TOSS -------------------------------------------------------------
    # "Vendor or salvage" is not always a legal move: NoSell blocks the vendor and
    # NoSalvage blocks the kit, and plenty of items carry both.
    can_vendor = "nosell" not in flags and int(item.get("vendor_value") or 0) > 0
    can_salvage = "nosalvage" not in flags

    if can_vendor:
        reason = f"Vendor for {copper_to_gold(vv * count)}"
        verdict = "toss"
    elif can_salvage:
        reason = "Salvage — no vendor value"
        verdict = "salvage"
    else:
        reason = "Cannot be sold or salvaged — destroy or keep"
        verdict = "toss"

    if "deletewarning" in flags:
        reason += " · game warns before deleting"

    return _verdict(verdict, reason, price, vv, count=count, price_basis=price_basis)


def _unit_price(price: dict | None, basis: str) -> int:
    if not price:
        return 0
    side = "buys" if basis == "buy" else "sells"
    return int((price.get(side) or {}).get("unit_price") or 0)


def _verdict(verdict, reason, price, vv, count=1, price_basis="sell"):
    sell_unit = _unit_price(price, "sell")
    buy_unit = _unit_price(price, "buy")
    basis_unit = buy_unit if price_basis == "buy" else sell_unit
    net_total = tp_net(basis_unit, count) - (vv * count) if basis_unit else 0
    return {
        "verdict": verdict,
        "reason": reason,
        "sell_price": sell_unit,
        "sell_price_fmt": copper_to_gold(sell_unit),
        "buy_price": buy_unit,
        "buy_price_fmt": copper_to_gold(buy_unit),
        "vendor_value": vv,
        "vendor_value_fmt": copper_to_gold(vv),
        "stack_value": basis_unit * count,
        "stack_value_fmt": copper_to_gold(basis_unit * count),
        "net_total": net_total,
        "net_total_fmt": copper_to_gold(net_total),
    }


def enrich_slots(
    slots: list[dict | None],
    sell_threshold: int,
    keep_rarity_min: int,
    location: str = "",
    price_basis: str = "sell",
) -> dict:
    """Enrich inventory/bank slots with item details and verdicts.

    Returns {"items": [...], "failed": int, "recipe_signal": bool} — the caller
    needs the failure count so the page can say the analysis is incomplete rather
    than quietly presenting degraded advice as fact.
    """
    from . import api

    slots = [s for s in slots if s and "id" in s]
    if not slots:
        return {"items": [], "failed": 0, "recipe_signal": True}

    item_ids = [s["id"] for s in slots]
    item_details, item_failed = api.items_bulk(item_ids)
    prices, _ = api.prices_bulk(item_ids)

    # Ingredient direction: keep what I need in order to craft, not what I could
    # craft. The index is built by the scheduler; if it isn't ready we say so
    # instead of blocking a page render on ~66 upstream calls.
    index = api.cached_recipe_index()
    recipe_signal = index is not None
    needed_for_crafting = set()
    makes: dict[int, list[dict]] = {}
    if recipe_signal:
        craftable = api.craftable_recipe_ids()
        ingredient_map = index["ingredients"]
        needed_for_crafting = {
            iid
            for iid in set(item_ids)
            if any(r in craftable for r in ingredient_map.get(iid, ()))
        }
        makes = _resolve_used_in(set(item_ids), index, craftable)

    result = []
    for slot in slots:
        iid = slot["id"]
        item = item_details.get(iid) or {}
        known = iid not in item_failed and bool(item)
        count = slot.get("count", 1)

        analysis = classify(
            item,
            prices.get(iid),
            iid in needed_for_crafting,
            sell_threshold,
            keep_rarity_min,
            slot=slot,
            count=count,
            price_basis=price_basis,
            known=known,
        )

        rarity = item.get("rarity", "Basic") if known else ""
        result.append(
            {
                "id": iid,
                "slot": slot,
                "item": item,
                "count": count,
                "location": location,
                "name": item.get("name") or f"Item #{iid}",
                "rarity": rarity,
                "rarity_color": RARITY_COLORS.get(rarity, UNKNOWN_COLOR),
                "type": item.get("type", ""),
                "description": item.get("description", ""),
                "icon": item.get("icon", ""),
                "wiki_url": wiki_url(item.get("name") or ""),
                "chat_link": item.get("chat_link", ""),
                "used_in": makes.get(iid, []),
                "recipe_count": len(index["ingredients"].get(iid, ())) if index else 0,
                **analysis,
            }
        )

    return {"items": result, "failed": len(item_failed), "recipe_signal": recipe_signal}


def _resolve_used_in(
    item_ids: set[int], index: dict, craftable: set[int]
) -> dict[int, list[dict]]:
    """Map each held item to the things it can be crafted into.

    Answers "what is this for?" without leaving the page. Recipes the account can
    actually make are listed first, since those are the actionable ones.
    """
    from . import api

    ingredient_map = index["ingredients"]
    recipe_output = index["recipe_output"]

    # Pick the outputs to show per item, then resolve every name in one bulk call.
    picked: dict[int, list[int]] = {}
    wanted: set[int] = set()
    for iid in item_ids:
        recipes = ingredient_map.get(iid) or ()
        if not recipes:
            continue
        outs, seen = [], set()
        for rid in sorted(recipes, key=lambda r: r not in craftable):
            out = recipe_output.get(rid)
            if out and out not in seen:
                seen.add(out)
                outs.append(out)
            if len(outs) >= MAX_USED_IN:
                break
        if outs:
            picked[iid] = outs
            wanted.update(outs)

    if not wanted:
        return {}

    names, _ = api.items_bulk(sorted(wanted))
    result: dict[int, list[dict]] = {}
    for iid, outs in picked.items():
        entries = []
        for out in outs:
            detail = names.get(out)
            if not detail:
                continue
            entries.append(
                {
                    "id": out,
                    "name": detail.get("name") or f"Item #{out}",
                    "rarity_color": RARITY_COLORS.get(detail.get("rarity", ""), "#ffffff"),
                    "wiki_url": wiki_url(detail.get("name") or ""),
                }
            )
        if entries:
            result[iid] = entries
    return result


def group_by_verdict(enriched: list) -> dict:
    """Group items by verdict, most valuable holding first."""
    groups = {v: [] for v in VERDICTS}
    for item in enriched:
        groups.setdefault(item["verdict"], []).append(item)
    for k in groups:
        # Sort on the stack, not the unit price — a 250-stack of cheap materials
        # outranks a single mid-priced item.
        groups[k].sort(key=lambda x: x.get("stack_value", 0), reverse=True)
    return groups


def totals(enriched: list) -> dict:
    sell = [i for i in enriched if i["verdict"] == "sell"]
    return {
        "sell_gross": sum(i["stack_value"] for i in sell),
        "sell_net": sum(i["net_total"] for i in sell),
        "vendor": sum(
            i["vendor_value"] * i["count"] for i in enriched if i["verdict"] == "toss"
        ),
    }


def aggregate_by_item(enriched: list) -> list[dict]:
    """Collapse the same item across every location into one row.

    Stacks are per-slot, so one character holding 323 of something in two slots
    yields two entries for the same place. Counts are summed per location name so
    that reads as "Scrollios x323" rather than "Scrollios x250 | Scrollios x73".
    """
    by_item: dict[int, dict] = {}
    for i in enriched:
        entry = by_item.get(i["id"])
        if entry is None:
            entry = by_item[i["id"]] = {
                "id": i["id"],
                "name": i["name"],
                "icon": i["icon"],
                "rarity": i["rarity"],
                "rarity_color": i["rarity_color"],
                "type": i["type"],
                "verdict": i["verdict"],
                "reason": i["reason"],
                "wiki_url": i["wiki_url"],
                "recipe_count": i["recipe_count"],
                "sell_price": i["sell_price"],
                "sell_price_fmt": i["sell_price_fmt"],
                "total_count": 0,
                "total_value": 0,
                "stacks": 0,
                "_by_location": {},
            }
        entry["total_count"] += i["count"]
        entry["total_value"] += i["stack_value"]
        entry["stacks"] += 1
        loc = i["location"] or "Unknown"
        entry["_by_location"][loc] = entry["_by_location"].get(loc, 0) + i["count"]

    for entry in by_item.values():
        entry["locations"] = sorted(
            ({"where": w, "count": c} for w, c in entry.pop("_by_location").items()),
            key=lambda loc: (-loc["count"], loc["where"]),
        )

    return sorted(by_item.values(), key=lambda r: r["total_value"], reverse=True)


def type_counts(enriched: list) -> list[tuple[str, int]]:
    counts = {}
    for i in enriched:
        counts[i["type"] or "Unknown"] = counts.get(i["type"] or "Unknown", 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
