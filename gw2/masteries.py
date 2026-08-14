"""Mastery point tracking and an ease ranking for what's left to earn.

Masteries are account-wide, not per-character.

The API has **no difficulty field**, so "ease" here is inferred from signals that
genuinely exist — achievement category, progress, and shape — plus one hand-written
judgement call about which content types need a group. That judgement is the only
part not derived from data, and it is kept in HARD_CONTENT below where it can be
argued with rather than buried in a sort key.
"""

import re
from urllib.parse import quote

# The reward payload uses short region codes; /v2/account/mastery/points uses
# display names. Neither endpoint documents the mapping.
REGION_CODES = {
    "Central Tyria": "Tyria",
    "Heart of Thorns": "Maguuma",
    "Path of Fire": "Desert",
    "Icebrood Saga": "Tundra",
    "End of Dragons": "Jade",
    "Secrets of the Obscure": "Sky",
    "Janthir Wilds": "Wild",
    "Visions of Eternity": "Magic",
}
REGION_NAMES = {v: k for k, v in REGION_CODES.items()}

# Insights are the one thing the API effectively labels as trivial: travel to a spot
# and interact. No combat, no group, no RNG.
#
# Detection can't rely on the category alone. 209 sit in "Mastery Insights", but
# another 74 live in story categories -- "Champions Insight: Lake Doric" is filed
# under the Icebrood Saga chapter called Champions. The requirement text is the
# reliable signal, since every one of them literally says "Discover this ... Insight".
INSIGHT_CATEGORY = "Mastery Insights"
INSIGHT_NAME_MARKER = " Insight: "

# The judgement call: content needing a group or a schedule, so it sorts last
# regardless of step count.
#
# Matched on the achievement's *group*, which is authoritative, rather than a
# substring across category+group. The looser version misclassified all 19
# achievements in the "Champions" category as raid-tier, when Champions is an
# Icebrood Saga story chapter (group "Story Journal") containing walk-up insights.
# No PvP or WvW group awards mastery points, so none are listed.
HARD_GROUPS = frozenset({"Raids", "Fractals of the Mists", "Strike Missions"})

# "Discover this <Region> Mastery Insight in <the >Map[, extra clause]."
# Stopping at a comma trims trailing directions like ", accessed via asura gate in
# Eye of the North" while keeping the location itself.
_MAP_FROM_REQUIREMENT = re.compile(r"Mastery Insight in (?:the )?([^,.]+)", re.I)

TIER_LABELS = {
    0: "Insight — walk there and interact",
    1: "Nearly done",
    2: "In progress",
    3: "Single step",
    4: "Collection — several steps",
    5: "Group or competitive content",
}


def wiki_url(name: str) -> str:
    return "https://wiki.guildwars2.com/index.php?search=" + quote(name or "", safe="")


def _is_hard(entry: dict) -> bool:
    return entry.get("group") in HARD_GROUPS


def is_insight(entry: dict) -> bool:
    return (
        entry.get("category") == INSIGHT_CATEGORY
        or INSIGHT_NAME_MARKER in entry.get("name", "")
        or "Mastery Insight in" in entry.get("requirement", "")
    )


def insight_map(entry: dict) -> str:
    """The map an insight sits in, for route planning.

    The requirement text wins over the name prefix. The name is usually
    "<Map> Insight: <Spot>", but it is the *chapter* for Icebrood Saga entries --
    "Champions Insight: Lake Doric" is in Lake Doric, not a map called Champions --
    and it abbreviates elsewhere ("New Kaineng" for New Kaineng City). The
    requirement says "...Insight in <Map>" and is right in all those cases.

    Falls back to the name prefix for the 8 of 283 whose requirement doesn't parse.
    """
    found = _MAP_FROM_REQUIREMENT.search(entry.get("requirement", ""))
    if found:
        return found.group(1).strip()
    name = entry.get("name", "")
    if INSIGHT_NAME_MARKER in name:
        return name.split(INSIGHT_NAME_MARKER, 1)[0].strip()
    return ""


