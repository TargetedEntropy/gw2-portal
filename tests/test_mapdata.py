"""Tile maths tests.

Pins landing in the wrong place is the failure mode that looks plausible and is
wrong, so the arithmetic is pinned against Desert Highlands, whose real values were
taken from the live API: continent_rect [[57256, 39744], [62376, 42304]], continent
max_zoom 8, tiles confirmed present at zoom 5 for x27-30 y19-20.
"""

from gw2 import mapdata as md


DH_RECT = [[57256, 39744], [62376, 42304]]
MAX_ZOOM = 8


def test_scale_halves_per_zoom_level():
    assert md.scale_for(8, 8) == 1
    assert md.scale_for(7, 8) == 2
    assert md.scale_for(5, 8) == 8


def test_tile_grid_matches_the_tiles_that_actually_exist():
    """These exact tiles returned HTTP 200 from the live tile server."""
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    assert (grid["tx0"], grid["tx1"]) == (27, 30)
    assert (grid["ty0"], grid["ty1"]) == (19, 20)


def test_grid_dimensions_follow_from_the_tile_range():
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    assert grid["cols"] == 4 and grid["rows"] == 2
    assert grid["width"] == 4 * 256 and grid["height"] == 2 * 256
    assert len(grid["tiles"]) == 8


def test_tiles_are_laid_out_left_to_right_top_to_bottom():
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    first, second = grid["tiles"][0], grid["tiles"][1]
    assert (first["left"], first["top"]) == (0, 0)
    assert (second["left"], second["top"]) == (256, 0)
    assert grid["tiles"][4]["top"] == 256  # start of the second row


def test_origin_is_the_tile_boundary_not_the_map_edge():
    """Pins measure from the grid origin; using the map's own edge shifts them."""
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    assert grid["origin"] == (27 * 256, 19 * 256)


def test_a_pin_at_the_map_corner_sits_inside_the_grid():
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    pos = md.pin_position(DH_RECT[0], grid, MAX_ZOOM)
    assert 0 <= pos["left"] <= grid["width"]
    assert 0 <= pos["top"] <= grid["height"]


def test_real_mastery_point_lands_within_the_rendered_map():
    """A live Desert Highlands mastery point coordinate."""
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    pos = md.pin_position([60728.5, 42115.5], grid, MAX_ZOOM)
    assert 0 <= pos["left_pct"] <= 100
    assert 0 <= pos["top_pct"] <= 100


def test_percentages_track_the_pixel_offsets():
    grid = md.tile_grid(DH_RECT, zoom=5, max_zoom=MAX_ZOOM)
    pos = md.pin_position([60728.5, 42115.5], grid, MAX_ZOOM)
    assert pos["left_pct"] == 100.0 * pos["left"] / grid["width"]
    assert pos["top_pct"] == 100.0 * pos["top"] / grid["height"]


def test_pins_keep_their_relative_order_across_zoom_levels():
    west = [57500, 40000]
    east = [62000, 40000]
    for zoom in (4, 5, 6):
        grid = md.tile_grid(DH_RECT, zoom, MAX_ZOOM)
        assert (md.pin_position(west, grid, MAX_ZOOM)["left_pct"]
                < md.pin_position(east, grid, MAX_ZOOM)["left_pct"])


def test_zoom_choice_respects_the_tile_budget():
    zoom = md.choose_zoom(DH_RECT, MAX_ZOOM, budget=24)
    grid = md.tile_grid(DH_RECT, zoom, MAX_ZOOM)
    assert grid["cols"] * grid["rows"] <= 24


def test_a_bigger_budget_buys_more_detail():
    assert md.choose_zoom(DH_RECT, MAX_ZOOM, budget=64) \
        >= md.choose_zoom(DH_RECT, MAX_ZOOM, budget=8)


def test_zoom_never_drops_below_the_floor():
    assert md.choose_zoom(DH_RECT, MAX_ZOOM, budget=1) >= 3


def test_tile_url_uses_the_documented_shape():
    assert md.tile_url(1, 49, 5, 27, 19) == "https://tiles.guildwars2.com/1/49/5/27/19.jpg"


def test_tiny_map_still_yields_at_least_one_tile():
    grid = md.tile_grid([[1000, 1000], [1001, 1001]], zoom=5, max_zoom=MAX_ZOOM)
    assert grid["cols"] >= 1 and grid["rows"] >= 1 and grid["tiles"]
