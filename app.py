import sys
import tomllib
from flask import Flask, render_template, abort
from gw2 import api, items as gw2_items

app = Flask(__name__)

# Load config
try:
    with open("config.toml", "rb") as f:
        config = tomllib.load(f)
except FileNotFoundError:
    print("ERROR: config.toml not found. Copy config.toml.example and add your API key.", file=sys.stderr)
    sys.exit(1)

api.init(config["gw2"]["api_key"])
SELL_THRESHOLD = config.get("analysis", {}).get("sell_threshold_copper", 100)
KEEP_RARITY_MIN = config.get("analysis", {}).get("keep_rarity_min", 4)


@app.route("/")
def index():
    try:
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
        char_names = api.characters()
    except Exception as e:
        return render_template("error.html", error=str(e)), 500

    return render_template(
        "index.html",
        account=account,
        wallet=wallet,
        char_names=char_names,
    )


@app.route("/inventory/<char_name>")
def inventory(char_name):
    try:
        char = api.character(char_name)
    except Exception as e:
        abort(404, description=str(e))

    all_slots = []
    for bag in (char.get("bags") or []):
        if bag:
            all_slots.extend(bag.get("inventory") or [])

    enriched = gw2_items.enrich_slots(all_slots, SELL_THRESHOLD, KEEP_RARITY_MIN)
    grouped = _group_by_verdict(enriched)

    equipment = char.get("equipment") or []
    equip_ids = [e["id"] for e in equipment if "id" in e]
    equip_details = api.items_bulk(equip_ids) if equip_ids else {}

    return render_template(
        "inventory.html",
        char=char,
        char_name=char_name,
        grouped=grouped,
        all_items=enriched,
        equipment=equipment,
        equip_details=equip_details,
        rarity_colors=gw2_items.RARITY_COLORS,
    )


@app.route("/bank")
def bank():
    try:
        slots = api.bank()
        mats = api.materials()
    except Exception as e:
        return render_template("error.html", error=str(e)), 500

    enriched_bank = gw2_items.enrich_slots([s for s in slots if s], SELL_THRESHOLD, KEEP_RARITY_MIN)
    enriched_mats = gw2_items.enrich_slots([{"id": m["id"], "count": m["count"]} for m in mats if m["count"] > 0], SELL_THRESHOLD, KEEP_RARITY_MIN)

    return render_template(
        "bank.html",
        bank_grouped=_group_by_verdict(enriched_bank),
        bank_all=enriched_bank,
        mats_grouped=_group_by_verdict(enriched_mats),
        mats_all=enriched_mats,
    )


def _group_by_verdict(enriched: list) -> dict:
    groups = {"keep": [], "sell": [], "toss": []}
    for item in enriched:
        groups[item["verdict"]].append(item)
    for k in groups:
        groups[k].sort(key=lambda x: x.get("sell_price", 0), reverse=True)
    return groups


def _fmt_currency(cid: int, value: int) -> str:
    if cid == 1:  # Gold
        return gw2_items.copper_to_gold(value)
    return str(value)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
