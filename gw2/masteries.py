"""Mastery point tracking and an ease ranking for what's left to earn.

Masteries are account-wide, not per-character.

The API has **no difficulty field**, so "ease" here is inferred from signals that
genuinely exist — achievement category, progress, and shape — plus one hand-written
judgement call about which content types need a group. That judgement is the only
part not derived from data, and it is kept in HARD_CONTENT below where it can be
argued with rather than buried in a sort key.
"""

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

# Insights are the one category the API effectively labels as trivial: travel to a
# spot and interact. No combat, no group, no RNG.
INSIGHT_CATEGORY = "Mastery Insights"

# The judgement call. These need a group, a schedule, or repeated attempts, so they
# sort last regardless of how few steps they have.
HARD_CONTENT = (
    "raid", "fractal", "strike", "challenge mode", "champions",
    "player vs. player", "world vs. world", "pvp", "wvw",
)

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
    haystack = f"{entry.get('category','')} {entry.get('group','')}".lower()
    return any(h in haystack for h in HARD_CONTENT)


def ease_tier(entry: dict, progress: dict | None) -> int:
    """Lower is easier. See TIER_LABELS."""
    if entry.get("category") == INSIGHT_CATEGORY:
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
                "tier": tier,
                "tier_label": TIER_LABELS[tier],
                "current": (progress or {}).get("current", 0),
                "max": (progress or {}).get("max", 0),
                "fraction": fraction,
                "percent": round(fraction * 100),
                "wiki_url": wiki_url(entry["name"]),
            }
        )

    # Easiest tier first; within a tier, closest to completion, then fewest steps.
    out.sort(key=lambda e: (e["tier"], -e["fraction"], e["bits"] or e["max"] or 0, e["name"]))
    return out


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
