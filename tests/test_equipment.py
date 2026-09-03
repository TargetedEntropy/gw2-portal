"""Tests for conservative equipment comparisons."""

from gw2 import equipment


def armor(iid, rarity="Exotic", defense=100, stats=None, choices=None, weight="Medium"):
    details = {
        "type": "Coat",
        "weight_class": weight,
        "defense": defense,
        "attribute_adjustment": 100,
        "infusion_slots": [],
    }
    if stats:
        details["infix_upgrade"] = {
            "id": iid * 10,
            "attributes": [
                {"attribute": name, "modifier": value} for name, value in stats.items()
            ],
        }
    if choices:
        details["stat_choices"] = choices
    return {
        "id": iid,
        "name": f"Coat {iid}",
        "type": "Armor",
        "rarity": rarity,
        "level": 80,
        "flags": [],
        "restrictions": [],
        "details": details,
    }


def weapon(iid, kind="Rifle", power=900, stats=None):
    return {
        "id": iid,
        "name": f"Weapon {iid}",
        "type": "Weapon",
        "rarity": "Exotic",
        "level": 80,
        "flags": [],
        "restrictions": [],
        "details": {
            "type": kind,
            "min_power": power - 50,
            "max_power": power + 50,
            "infix_upgrade": {
                "id": iid * 10,
                "attributes": [
                    {"attribute": name, "modifier": value}
                    for name, value in (stats or {}).items()
                ],
            },
        },
    }


def test_character_profession_wins_over_inference():
    got = equipment.infer_profession(
        {"profession": "Engineer"}, [], {}, {"Ranger": {"weapons": {"Rifle": {}}}}
    )
    assert got == ("Engineer", "character API", ["Engineer"])


def test_profession_can_be_inferred_from_armor_and_weapon():
    details = {
        1: armor(1, weight="Medium"),
        2: weapon(2, kind="Rifle"),
    }
    professions = {
        "Engineer": {"weapons": {"Rifle": {}}},
        "Ranger": {"weapons": {"LongBow": {}}},
        "Warrior": {"weapons": {"Rifle": {}}},
    }
    got = equipment.infer_profession(
        {}, [{"id": 1}, {"id": 2}], details, professions
    )
    assert got[0] == "Engineer"
    assert got[1].startswith("inferred")


def test_profile_identifies_power_gear():
    profile = equipment.infer_profile(
        [{"Power": 100, "Precision": 70, "CritDamage": 70}] * 6
    )
    assert profile["label"] == "Power / strike damage"
    assert profile["confidence"] == "medium"
    assert profile["priorities"][0]["name"] == "Power"


def test_different_stat_profile_is_not_called_an_upgrade():
    current = armor(1, stats={"Power": 100, "Precision": 70, "CritDamage": 70})
    candidate = armor(
        2, rarity="Ascended", defense=110,
        stats={"ConditionDamage": 120, "Toughness": 80, "Vitality": 80},
    )
    current_tuple = ({"id": 1}, current, {"Power": 100, "Precision": 70, "CritDamage": 70}, 10, "Berserker's")
    profile = equipment.infer_profile([current_tuple[2]])
    assert equipment.compare_piece(
        current_tuple, candidate,
        {"ConditionDamage": 120, "Toughness": 80, "Vitality": 80}, profile
    ) is None


def test_like_for_like_stat_and_defense_gain_is_an_upgrade():
    current = armor(1, defense=100, stats={"Power": 100, "Precision": 70, "CritDamage": 70})
    candidate = armor(2, rarity="Ascended", defense=110,
                      stats={"Power": 110, "Precision": 77, "CritDamage": 77})
    current_tuple = ({"id": 1}, current, {"Power": 100, "Precision": 70, "CritDamage": 70}, 10, "Berserker's")
    profile = equipment.infer_profile([current_tuple[2]])
    got = equipment.compare_piece(
        current_tuple, candidate,
        {"Power": 110, "Precision": 77, "CritDamage": 77}, profile
    )
    assert got is not None
    assert "aligned attributes" in got["reason"]
    assert "defense" in got["reason"]


