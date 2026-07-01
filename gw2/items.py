from . import api

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

BOUND_FLAGS = {"AccountBound", "AccountBindOnUse", "SoulbindOnAcquire", "SoulboundOnUse"}


def rarity_index(rarity: str) -> int:
    try:
        return RARITY_ORDER.index(rarity)
    except ValueError:
        return 0


def copper_to_gold(copper: int) -> str:
    g, rem = divmod(abs(copper), 10000)
    s, c = divmod(rem, 100)
    parts = []
    if g:
        parts.append(f"{g}g")
    if s:
        parts.append(f"{s}s")
    if c or not parts:
        parts.append(f"{c}c")
    return " ".join(parts)


def classify(
    item: dict,
    price: dict | None,
    in_unlocked_recipes: bool,
    sell_threshold: int,
    keep_rarity_min: int,
) -> dict:
    """
    Returns a dict with keys: verdict ('keep'|'sell'|'toss'), reason, sell_price, vendor_value.
    """
    flags = set(item.get("flags", []))
    rarity = item.get("rarity", "Basic")
    vendor_value = item.get("vendor_value", 0)
    rid = rarity_index(rarity)

    # --- KEEP signals ---
    if flags & BOUND_FLAGS:
        return _verdict("keep", "Account/soulbound — can't be traded", price, vendor_value)

    if rid >= keep_rarity_min:
        return _verdict("keep", f"{rarity} rarity (≥ keep threshold)", price, vendor_value)

    if in_unlocked_recipes:
        return _verdict("keep", "Used in an unlocked recipe", price, vendor_value)

    # --- SELL signal ---
    if price:
        sell_price = price.get("sells", {}).get("unit_price", 0)
        net = int(sell_price * 0.85) - vendor_value
        if net > sell_threshold:
            return _verdict("sell", f"TP net {copper_to_gold(net)} over vendor", price, vendor_value, sell_price)

    # --- TOSS ---
    return _verdict("toss", "Low value — vendor or salvage", price, vendor_value)


def _verdict(verdict, reason, price, vendor_value, sell_price=None):
    if sell_price is None and price:
        sell_price = price.get("sells", {}).get("unit_price", 0)
    return {
        "verdict": verdict,
        "reason": reason,
        "sell_price": sell_price or 0,
        "sell_price_fmt": copper_to_gold(sell_price or 0),
        "vendor_value": vendor_value,
        "vendor_value_fmt": copper_to_gold(vendor_value),
    }


def enrich_slots(slots: list[dict | None], sell_threshold: int, keep_rarity_min: int) -> list[dict]:
    """
    Takes a list of inventory/bank slot dicts (may be None for empty slots).
    Returns enriched list with item details and verdicts.
    """
    item_ids = [s["id"] for s in slots if s and "id" in s]
    if not item_ids:
        return []

    item_details = api.items_bulk(item_ids)
    prices = api.prices_bulk(item_ids)

    try:
        unlocked_recipe_ids = set(api.account_recipes())
    except Exception:
        unlocked_recipe_ids = set()

    # Build set of item IDs that appear as outputs in unlocked recipes
    recipe_output_items: set[int] = set()
    for iid in item_ids:
        try:
            recipe_ids = api.recipes_for_output(iid)
            if any(r in unlocked_recipe_ids for r in recipe_ids):
                recipe_output_items.add(iid)
        except Exception:
            pass

    result = []
    for slot in slots:
        if not slot or "id" not in slot:
            continue
        iid = slot["id"]
        item = item_details.get(iid, {})
        price = prices.get(iid)
        in_recipe = iid in recipe_output_items

        analysis = classify(item, price, in_recipe, sell_threshold, keep_rarity_min)

        result.append({
            "slot": slot,
            "item": item,
            "count": slot.get("count", 1),
            "name": item.get("name", f"Item #{iid}"),
            "rarity": item.get("rarity", "Basic"),
            "rarity_color": RARITY_COLORS.get(item.get("rarity", "Basic"), "#ffffff"),
            "type": item.get("type", ""),
            "description": item.get("description", ""),
            "icon": item.get("icon", ""),
            **analysis,
        })

    return result
