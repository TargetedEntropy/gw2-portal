"""Conservative, pure equipment comparison logic.

The GW2 API can tell us what a character owns and wears, but not what encounter
or rotation the player intends to use.  This module therefore finds *like for
like* improvements: compatible pieces whose stat distribution closely matches
the currently equipped piece (or the character's aggregate gear profile).
"""

from __future__ import annotations

import math


RARITY_ORDER = {
    "Junk": 0,
    "Basic": 1,
    "Fine": 2,
    "Masterwork": 3,
    "Rare": 4,
    "Exotic": 5,
    "Ascended": 6,
    "Legendary": 7,
}

PROFESSION_WEIGHTS = {
    "Guardian": "Heavy",
    "Warrior": "Heavy",
    "Revenant": "Heavy",
    "Engineer": "Medium",
    "Ranger": "Medium",
    "Thief": "Medium",
    "Elementalist": "Light",
    "Mesmer": "Light",
    "Necromancer": "Light",
}

PROFESSION_NAMES = set(PROFESSION_WEIGHTS)
RACE_NAMES = {"Asura", "Charr", "Human", "Norn", "Sylvari"}

CORE_SLOTS = (
    "Helm",
    "Shoulders",
    "Coat",
    "Gloves",
    "Leggings",
    "Boots",
    "Backpack",
    "Accessory1",
    "Accessory2",
    "Amulet",
    "Ring1",
    "Ring2",
)

SLOT_LABELS = {
    "Helm": "Head",
    "Shoulders": "Shoulders",
    "Coat": "Chest",
    "Gloves": "Hands",
    "Leggings": "Legs",
    "Boots": "Feet",
    "Backpack": "Back",
    "Accessory1": "Accessory 1",
    "Accessory2": "Accessory 2",
    "Amulet": "Amulet",
    "Ring1": "Ring 1",
    "Ring2": "Ring 2",
    "HelmAquatic": "Aquatic helm",
    "WeaponA1": "Weapon set 1",
    "WeaponA2": "Weapon set 1 off hand",
    "WeaponB1": "Weapon set 2",
    "WeaponB2": "Weapon set 2 off hand",
    "WeaponAquaticA": "Aquatic weapon 1",
    "WeaponAquaticB": "Aquatic weapon 2",
}

ATTRIBUTE_LABELS = {
    "Power": "Power",
    "Precision": "Precision",
    "CritDamage": "Ferocity",
    "ConditionDamage": "Condition damage",
    "ConditionDuration": "Expertise",
    "Healing": "Healing power",
    "BoonDuration": "Concentration",
    "Toughness": "Toughness",
    "Vitality": "Vitality",
    "AgonyResistance": "Agony resistance",
}

STRIKE = {"Power", "Precision", "CritDamage"}
CONDITION = {"ConditionDamage", "ConditionDuration"}
SUPPORT = {"Healing", "BoonDuration"}
DEFENSE = {"Toughness", "Vitality"}

EQUIPMENT_TYPES = {"Armor", "Back", "Trinket", "Weapon"}
TWO_HANDED = {
    "Greatsword",
    "Hammer",
    "LongBow",
    "Rifle",
    "ShortBow",
    "Staff",
    "Harpoon",
    "Speargun",
    "Trident",
    "Spear",
}


