"""Mastery point ranking tests.

The ease ordering is a heuristic, not API data, so the rules it encodes are pinned
here — including the one hand-written judgement call about group content.
"""

from gw2 import masteries as m


def ach(id=1, name="Thing", region="Tyria", category="", group="", bits=0,
        target=0, requirement="", **kw):
    return dict(id=id, name=name, region=region, category=category, group=group,
                bits=bits, target=target, requirement=requirement,
                type="Default", flags=[], **kw)


def progress(id=1, current=0, max=0, done=False):
    return dict(id=id, current=current, max=max, done=done)


ALL_REGIONS = {"Tyria", "Maguuma", "Desert", "Tundra", "Jade", "Sky", "Wild", "Magic"}


# --- Tiering ----------------------------------------------------------------

def test_insights_are_the_easiest_tier():
    assert m.ease_tier(ach(category=m.INSIGHT_CATEGORY), None) == 0


def test_insight_beats_progress_because_it_is_a_single_interaction():
    tier = m.ease_tier(ach(category=m.INSIGHT_CATEGORY), progress(current=9, max=10))
    assert tier == 0


def test_more_than_half_done_is_nearly_done():
    assert m.ease_tier(ach(), progress(current=5, max=8)) == 1


def test_some_progress_is_in_progress():
    assert m.ease_tier(ach(), progress(current=1, max=10)) == 2


def test_untouched_single_step_is_tier_three():
    assert m.ease_tier(ach(), None) == 3


def test_collections_rank_below_single_steps():
    assert m.ease_tier(ach(bits=16), None) == 4
    assert m.ease_tier(ach(target=100), None) == 4


def test_group_content_ranks_last_regardless_of_step_count():
    """The one judgement call: a one-step raid boss is not 'easy'."""
    for hard in ["Raids", "Fractals of the Mists", "Strike Missions"]:
        assert m.ease_tier(ach(group=hard), None) == 5


def test_hardness_keys_on_the_group_not_the_category():
    """A raid wing's category is its own name ("Spirit Vale"); the group says Raids."""
    assert m.ease_tier(ach(category="Spirit Vale", group="Raids"), None) == 5


def test_champions_story_chapter_is_not_treated_as_group_content():
    """Regression: "Champions" is an Icebrood Saga chapter, not champion bounties.

    A substring match on category+group filed all 19 of its achievements as
    raid-tier -- including walk-up insights, which are the easiest thing there is.
    """
    entry = ach(name="Champions Insight: Lake Doric", category="Champions",
                group="Story Journal",
                requirement="Discover this Icebrood Saga Mastery Insight in Lake Doric.")
    assert m.ease_tier(entry, None) == 0


def test_progress_still_outranks_hard_content():
    """If you're already 60% through a raid achievement, it is genuinely close."""
    assert m.ease_tier(ach(category="Raids"), progress(current=3, max=5)) == 1


# --- Filtering and ordering -------------------------------------------------

def test_completed_achievements_are_excluded():
    out = m.remaining_points([ach(id=1)], [progress(id=1, done=True)], ALL_REGIONS)
    assert out == []


def test_unowned_regions_are_excluded():
    """Otherwise the list fills with points from expansions you cannot reach."""
    out = m.remaining_points([ach(id=1, region="Magic")], [], {"Tyria"})
    assert out == []


def test_owned_regions_come_from_mastery_totals_not_the_access_list():
    pts = {"totals": [{"region": "Heart of Thorns", "earned": 7, "spent": 2}]}
    assert m.owned_region_codes(pts) == {"Maguuma"}


def test_easiest_tier_sorts_first():
    out = m.remaining_points(
        [ach(id=1, name="Raid", category="Raids"),
         ach(id=2, name="Insight", category=m.INSIGHT_CATEGORY)],
        [], ALL_REGIONS,
    )
    assert [r["name"] for r in out] == ["Insight", "Raid"]


def test_within_a_tier_the_closest_to_done_comes_first():
    out = m.remaining_points(
        [ach(id=1, name="Far"), ach(id=2, name="Close")],
        [progress(id=1, current=1, max=10), progress(id=2, current=4, max=10)],
        ALL_REGIONS,
    )
    assert [r["name"] for r in out] == ["Close", "Far"]


def test_ties_break_on_fewest_steps_then_name():
    out = m.remaining_points(
        [ach(id=1, name="Long", bits=20), ach(id=2, name="Short", bits=3)],
        [], ALL_REGIONS,
    )
    assert [r["name"] for r in out] == ["Short", "Long"]


def test_rows_carry_a_readable_region_and_wiki_link():
    out = m.remaining_points([ach(id=1, name="A Thing", region="Maguuma")], [], ALL_REGIONS)
    assert out[0]["region_name"] == "Heart of Thorns"
    assert out[0]["wiki_url"].endswith("A%20Thing")


def test_percent_is_rounded_for_display():
    out = m.remaining_points([ach(id=1)], [progress(id=1, current=1, max=3)], ALL_REGIONS)
    assert out[0]["percent"] == 33


def test_zero_target_does_not_divide_by_zero():
    out = m.remaining_points([ach(id=1)], [progress(id=1, current=0, max=0)], ALL_REGIONS)
    assert out[0]["fraction"] == 0.0


# --- Summaries ---------------------------------------------------------------

def test_region_summary_reports_unspent_points():
    pts = {"totals": [{"region": "Heart of Thorns", "earned": 7, "spent": 2}]}
    rows = m.region_summary(pts, [])
    assert rows[0]["unspent"] == 5


