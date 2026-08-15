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


# --- Zoom probing ------------------------------------------------------------

def test_probe_returns_the_highest_zoom_that_actually_serves_tiles():
    """Janthir Syntri publishes tiles only up to zoom 4; the budget wanted 6."""
    def exists(url):
        return "/4/" in url or "/3/" in url
    assert md.probe_zoom(DH_RECT, 1, 1, MAX_ZOOM, exists) == 4


def test_probe_gives_up_when_no_zoom_has_tiles():
    """Bava Nisos has no imagery at all; the page must fall back, not 404 in a grid."""
    assert md.probe_zoom(DH_RECT, 1, 1, MAX_ZOOM, lambda url: False) is None


def test_probe_prefers_a_fully_covered_zoom_over_a_patchy_higher_one():
    """A centre-only check accepts a zoom that renders with holes around the rim."""
    def exists(url):
        if "/6/" in url:
            return url.endswith("/38.jpg")   # only some tiles at zoom 6
        return "/5/" in url

    assert md.probe_zoom(DH_RECT, 1, 1, MAX_ZOOM, exists) == 5


def test_probe_accepts_partial_coverage_rather_than_giving_up():
    """Janthir Syntri is patchy at every zoom; 14 of 16 tiles beats no map at all."""
    # At zoom 4 this map spans tiles x13-15, y9-10, so this covers the top row only.
    def exists(url):
        return "/4/" in url and url.endswith("/9.jpg")

    assert md.probe_zoom(DH_RECT, 1, 1, MAX_ZOOM, exists) == 4


def test_probe_checks_more_than_one_tile():
    calls = []
    md.probe_zoom(DH_RECT, 1, 1, MAX_ZOOM, lambda u: calls.append(u) or True)
    assert len(calls) > 1


def test_zoom_never_exceeds_what_the_tile_server_publishes():
    """Zoom 8 404s even on maps that are otherwise complete."""
    tiny = [[1000, 1000], [1100, 1100]]
    assert md.choose_zoom(tiny, MAX_ZOOM) <= md.HIGHEST_SERVED_ZOOM


def test_partial_fallback_picks_the_best_covered_zoom_not_the_highest():
    """Coverage thins as zoom rises, so the highest partial zoom is the patchiest."""
    def exists(url):
        if "/6/" in url:
            return url.endswith("/38.jpg")          # barely any coverage
        if "/5/" in url:
            return not url.endswith("/20.jpg")      # most tiles present
        return False

    assert md.probe_zoom(DH_RECT, 1, 1, MAX_ZOOM, exists) == 5
