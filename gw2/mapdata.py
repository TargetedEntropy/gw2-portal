"""Tile maths for rendering GW2 map tiles with mastery-point pins.

Tiles come from https://tiles.guildwars2.com/{continent}/{floor}/{zoom}/{x}/{y}.jpg
and are 256x256. Continent coordinates are expressed at max zoom, so a coordinate is
scaled down by 2^(max_zoom - zoom) before being turned into a tile or pixel offset.

Two floors are involved and they are not the same one:
  * tiles are served from the map's `default_floor`
  * mastery point coordinates live on whichever floor actually carries them
Desert Highlands is the clearest case — tiles on floor 1, coordinates on floor 49.
"""

TILE_PX = 256
DEFAULT_ZOOM = 5
TILE_URL = "https://tiles.guildwars2.com/{continent}/{floor}/{zoom}/{x}/{y}.jpg"


def scale_for(zoom: int, max_zoom: int) -> int:
    return 2 ** (max_zoom - zoom)


def tile_grid(continent_rect: list, zoom: int, max_zoom: int) -> dict:
    """Tiles covering a map's bounding box, plus the rendered image's dimensions.

    `origin` is the top-left of the tile grid in scaled pixels, which is what pin
    positions are measured against — the grid starts at a tile boundary, not at the
    map's own edge.
    """
    (x0, y0), (x1, y1) = continent_rect
    scale = scale_for(zoom, max_zoom)

    tx0, ty0 = int(x0 / scale) // TILE_PX, int(y0 / scale) // TILE_PX
    tx1, ty1 = int(x1 / scale) // TILE_PX, int(y1 / scale) // TILE_PX

    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    return {
        "zoom": zoom,
        "tx0": tx0, "ty0": ty0, "tx1": tx1, "ty1": ty1,
        "cols": cols, "rows": rows,
        "width": cols * TILE_PX,
        "height": rows * TILE_PX,
        "origin": (tx0 * TILE_PX, ty0 * TILE_PX),
        "tiles": [
            {"x": tx, "y": ty, "left": (tx - tx0) * TILE_PX, "top": (ty - ty0) * TILE_PX}
            for ty in range(ty0, ty1 + 1)
            for tx in range(tx0, tx1 + 1)
        ],
    }


def pin_position(coord: list, grid: dict, max_zoom: int) -> dict:
    """Where a continent coordinate lands inside the rendered tile grid.

    Returned as percentages so the page can scale the image responsively without
    the pins drifting off their landmarks.
    """
    scale = scale_for(grid["zoom"], max_zoom)
    ox, oy = grid["origin"]
    left = coord[0] / scale - ox
    top = coord[1] / scale - oy
    return {
        "left": left,
        "top": top,
        "left_pct": 100.0 * left / grid["width"],
        "top_pct": 100.0 * top / grid["height"],
    }


def tile_url(continent: int, floor: int, zoom: int, x: int, y: int) -> str:
    return TILE_URL.format(continent=continent, floor=floor, zoom=zoom, x=x, y=y)


def choose_zoom(continent_rect: list, max_zoom: int, budget: int = 24) -> int:
    """Largest zoom whose tile count stays within budget.

    Detail is worth paying for, but a map rendered at max zoom is 231 tiles — 231
    image requests for one page. This trades resolution for a sane request count.
    """
    best = 3
    for zoom in range(3, max_zoom + 1):
        grid = tile_grid(continent_rect, zoom, max_zoom)
        if grid["cols"] * grid["rows"] <= budget:
            best = zoom
        else:
            break
    return best
