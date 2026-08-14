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
    for hard in ["Raids", "Fractals of the Mists", "Strike Missions", "Champions"]:
        assert m.ease_tier(ach(category=hard), None) == 5


def test_group_content_is_detected_from_the_group_name_too():
    assert m.ease_tier(ach(group="Player vs. Player"), None) == 5


def test_hard_content_detection_is_case_insensitive():
    assert m.ease_tier(ach(category="RAIDS"), None) == 5


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