def test_selectable_piece_is_evaluated_as_the_matching_prefix():
    selectable = armor(2, rarity="Ascended", defense=110, choices=[584])
    stat_defs = {
        584: {
            "id": 584,
            "name": "Berserker's",
            "attributes": [
                {"attribute": "Power", "multiplier": 0.35, "value": 32},
                {"attribute": "Precision", "multiplier": 0.25, "value": 18},
                {"attribute": "CritDamage", "multiplier": 0.25, "value": 18},
            ],
        }
    }
    variants = equipment.candidate_variants({"id": 2}, selectable, stat_defs)
    assert variants == [
        ({"Power": 67, "Precision": 43, "CritDamage": 43}, 584, "Berserker's", True)
    ]


def test_wrong_armor_weight_and_weapon_type_are_incompatible():
    current_armor = armor(1, weight="Medium")
    current_weapon = weapon(2, kind="Rifle")
    assert not equipment.compatible(
        "Coat", ({}, current_armor, {}, None, ""), armor(3, weight="Heavy"), "Medium"
    )
    assert not equipment.compatible(
        "WeaponA1", ({}, current_weapon, {}, None, ""), weapon(4, kind="Pistol"), "Medium"
    )


def test_character_bound_item_for_someone_else_is_rejected():
    instance = {
        "binding": "Character",
        "bound_to": "Alt",
        "_target_character": "Main",
    }
    assert not equipment.usable_by(instance, armor(1), "Engineer", "Human", 80)


def test_empty_infusions_runes_and_sigils_are_reported():
    coat = armor(1)
    coat["details"]["infusion_slots"] = [{"flags": ["Infusion"]}]
    assert equipment.socket_notes({"upgrades": [], "infusions": []}, coat) == [
        "1 empty infusion slot", "1 empty rune slot"
    ]
    rifle = weapon(2)
    assert equipment.socket_notes({"upgrades": []}, rifle) == ["2 empty sigil slots"]


def test_full_report_finds_bank_upgrade_and_not_condition_sidegrade():
    current = armor(1, defense=100, stats={"Power": 100, "Precision": 70, "CritDamage": 70})
    upgrade = armor(2, rarity="Ascended", defense=110,
                    stats={"Power": 110, "Precision": 77, "CritDamage": 77})
    sidegrade = armor(3, rarity="Ascended", defense=110,
                      stats={"ConditionDamage": 120, "Toughness": 80, "Vitality": 80})
    report = equipment.analyze(
        {"name": "Main", "profession": "Engineer", "race": "Human", "level": 80},
        [{"id": 1, "slot": "Coat"}],
        [
            {"slot": {"id": 2}, "location": "Bank", "target_character": "Main"},
            {"slot": {"id": 3}, "location": "Alt", "target_character": "Main"},
        ],
        {1: current, 2: upgrade, 3: sidegrade},
    )
    assert report["profession"] == "Engineer"
    assert report["recommendation_count"] == 1
    assert report["recommendations"][0]["candidates"][0]["name"] == "Coat 2"


def test_analyze_rejects_gear_bound_to_another_character_without_route_metadata():
    current = armor(1, defense=100, stats={"Power": 100, "Precision": 70, "CritDamage": 70})
    upgrade = armor(2, rarity="Ascended", defense=110,
                    stats={"Power": 110, "Precision": 77, "CritDamage": 77})
    report = equipment.analyze(
        {"name": "Main", "profession": "Engineer", "race": "Human", "level": 80},
        [{"id": 1, "slot": "Coat"}],
        [{
            "slot": {"id": 2, "binding": "Character", "bound_to": "Alt"},
            "location": "Alt's equipped gear",
        }],
        {1: current, 2: upgrade},
    )
    assert report["recommendation_count"] == 0


def test_duplicate_candidates_preserve_copy_count_and_locations():
    rows = [
        {"id": 2, "stat_id": 20, "location": "Bank", "available_count": 2},
        {"id": 2, "stat_id": 20, "location": "Alt's bags", "available_count": 1},
    ]
    got = equipment._deduplicate(rows)
    assert got[0]["available_count"] == 3
    assert got[0]["location"] == "Alt's bags · Bank ×2"


def test_legendary_armory_max_count_is_preserved():
    assert equipment._available_count({"id": 2, "max_count": 2}) == 2