def analyze(
    char: dict,
    equipped: list[dict],
    holdings: list[dict],
    details: dict[int, dict],
    stat_defs: dict[int, dict] | None = None,
    profession_defs: dict[str, dict] | None = None,
) -> dict:
    """Build an equipment report from already-fetched API data.

    ``holdings`` entries are ``{"slot": <API slot>, "location": <label>}``.
    The function performs no I/O, which keeps recommendations deterministic and
    makes a failed upstream lookup degrade to an explicit warning rather than a
    confident but invented answer.
    """
    stat_defs = stat_defs or {}
    profession_defs = profession_defs or {}
    character_name = char.get("name")
    if character_name:
        for holding in holdings:
            holding.setdefault("target_character", character_name)
    known_equipped = [e for e in equipped if details.get(e.get("id"))]

    profession, profession_source, profession_options = infer_profession(
        char, known_equipped, details, profession_defs
    )
    weight = PROFESSION_WEIGHTS.get(profession) or infer_armor_weight(known_equipped, details)
    level = int(char.get("level") or 80)
    race = char.get("race")

    equipped_rows = []
    equipped_by_slot = {}
    for slot in known_equipped:
        detail = details[slot["id"]]
        attrs, stat_id, stat_name = resolved_attributes(slot, detail, stat_defs)
        row = _display_item(slot, detail, attrs, stat_id, stat_name, "Equipped")
        row["slot_name"] = SLOT_LABELS.get(slot.get("slot"), slot.get("slot", "Unknown"))
        row["sockets"] = socket_notes(slot, detail)
        equipped_rows.append(row)
        equipped_by_slot[slot.get("slot")] = (slot, detail, attrs, stat_id, stat_name)

    profile = infer_profile([r[2] for r in equipped_by_slot.values()])

    candidates, skipped_unknown = _candidate_instances(
        holdings, details, profession, race, level
    )
    held_equipment_count = sum(_available_count(instance) for instance, _ in candidates)
    recommendations = []
    occupied_unique = {e.get("id") for e in equipped if "Unique" in (details.get(e.get("id"), {}).get("flags") or [])}

    slots = list(CORE_SLOTS)
    slots.extend(
        s for s in equipped_by_slot if s not in slots
    )
    for slot_name in slots:
        current = equipped_by_slot.get(slot_name)
        # A two-handed weapon intentionally leaves its paired off-hand absent.
        if current is None and _blocked_by_two_hander(slot_name, equipped_by_slot):
            continue

        matches = []
        for instance, candidate in candidates:
            if not compatible(slot_name, current, candidate, weight):
                continue
            if "Unique" in (candidate.get("flags") or []) and candidate.get("id") in occupied_unique:
                continue
            variants = candidate_variants(instance, candidate, stat_defs)
            if not variants:
                continue
            for attrs, stat_id, stat_name, selectable in variants:
                compared = compare_piece(current, candidate, attrs, profile)
                if compared is None:
                    continue
                row = _display_item(
                    instance, candidate, attrs, stat_id, stat_name, instance.get("_location", "")
                )
                row.update(compared)
                row["selectable"] = selectable
                matches.append(row)

        matches = _deduplicate(matches)
        matches.sort(
            key=lambda r: (r["gain"], RARITY_ORDER.get(r["rarity"], -1), r["name"]),
            reverse=True,
        )
        if not matches:
            continue

        current_display = None
        if current:
            current_display = _display_item(
                current[0], current[1], current[2], current[3], current[4], "Equipped"
            )
        recommendations.append(
            {
                "slot": slot_name,
                "slot_name": SLOT_LABELS.get(slot_name, slot_name),
                "current": current_display,
                "candidates": matches[:3],
            }
        )

    return {
        "profession": profession or "Unknown",
        "profession_source": profession_source,
        "profession_options": profession_options,
        "armor_weight": weight or "Unknown",
        "profile": profile,
        "equipped": equipped_rows,
        "recommendations": recommendations,
        "recommendation_count": sum(len(r["candidates"]) for r in recommendations),
        "holding_count": len(holdings),
        "equipment_candidate_count": held_equipment_count,
        "unknown_count": skipped_unknown + (len(equipped) - len(known_equipped)),
        "socket_notes": [
            {"slot": r["slot_name"], "name": r["name"], "notes": r["sockets"]}
            for r in equipped_rows
            if r["sockets"]
        ],
    }


def infer_profession(
    char: dict,
    equipped: list[dict],
    details: dict[int, dict],
    profession_defs: dict[str, dict],
) -> tuple[str | None, str, list[str]]:
    """Use character core data first; fall back to equipped armor and weapons."""
    direct = char.get("profession")
    if direct:
        return direct, "character API", [direct]

    weight = infer_armor_weight(equipped, details)
    possible = {
        p for p, pweight in PROFESSION_WEIGHTS.items() if not weight or pweight == weight
    }
    weapon_types = {
        (details.get(e.get("id"), {}).get("details") or {}).get("type")
        for e in equipped
        if details.get(e.get("id"), {}).get("type") == "Weapon"
    }
    for weapon in weapon_types - {None}:
        users = {
            p for p, definition in profession_defs.items()
            if weapon in (definition.get("weapons") or {})
        }
        if users:
            possible &= users

    options = sorted(possible)
    if len(options) == 1:
        return options[0], "inferred from equipped armor and weapons", options
    return None, "equipment inference was not unique", options