def ease_tier(entry: dict, progress: dict | None) -> int:
    """Lower is easier. See TIER_LABELS."""
    if is_insight(entry):
        return 0

    if progress:
        current, target = progress.get("current", 0), progress.get("max", 0)
        if target and current:
            return 1 if current / target >= 0.5 else 2

    if _is_hard(entry):
        return 5
    if entry.get("bits") or (entry.get("target") or 0) > 1:
        return 4
    return 3


def _fraction(progress: dict | None) -> float:
    if not progress:
        return 0.0
    target = progress.get("max") or 0
    return (progress.get("current", 0) / target) if target else 0.0


def remaining_points(
    achievements: list[dict],
    account_achievements: list[dict],
    owned_regions: set[str],
) -> list[dict]:
    """Mastery points still available, easiest first.

    `owned_regions` is region *codes*; anything outside it is dropped, otherwise the
    list fills with points from expansions the account cannot reach.
    """
    progress_by_id = {a["id"]: a for a in account_achievements}

    out = []
    for entry in achievements:
        if entry.get("region") not in owned_regions:
            continue
        progress = progress_by_id.get(entry["id"])
        if progress and progress.get("done"):
            continue

        tier = ease_tier(entry, progress)
        fraction = _fraction(progress)
        out.append(
            {
                **entry,
                "region_name": REGION_NAMES.get(entry.get("region"), entry.get("region", "")),
                "map": insight_map(entry) if tier == 0 else "",
                "tier": tier,
                "tier_label": TIER_LABELS[tier],
                "current": (progress or {}).get("current", 0),
                "max": (progress or {}).get("max", 0),
                "fraction": fraction,
                "percent": round(fraction * 100),
                "wiki_url": wiki_url(entry["name"]),
            }
        )

    # Easiest tier first. Insights then cluster by map so they read as a route
    # rather than a list; everything else goes closest-to-done, then fewest steps.
    out.sort(
        key=lambda e: (
            e["tier"],
            e["map"],
            -e["fraction"],
            e["bits"] or e["max"] or 0,
            e["name"],
        )
    )
    return out


def routes_by_map(rows: list[dict]) -> list[dict]:
    """Group insights into per-map trips, busiest map first.

    A map with eight insights left is one trip worth making; a map with one is an
    errand. Sorting by count puts the efficient trips at the top.
    """
    by_map: dict[str, dict] = {}
    for row in rows:
        if row["tier"] != 0:
            continue
        key = row["map"] or "Unknown location"
        trip = by_map.setdefault(
            key, {"map": key, "region_name": row["region_name"], "insights": []}
        )
        trip["insights"].append(row)

    trips = list(by_map.values())
    trips.sort(key=lambda t: (-len(t["insights"]), t["map"]))
    return trips


def completed_count(achievements: list[dict], account_achievements: list[dict]) -> int:
    done = {a["id"] for a in account_achievements if a.get("done")}
    return sum(1 for e in achievements if e["id"] in done)


def region_summary(points: dict, remaining: list[dict]) -> list[dict]:
    """Per-region earned/spent/unspent, joined to how many points are still out there."""
    rows = []
    for total in points.get("totals", []):
        name = total["region"]
        code = REGION_CODES.get(name, name)
        in_region = [r for r in remaining if r["region"] == code]
        rows.append(
            {
                "region": name,
                "code": code,
                "earned": total.get("earned", 0),
                "spent": total.get("spent", 0),
                "unspent": total.get("earned", 0) - total.get("spent", 0),
                "remaining": len(in_region),
                "insights": sum(1 for r in in_region if r["tier"] == 0),
            }
        )
    return rows


def owned_region_codes(points: dict) -> set[str]:
    """Regions the account can actually progress.

    Derived from /v2/account/mastery/points rather than the `access` list on
    /v2/account, because expansion bundling makes access an unreliable proxy — this
    account shows Heart of Thorns mastery progress without HeartOfThorns in access.
    """
    return {
        REGION_CODES.get(t["region"], t["region"]) for t in points.get("totals", [])
    }