def test_region_summary_counts_remaining_and_insights_per_region():
    pts = {"totals": [{"region": "Central Tyria", "earned": 0, "spent": 0}]}
    remaining = [
        dict(region="Tyria", tier=0), dict(region="Tyria", tier=3),
        dict(region="Maguuma", tier=0),
    ]
    row = m.region_summary(pts, remaining)[0]
    assert row["remaining"] == 2 and row["insights"] == 1


def test_completed_count_only_counts_point_granting_achievements():
    cat = [ach(id=1), ach(id=2)]
    acct = [progress(id=1, done=True), progress(id=99, done=True)]
    assert m.completed_count(cat, acct) == 1


def test_region_code_mapping_round_trips():
    for name, code in m.REGION_CODES.items():
        assert m.REGION_NAMES[code] == name


# --- Insight detection and map extraction -----------------------------------

def test_insight_detected_from_its_own_category():
    assert m.is_insight(ach(category=m.INSIGHT_CATEGORY))


def test_insight_detected_from_the_name_when_filed_under_a_story_chapter():
    assert m.is_insight(ach(name="Amnytas Insight: The Arboretum", category="Amnytas"))


def test_insight_detected_from_the_requirement_when_the_name_gives_nothing():
    """"Thirsty Tourist" is an insight; only its requirement says so."""
    entry = ach(name="Thirsty Tourist",
                requirement="Discover this End of Dragons Mastery Insight in Dragon's End.")
    assert m.is_insight(entry)


def test_ordinary_achievements_are_not_insights():
    assert not m.is_insight(ach(name="Dungeons Discovered",
                                requirement="Complete 8 dungeon stories."))


def test_map_comes_from_the_requirement():
    entry = ach(name="Mistburned Barrens Insight: Alliance Staging",
                requirement="Discover this Janthir Wilds Mastery Insight in Mistburned Barrens.")
    assert m.insight_map(entry) == "Mistburned Barrens"


def test_map_falls_back_to_the_name_prefix_when_the_requirement_is_silent():
    assert m.insight_map(ach(name="Mistburned Barrens Insight: Alliance Staging")) \
        == "Mistburned Barrens"


def test_requirement_beats_the_name_when_the_name_is_a_story_chapter():
    """"Champions Insight: Lake Doric" is in Lake Doric, not a map called Champions."""
    entry = ach(name="Champions Insight: Lake Doric", category="Champions",
                requirement="Discover this Icebrood Saga Mastery Insight in Lake Doric.")
    assert m.insight_map(entry) == "Lake Doric"


def test_requirement_beats_the_name_when_the_name_abbreviates():
    entry = ach(name="New Kaineng Insight: Jade Monument",
                requirement="Discover this End of Dragons Mastery Insight in New Kaineng City.")
    assert m.insight_map(entry) == "New Kaineng City"


def test_trailing_directions_are_trimmed_from_the_map_name():
    entry = ach(name="Champions Insight: Fields of Ruin",
                requirement=("Discover this Icebrood Saga Mastery Insight in Fields of Ruin "
                             "Dragon Response Mission, accessed via asura gate in Eye of the North."))
    assert m.insight_map(entry) == "Fields of Ruin Dragon Response Mission"


def test_map_fallback_strips_a_leading_the():
    entry = ach(name="Odd One",
                requirement="Discover this Crystal Desert Mastery Insight in the Domain of Vabbi.")
    assert m.insight_map(entry) == "Domain of Vabbi"


def test_map_is_blank_when_nothing_identifies_it():
    assert m.insight_map(ach(name="Mystery", requirement="Do a thing.")) == ""


# --- Route planning ----------------------------------------------------------

def _insights(*specs):
    out = []
    for i, (map_name, label) in enumerate(specs):
        out.append(ach(id=i + 1, name=f"{map_name} Insight: {label}",
                       category=m.INSIGHT_CATEGORY, region="Sky"))
    return out


def test_insights_cluster_by_map_so_the_list_reads_as_a_route():
    rows = m.remaining_points(
        _insights(("Amnytas", "B"), ("Skywatch", "A"), ("Amnytas", "A")),
        [], ALL_REGIONS,
    )
    assert [r["map"] for r in rows] == ["Amnytas", "Amnytas", "Skywatch"]


def test_routes_group_insights_per_map():
    rows = m.remaining_points(_insights(("Amnytas", "A"), ("Amnytas", "B")), [], ALL_REGIONS)
    trips = m.routes_by_map(rows)
    assert len(trips) == 1
    assert trips[0]["map"] == "Amnytas" and len(trips[0]["insights"]) == 2


def test_busiest_map_comes_first_because_it_is_the_best_trip():
    rows = m.remaining_points(
        _insights(("Solo", "A"), ("Busy", "A"), ("Busy", "B"), ("Busy", "C")),
        [], ALL_REGIONS,
    )
    trips = m.routes_by_map(rows)
    assert [t["map"] for t in trips] == ["Busy", "Solo"]


def test_routes_ignore_non_insight_rows():
    rows = m.remaining_points(
        [ach(id=1, name="Dungeons Discovered")] + _insights(("Amnytas", "A")),
        [], ALL_REGIONS,
    )
    trips = m.routes_by_map(rows)
    assert len(trips) == 1 and trips[0]["map"] == "Amnytas"


def test_unlocatable_insights_still_get_a_bucket():
    rows = m.remaining_points(
        [ach(id=1, name="Mystery", category=m.INSIGHT_CATEGORY, requirement="Do a thing.")],
        [], ALL_REGIONS,
    )
    assert m.routes_by_map(rows)[0]["map"] == "Unknown location"


def test_only_insights_carry_a_map():
    rows = m.remaining_points([ach(id=1, name="Dungeons Discovered")], [], ALL_REGIONS)
    assert rows[0]["map"] == ""