def infer_armor_weight(equipped: list[dict], details: dict[int, dict]) -> str | None:
    weights = []
    for slot in equipped:
        detail = details.get(slot.get("id"), {})
        if detail.get("type") == "Armor":
            weight = (detail.get("details") or {}).get("weight_class")
            if weight in {"Heavy", "Medium", "Light"}:
                weights.append(weight)
    return max(set(weights), key=weights.count) if weights else None


def infer_profile(attribute_sets: list[dict]) -> dict:
    totals = {name: 0 for name in ATTRIBUTE_LABELS}
    pieces = 0
    for attrs in attribute_sets:
        if attrs:
            pieces += 1
        for name, value in attrs.items():
            if name in totals:
                totals[name] += max(0, int(value or 0))

    category = {
        "Power / strike damage": sum(totals[a] for a in STRIKE),
        "Condition damage": sum(totals[a] for a in CONDITION),
        "Support / healing": sum(totals[a] for a in SUPPORT),
        "Defense / survival": sum(totals[a] for a in DEFENSE),
    }
    total = sum(category.values())
    ordered_categories = sorted(category.items(), key=lambda x: x[1], reverse=True)
    top_name, top_value = ordered_categories[0]
    second_value = ordered_categories[1][1]
    share = top_value / total if total else 0
    if not total:
        label = "Unknown"
    elif share >= 0.46 and top_value >= second_value * 1.35:
        label = top_name
    else:
        label = "Hybrid"

    if pieces >= 8 and share >= 0.46:
        confidence = "high"
    elif pieces >= 4 and total:
        confidence = "medium"
    else:
        confidence = "low"

    priorities = [
        {"name": ATTRIBUTE_LABELS[name], "value": value}
        for name, value in sorted(totals.items(), key=lambda x: x[1], reverse=True)
        if value > 0
    ][:5]
    return {
        "label": label,
        "confidence": confidence,
        "pieces": pieces,
        "totals": totals,
        "priorities": priorities,
    }


def resolved_attributes(
    instance: dict, detail: dict, stat_defs: dict[int, dict]
) -> tuple[dict, int | None, str]:
    selected = instance.get("stats") or {}
    if selected.get("attributes"):
        stat_id = selected.get("id")
        return (
            _clean_attrs(selected["attributes"]),
            stat_id,
            (stat_defs.get(stat_id) or {}).get("name", "Selected stats"),
        )

    infix = (detail.get("details") or {}).get("infix_upgrade") or {}
    attrs = {
        row.get("attribute"): row.get("modifier", 0)
        for row in infix.get("attributes") or []
        if row.get("attribute")
    }
    stat_id = infix.get("id")
    name = (stat_defs.get(stat_id) or {}).get("name", "")
    return _clean_attrs(attrs), stat_id, name


def candidate_variants(
    instance: dict, detail: dict, stat_defs: dict[int, dict]
) -> list[tuple[dict, int | None, str, bool]]:
    attrs, stat_id, stat_name = resolved_attributes(instance, detail, stat_defs)
    if attrs:
        return [(attrs, stat_id, stat_name, False)]

    d = detail.get("details") or {}
    adjustment = float(d.get("attribute_adjustment") or 0)
    variants = []
    for choice in d.get("stat_choices") or []:
        definition = stat_defs.get(choice)
        if not definition:
            continue
        calculated = {
            row["attribute"]: round(
                adjustment * float(row.get("multiplier") or 0) + int(row.get("value") or 0)
            )
            for row in definition.get("attributes") or []
            if row.get("attribute")
        }
        if calculated:
            variants.append((_clean_attrs(calculated), choice, definition.get("name", "Selectable"), True))
    return variants


