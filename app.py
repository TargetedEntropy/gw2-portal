import logging
import os
import sys
import threading
import time

from flask import Flask, abort, render_template, request, url_for

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from gw2 import api, cache
from gw2 import equipment as gw2_equipment
from gw2 import items as gw2_items
from gw2 import mapdata as gw2_mapdata
from gw2 import masteries as gw2_masteries

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("gw2portal")

CONFIG_PATH = os.environ.get(
    "GW2_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
)


def load_config(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        sys.exit(
            f"ERROR: {path} not found. Copy config.toml.example to config.toml and add your API key."
        )
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: {path} is not valid TOML: {e}")

    try:
        key = cfg["gw2"]["api_key"]
    except KeyError:
        sys.exit(f"ERROR: {path} is missing [gw2] api_key.")
    if not key or key == "YOUR-API-KEY-HERE":
        sys.exit(f"ERROR: {path} still contains the placeholder API key.")

    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        log.warning("%s is mode %o and holds your API key — tightening to 600", path, mode)
        try:
            os.chmod(path, 0o600)
        except OSError:
            log.warning("could not chmod %s; run: chmod 600 %s", path, path)
    return cfg


config = load_config(CONFIG_PATH)

_analysis = config.get("analysis", {})
SELL_THRESHOLD = _analysis.get("sell_threshold_copper", 100)
KEEP_RARITY_MIN = _analysis.get("keep_rarity_min", 4)
PRICE_BASIS = _analysis.get("price_basis", "sell")

_server = config.get("server", {})
HOST = _server.get("host", "127.0.0.1")
PORT = int(os.environ.get("GW2_PORT", _server.get("port", 5000)))
ALLOWED_HOSTS = set(
    _server.get("allowed_hosts", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
)

api.init(
    config["gw2"]["api_key"],
    rate_per_sec=_server.get("api_rate_per_sec", 4.0),
    burst=_server.get("api_burst", 100),
)

app = Flask(__name__)
app.jinja_env.filters["gold"] = gw2_items.copper_to_gold


# --- Request/response guards -------------------------------------------------


@app.before_request
def _guard_host():
    # Blocks DNS rebinding: without this, a page on an attacker's domain that
    # re-resolves to 127.0.0.1 can read this portal's responses same-origin.
    if request.host not in ALLOWED_HOSTS:
        abort(400, description="Invalid Host header")


@app.after_request
def _security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        # tiles.guildwars2.com serves the official map tiles for the mastery map.
        "img-src 'self' https://render.guildwars2.com https://tiles.guildwars2.com data:; "
        "style-src 'unsafe-inline'; "
        "form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.errorhandler(api.GW2AuthError)
def _err_auth(e):
    return render_template("error.html", error="API key rejected — check config.toml"), 502


@app.errorhandler(api.GW2ScopeError)
def _err_scope(e):
    return render_template("error.html", error=str(e)), 502


@app.errorhandler(api.GW2RateLimited)
def _err_rate(e):
    return (
        render_template("error.html", error="Rate limited by the GW2 API — try again shortly"),
        503,
    )


@app.errorhandler(api.GW2NotFound)
def _err_notfound(e):
    return render_template("error.html", error="Not found in the GW2 API."), 404


@app.errorhandler(Exception)
def _err_catchall(e):
    # Never let a traceback reach the response body: with the Werkzeug debugger
    # enabled that page is an RCE surface, and the exception text leaks internals.
    code = getattr(e, "code", 500)
    if code != 500:
        return render_template("error.html", error=getattr(e, "description", str(code))), code
    log.exception("unhandled error serving %s", request.path)
    return render_template("error.html", error="Something went wrong. See the server log."), 500


# --- Views -------------------------------------------------------------------


@app.route("/")
def index():
    account = api.account()
    wallet_raw = api.wallet()
    currency_defs = api.currencies()
    wallet = [
        {
            "name": currency_defs.get(e["id"], {}).get("name", f"Currency {e['id']}"),
            "icon": currency_defs.get(e["id"], {}).get("icon", ""),
            "value": e["value"],
            "value_fmt": _fmt_currency(e["id"], e["value"]),
        }
        for e in wallet_raw
        if e["value"] > 0
    ]
    return render_template(
        "index.html",
        account=account,
        world=api.world_name(account.get("world")),
        wallet=wallet,
        char_names=api.characters(),
        index_ready=api.cached_recipe_index() is not None,
    )


@app.route("/inventory/<char_name>")
def inventory(char_name):
    if char_name not in api.characters():
        abort(404, description="No such character on this account.")

    char = api.character(char_name)

    all_slots = []
    for bag in char.get("bags") or []:
        if bag:
            all_slots.extend(bag.get("inventory") or [])

    enriched = gw2_items.enrich_slots(
        all_slots, SELL_THRESHOLD, KEEP_RARITY_MIN, char_name, PRICE_BASIS
    )
    items = enriched["items"]

    equipment = _equipped(char)
    equip_ids = [e["id"] for e in equipment if "id" in e]
    equip_details, _ = api.items_bulk(equip_ids) if equip_ids else ({}, set())

    return render_template(
        "inventory.html",
        char=char,
        char_name=char_name,
        grouped=gw2_items.group_by_verdict(items),
        all_items=items,
        totals=gw2_items.totals(items),
        type_counts=gw2_items.type_counts(items),
        equipment=equipment,
        equip_details=equip_details,
        rarity_colors=gw2_items.RARITY_COLORS,
        failed=enriched["failed"],
        recipe_signal=enriched["recipe_signal"],
        price_basis=PRICE_BASIS,
    )


@app.route("/bank")
def bank():
    bank_slots = api.bank()
    mats = api.materials()
    try:
        shared = api.shared_inventory()
    except api.GW2Error:
        shared = []

    sections = []
    failed = 0
    signal_ok = True
    for label, slots in (
        ("Bank", bank_slots),
        ("Material Storage", [m for m in mats if m and m.get("count", 0) > 0]),
        ("Shared Slots", shared),
    ):
        e = gw2_items.enrich_slots(slots, SELL_THRESHOLD, KEEP_RARITY_MIN, label, PRICE_BASIS)
        failed += e["failed"]
        signal_ok = signal_ok and e["recipe_signal"]
        sections.append(
            {
                "label": label,
                # Not "items": in a Jinja attribute lookup `s.items` resolves to
                # dict.items, the bound method, not this key.
                "entries": e["items"],
                "grouped": gw2_items.group_by_verdict(e["items"]),
                "totals": gw2_items.totals(e["items"]),
            }
        )

    overall = {
        "items": sum(len(s["entries"]) for s in sections),
        "keep": sum(len(s["grouped"]["keep"]) for s in sections),
        "sell": sum(len(s["grouped"]["sell"]) for s in sections),
        "toss": sum(
            len(s["grouped"]["toss"]) + len(s["grouped"]["salvage"]) for s in sections
        ),
        "sell_net": sum(s["totals"]["sell_net"] for s in sections),
    }

    return render_template(
        "bank.html",
        sections=sections,
        overall=overall,
        failed=failed,
        recipe_signal=signal_ok,
        price_basis=PRICE_BASIS,
    )


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    verdict_filter = request.args.get("verdict") or ""

    everything = _account_wide_items()
    results = everything
    if q:
        needle = q.lower()
        results = [i for i in results if needle in i["name"].lower()]
    if verdict_filter in gw2_items.VERDICTS:
        results = [i for i in results if i["verdict"] == verdict_filter]

    rows = gw2_items.aggregate_by_item(results)

    return render_template(
        "search.html",
        q=q,
        verdict_filter=verdict_filter,
        rows=rows,
        grand_total=sum(r["total_value"] for r in rows),
        account_totals=gw2_items.totals(everything),
        verdicts=gw2_items.VERDICTS,
    )


@app.route("/equipment")
def equipment_picker():
    """Character chooser for the account-wide equipment comparison tool."""
    characters = []
    for name in api.characters():
        char = _safe(lambda n=name: api.character(n), default={})
        characters.append(
            {
                "name": name,
                "profession": char.get("profession", "Unknown profession"),
                "race": char.get("race", ""),
                "level": char.get("level", "?"),
            }
        )
    return render_template("equipment_picker.html", characters=characters)


@app.route("/equipment/<char_name>")
def equipment(char_name):
    """Compare account-held equipment with what a character currently wears."""
    names = api.characters()
    if char_name not in names:
        abort(404, description="No such character on this account.")

    char = api.character(char_name)
    equipped = _equipped(char)
    holdings = []
    source_errors = []

    def add_source(label, slots):
        for slot in slots or []:
            if slot and slot.get("id"):
                holdings.append(
                    {
                        "slot": slot,
                        "location": label,
                        "target_character": char_name,
                    }
                )

    for label, fetch in (
        ("Bank", api.bank),
        ("Shared inventory", api.shared_inventory),
        ("Legendary Armory", api.legendary_armory),
    ):
        try:
            add_source(label, fetch())
        except api.GW2Error as e:
            log.warning("equipment tool skipped %s: %s", label, e)
            source_errors.append(label)

    for name in names:
        other = char if name == char_name else None
        try:
            other = other or api.character(name)
            slots = []
            for bag in other.get("bags") or []:
                if bag:
                    slots.extend(bag.get("inventory") or [])
            add_source(f"{name}'s bags", slots)
            if name != char_name:
                add_source(f"{name}'s equipped gear", _equipped(other))
        except api.GW2Error as e:
            log.warning("equipment tool skipped %s's bags: %s", name, e)
            source_errors.append(f"{name}'s bags")

    all_instances = equipped + [h["slot"] for h in holdings]
    item_ids = [s["id"] for s in all_instances if s and s.get("id")]
    details, failed_items = api.items_bulk(item_ids)

    wanted_stats = gw2_equipment.stat_ids(all_instances, details)
    stat_defs, failed_stats = (
        api.itemstats_bulk(sorted(wanted_stats)) if wanted_stats else ({}, set())
    )
    try:
        profession_defs = api.professions()
    except api.GW2Error as e:
        log.warning("equipment tool could not load profession definitions: %s", e)
        profession_defs = {}
        source_errors.append("Profession definitions")

    report = gw2_equipment.analyze(
        char, equipped, holdings, details, stat_defs, profession_defs
    )
    # Missing item definitions were already counted while the report walked each
    # physical holding. Missing stat definitions are separate unknowns.
    report["unknown_count"] += len(failed_stats)
    return render_template(
        "equipment.html",
        char=char,
        char_name=char_name,
        report=report,
        source_errors=source_errors,
        rarity_colors=gw2_items.RARITY_COLORS,
    )


@app.route("/masteries")
def masteries():
    catalogue = api.cached_mastery_achievements()
    if catalogue is None:
        return render_template("masteries.html", building=True), 200

    points = api.account_mastery_points()
    mine = api.account_achievements()
    owned = gw2_masteries.owned_region_codes(points)

    remaining = gw2_masteries.remaining_points(catalogue, mine, owned)

    region_filter = request.args.get("region") or ""
    tier_filter = request.args.get("tier") or ""
    shown = remaining
    if region_filter:
        shown = [r for r in shown if r["region"] == region_filter]
    if tier_filter.isdigit():
        shown = [r for r in shown if r["tier"] == int(tier_filter)]

    summary = gw2_masteries.region_summary(points, remaining)
    # Insights get a per-map route view; everything else stays a ranked list.
    routes = gw2_masteries.routes_by_map(shown) if tier_filter in ("", "0") else []

    # Attach a map id where we have coordinates, so the card can link to the map.
    map_index = api.cached_map_index()
    if map_index:
        by_name = {m["name"]: mid for mid, m in map_index["maps"].items()}
        for trip in routes:
            trip["map_id"] = by_name.get(trip["map"])
    return render_template(
        "masteries.html",
        building=False,
        summary=summary,
        routes=routes,
        rows=shown[:400],
        shown_total=len(shown),
        remaining_total=len(remaining),
        completed=gw2_masteries.completed_count(catalogue, mine),
        catalogue_total=len(catalogue),
        unspent=sum(r["unspent"] for r in summary),
        region_filter=region_filter,
        tier_filter=tier_filter,
        tier_labels=gw2_masteries.TIER_LABELS,
        region_names=gw2_masteries.REGION_NAMES,
    )


@app.route("/masteries/map/<int:map_id>")
def mastery_map(map_id):
    index = api.cached_map_index()
    if index is None:
        abort(404, description="Map coordinates are still being indexed.")
    entry = index["maps"].get(map_id)
    if entry is None:
        abort(404, description="No mastery points recorded for that map.")

    max_zoom = entry.get("max_zoom") or api.continent_meta(entry["continent_id"])["max_zoom"]
    # Verified at index time, so this renders rather than 404ing. None means the
    # tile server has no imagery for this zone at any zoom.
    zoom = entry.get("tile_zoom")
    grid = gw2_mapdata.tile_grid(
        entry["continent_rect"], zoom or gw2_mapdata.choose_zoom(entry["continent_rect"], max_zoom), max_zoom
    )

    tiles = (
        [
            {
                **tile,
                "url": gw2_mapdata.tile_url(
                    entry["continent_id"], entry["tile_floor"], zoom, tile["x"], tile["y"]
                ),
            }
            for tile in grid["tiles"]
        ]
        if zoom
        else []
    )

    # `unlocked` is per mastery point, so collected state is exact here rather than
    # inferred from achievement completion.
    unlocked = set(api.account_mastery_points().get("unlocked", []))
    catalogue = api.cached_mastery_achievements() or []
    names = {
        a["point_id"]: a for a in catalogue if a.get("point_id") is not None
    }

    pins = []
    for point in entry["points"]:
        achievement = names.get(point["id"])
        pins.append(
            {
                **gw2_mapdata.pin_position(point["coord"], grid, max_zoom),
                "id": point["id"],
                "collected": point["id"] in unlocked,
                "name": (achievement or {}).get("name", f"Mastery Point {point['id']}"),
                "requirement": (achievement or {}).get("requirement", ""),
                "wiki_url": gw2_masteries.wiki_url((achievement or {}).get("name", "")),
            }
        )
    pins.sort(key=lambda p: (p["collected"], p["name"]))

    return render_template(
        "mastery_map.html",
        entry=entry,
        grid=grid,
        tiles=tiles,
        pins=pins,
        zoom=zoom,
        collected=sum(1 for p in pins if p["collected"]),
    )


def _account_wide_items() -> list:
    """Every item the account holds, from every location, enriched once."""
    out = []
    for label, slots in (
        ("Bank", _safe(api.bank)),
        ("Material Storage", [m for m in _safe(api.materials) if m and m.get("count", 0) > 0]),
        ("Shared Slots", _safe(api.shared_inventory)),
    ):
        out += gw2_items.enrich_slots(
            slots, SELL_THRESHOLD, KEEP_RARITY_MIN, label, PRICE_BASIS
        )["items"]

    for name in _safe(api.characters):
        char = _safe(lambda: api.character(name), default={})
        slots = []
        for bag in char.get("bags") or []:
            if bag:
                slots.extend(bag.get("inventory") or [])
        out += gw2_items.enrich_slots(
            slots, SELL_THRESHOLD, KEEP_RARITY_MIN, name, PRICE_BASIS
        )["items"]
    return out


def _safe(fn, default=None):
    try:
        return fn() or (default if default is not None else [])
    except Exception as e:
        log.warning("skipping unavailable source: %s", e)
        return default if default is not None else []


def _equipped(char: dict) -> list:
    """Equipment actually worn.

    On the pinned schema `equipment` is a flat list. If SCHEMA is moved to
    2019-12-19 or later this filter is what stops the legendary armory and every
    inactive build tab from being listed as worn gear.
    """
    eq = char.get("equipment") or []
    if any("location" in e for e in eq):
        return [e for e in eq if e.get("location") in ("Equipped", "EquippedFromLegendaryArmory")]
    return eq


def _fmt_currency(cid: int, value: int) -> str:
    if cid == 1:  # Coin
        return gw2_items.copper_to_gold(value)
    return f"{value:,}"


# --- Background jobs ---------------------------------------------------------


def _insight_map_names() -> set[str]:
    catalogue = api.cached_mastery_achievements() or []
    names = {
        gw2_masteries.insight_map(a)
        for a in catalogue
        if gw2_masteries.is_insight(a)
    }
    return {n for n in names if n}


def _scheduler():
    """Keeps expensive work off the request path.

    The recipe crawl is ~66 upstream calls; running it inside a page render blocked
    the user for the whole crawl and let concurrent tabs each start their own.
    """
    for label, ready, build in (
        ("recipe", api.cached_recipe_index, api.build_recipe_index),
        ("mastery", api.cached_mastery_achievements, api.build_mastery_achievements),
    ):
        try:
            if ready() is None:
                log.info("no %s index cached — building in background", label)
                build()
        except Exception:
            log.exception("initial %s index build failed", label)

    # Depends on the mastery index: the maps to scan come from insight requirements.
    try:
        if api.cached_map_index() is None:
            log.info("no map index cached — building in background")
            api.build_map_index(_insight_map_names())
    except Exception:
        log.exception("initial map index build failed")

    last_index = time.time()
    while True:
        time.sleep(3600)
        try:
            cache.prune()
        except Exception:
            log.exception("cache prune failed")
        if time.time() - last_index > 86400:
            # Both only change on game patches.
            for label, build in (
                ("recipe", api.build_recipe_index),
                ("mastery", api.build_mastery_achievements),
            ):
                try:
                    build(force=True)
                except Exception:
                    log.exception("scheduled %s index rebuild failed", label)
            try:
                api.build_map_index(_insight_map_names(), force=True)
            except Exception:
                log.exception("scheduled map index rebuild failed")
            last_index = time.time()


def start_background_jobs(reloader: bool = False):
    # With the reloader active the script runs in both a parent and a child
    # process; only the child should own the jobs.
    if reloader and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    threading.Thread(target=_scheduler, name="gw2-scheduler", daemon=True).start()


def preflight():
    """Fail loudly on a misconfigured key rather than deep inside a page render."""
    required = {"account", "wallet", "characters", "inventories", "unlocks"}
    try:
        info = api.tokeninfo()
    except api.GW2AuthError:
        sys.exit("ERROR: the GW2 API key in config.toml was rejected.")
    except Exception as e:
        log.warning("could not verify API key permissions: %s", e)
        return
    missing = required - set(info.get("permissions", []))
    if missing:
        sys.exit(
            f"ERROR: API key '{info.get('name', '?')}' is missing permissions: "
            f"{', '.join(sorted(missing))}"
        )
    log.info("API key '%s' verified with all required permissions", info.get("name", "?"))


if __name__ == "__main__":
    reloader = bool(os.environ.get("GW2_RELOAD"))

    preflight()
    start_background_jobs(reloader)
    app.run(
        host=HOST,
        port=PORT,
        # Never enabled. The Werkzeug debugger executes arbitrary Python from the
        # browser, and CVE-2024-34069 means a loopback bind is not a boundary: a
        # site the user visits can drive it. GW2_RELOAD gives auto-reload without
        # exposing the console.
        debug=False,
        use_reloader=reloader,
    )
