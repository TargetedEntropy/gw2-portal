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
from gw2 import items as gw2_items
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
        "img-src 'self' https://render.guildwars2.com data:; "
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
    return render_template(
        "masteries.html",
        building=False,
        summary=summary,
        rows=shown[:400],
        shown_total=len(shown),
        remaining_total=len(remaining),
        completed=gw2_masteries.completed_count(catalogue, mine),
        catalogue_total=len(catalogue),
        unspent=sum(r["unspent"] for r in summary),
        region_filter=region_filter,
        tier_filter=tier_filter,
        tier_labels=gw2_masteries.TIER_LABELS,
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