def compare_piece(current, candidate: dict, candidate_attrs: dict, profile: dict) -> dict | None:
    """Return comparison fields only when the candidate is a defensible upgrade."""
    if current:
        _, current_detail, current_attrs, _, _ = current
        reference = current_attrs or profile["totals"]
    else:
        current_detail = {}
        current_attrs = {}
        reference = profile["totals"]

    if not candidate_attrs or not reference:
        return None

    similarity = cosine_similarity(candidate_attrs, reference)
    threshold = 0.965 if current_attrs else 0.90
    if similarity < threshold:
        return None

    candidate_stats = sum(candidate_attrs.values())
    current_stats = sum(current_attrs.values())
    candidate_core = core_value(candidate)
    current_core = core_value(current_detail)

    if current:
        # A candidate that cuts an attribute in the apparent existing profile is a
        # build change, not a safe upgrade. Keep it out of this tool.
        for name, value in current_attrs.items():
            if value >= 10 and candidate_attrs.get(name, 0) < value * 0.97:
                return None
        stat_gain = candidate_stats - current_stats
        core_gain = candidate_core - current_core
        meaningful_stats = stat_gain >= max(2, current_stats * 0.015)
        meaningful_core = core_gain >= max(2, current_core * 0.015)
        if not meaningful_stats and not meaningful_core:
            return None
        if candidate_core and current_core and candidate_core < current_core:
            return None
        baseline = max(1.0, current_stats + current_core)
        gain = ((candidate_stats + candidate_core) - baseline) / baseline * 100
        reason_bits = []
        if meaningful_stats:
            reason_bits.append(f"+{stat_gain} aligned attributes")
        if meaningful_core:
            noun = "weapon strength" if candidate.get("type") == "Weapon" else "defense"
            reason_bits.append(f"+{core_gain} {noun}")
        reason = " · ".join(reason_bits)
    else:
        gain = 100.0
        reason = "Fills an empty slot and matches the inferred stat profile"

    return {
        "gain": round(gain, 1),
        "match": round(similarity * 100),
        "reason": reason,
    }


def compatible(slot_name: str, current, candidate: dict, weight: str | None) -> bool:
    if candidate.get("type") not in EQUIPMENT_TYPES:
        return False
    d = candidate.get("details") or {}
    ctype = candidate.get("type")

    if current:
        current_detail = current[1]
        if ctype != current_detail.get("type"):
            return False
        cd = current_detail.get("details") or {}
        if ctype in {"Armor", "Trinket", "Weapon"} and d.get("type") != cd.get("type"):
            return False
        if ctype == "Armor" and d.get("weight_class") != cd.get("weight_class"):
            return False
        return True

    if slot_name in {"Helm", "Shoulders", "Coat", "Gloves", "Leggings", "Boots"}:
        return ctype == "Armor" and d.get("type") == slot_name and (
            not weight or d.get("weight_class") == weight
        )
    if slot_name == "Backpack":
        return ctype == "Back"
    if slot_name.startswith("Accessory"):
        return ctype == "Trinket" and d.get("type") == "Accessory"
    if slot_name.startswith("Ring"):
        return ctype == "Trinket" and d.get("type") == "Ring"
    if slot_name == "Amulet":
        return ctype == "Trinket" and d.get("type") == "Amulet"
    if slot_name == "HelmAquatic":
        return ctype == "Armor" and d.get("type") == "HelmAquatic" and (
            not weight or d.get("weight_class") == weight
        )
    return False


def usable_by(
    instance: dict,
    detail: dict,
    profession: str | None,
    race: str | None,
    level: int,
) -> bool:
    if int(detail.get("level") or 0) > level:
        return False
    if instance.get("binding") == "Character":
        bound_to = instance.get("bound_to")
        target = instance.get("_target_character")
        if bound_to and target and bound_to != target:
            return False

    restrictions = set(detail.get("restrictions") or [])
    profession_restrictions = restrictions & PROFESSION_NAMES
    race_restrictions = restrictions & RACE_NAMES
    if profession_restrictions and profession not in profession_restrictions:
        return False
    if race_restrictions and race not in race_restrictions:
        return False
    return True


def socket_notes(instance: dict, detail: dict) -> list[str]:
    notes = []
    d = detail.get("details") or {}
    available_infusions = len(d.get("infusion_slots") or [])
    used_infusions = len([i for i in (instance.get("infusions") or []) if i])
    if available_infusions > used_infusions:
        missing = available_infusions - used_infusions
        notes.append(f"{missing} empty infusion slot{'s' if missing != 1 else ''}")

    flags = set(detail.get("flags") or [])
    upgrades = len([u for u in (instance.get("upgrades") or []) if u])
    if "NotUpgradeable" not in flags:
        expected = 0
        if detail.get("type") == "Armor":
            expected = 1
        elif detail.get("type") == "Weapon":
            expected = 2 if d.get("type") in TWO_HANDED else 1
        if expected > upgrades:
            missing = expected - upgrades
            component = "rune" if detail.get("type") == "Armor" else "sigil"
            notes.append(f"{missing} empty {component} slot{'s' if missing != 1 else ''}")
    return notes


def cosine_similarity(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    dot = sum(float(a.get(k, 0)) * float(b.get(k, 0)) for k in keys)
    mag_a = math.sqrt(sum(float(a.get(k, 0)) ** 2 for k in keys))
    mag_b = math.sqrt(sum(float(b.get(k, 0)) ** 2 for k in keys))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def core_value(detail: dict) -> float:
    d = detail.get("details") or {}
    if detail.get("type") == "Weapon":
        return (float(d.get("min_power") or 0) + float(d.get("max_power") or 0)) / 2
    if detail.get("type") == "Armor":
        return float(d.get("defense") or 0)
    return 0.0


def stat_ids(instances: list[dict], details: dict[int, dict]) -> set[int]:
    """All itemstat ids needed to resolve fixed, selected and selectable gear."""
    out = set()
    for instance in instances:
        selected = instance.get("stats") or {}
        if selected.get("id"):
            out.add(selected["id"])
        d = (details.get(instance.get("id"), {}).get("details") or {})
        infix = d.get("infix_upgrade") or {}
        if infix.get("id"):
            out.add(infix["id"])
        out.update(d.get("stat_choices") or [])
    return out


def _candidate_instances(holdings, details, profession, race, level):
    candidates = []
    unknown = 0
    for holding in holdings:
        instance = dict(holding.get("slot") or {})
        detail = details.get(instance.get("id"))
        if not detail:
            if instance.get("id"):
                unknown += 1
            continue
        if detail.get("type") not in EQUIPMENT_TYPES:
            continue
        instance["_location"] = holding.get("location") or "Unknown"
        instance["_target_character"] = holding.get("target_character")
        if usable_by(instance, detail, profession, race, level):
            candidates.append((instance, detail))
    return candidates, unknown


def _clean_attrs(attrs: dict) -> dict:
    return {
        str(name): int(value or 0)
        for name, value in (attrs or {}).items()
        if value and str(name) in ATTRIBUTE_LABELS
    }


def _display_item(instance, detail, attrs, stat_id, stat_name, location):
    return {
        "id": detail.get("id"),
        "name": detail.get("name") or f"Item #{detail.get('id', '?')}",
        "icon": detail.get("icon", ""),
        "rarity": detail.get("rarity", ""),
        "level": detail.get("level", 0),
        "type": (detail.get("details") or {}).get("type") or detail.get("type", ""),
        "location": location,
        "available_count": _available_count(instance),
        "stat_id": stat_id,
        "stat_name": stat_name or "Stats not named",
        "attributes": [
            {"name": ATTRIBUTE_LABELS.get(name, name), "value": value}
            for name, value in sorted(attrs.items(), key=lambda x: x[1], reverse=True)
        ],
    }


def _deduplicate(rows: list[dict]) -> list[dict]:
    by_key = {}
    for row in rows:
        key = (row["id"], row.get("stat_id"))
        existing = by_key.get(key)
        if existing is None:
            row["locations"] = {row["location"]: row["available_count"]}
            by_key[key] = row
        else:
            existing["locations"][row["location"]] = (
                existing["locations"].get(row["location"], 0) + row["available_count"]
            )
            existing["available_count"] += row["available_count"]
    for row in by_key.values():
        locations = row.pop("locations")
        row["location"] = " · ".join(
            f"{name} ×{count}" if count > 1 else name
            for name, count in sorted(locations.items())
        )
    return list(by_key.values())


def _blocked_by_two_hander(slot_name: str, equipped_by_slot: dict) -> bool:
    pair = {"WeaponA2": "WeaponA1", "WeaponB2": "WeaponB1"}.get(slot_name)
    if not pair or pair not in equipped_by_slot:
        return False
    detail = equipped_by_slot[pair][1]
    return (detail.get("details") or {}).get("type") in TWO_HANDED


def _available_count(instance: dict) -> int:
    """Physical stack count or Legendary Armory per-template capacity."""
    return max(1, int(instance.get("count") or instance.get("max_count") or 1))
