# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A single-user Guild Wars 2 character portal that connects to the GW2 REST API to display character inventory, bank contents, and provide item analysis (keep/sell/toss recommendations).

The visual layout is adapted from the EVE character portal v5 template: a 3-column dark RPG sheet design with a character header, left attributes panel, center main content panel, and right sidebar.

## Architecture

- **Backend**: Python Flask — proxies GW2 API calls, serves Jinja2 templates, holds the API key server-side
- **Frontend**: Jinja2 templates adapting the EVE portal v5 HTML/CSS 3-column layout to GW2 aesthetics
- **Config**: `config.toml` at the project root for the GW2 API key and any thresholds
- **GW2 API base URL**: `https://api.guildwars2.com/v2/`

## Scope (all active at launch)

- Character inventory + equipment (per-character bags and gear)
- Account bank (all bank tab slots)
- Material storage (crafting materials tab)
- Wallet + currencies (gold, Karma, Gems, WvW Badges, etc.)

## Key GW2 API Endpoints

- `/v2/tokeninfo` — key permissions; checked at startup
- `/v2/characters` — list characters; `/v2/characters/{name}` for inventory/equipment
- `/v2/account/bank` — bank contents (slot, item id, count, binding, upgrades)
- `/v2/account/materials` — material storage
- `/v2/account/inventory` — shared inventory slots
- `/v2/items?ids=...` — bulk item details (name, type, rarity, flags, vendor value)
- `/v2/commerce/prices?ids=...` — Trading Post buy/sell prices
- `/v2/account/wallet` — currency; `/v2/currencies` and `/v2/worlds` to resolve ids
- `/v2/recipes` — full recipe crawl for the ingredient index

All authenticated endpoints require an `Authorization: Bearer {api_key}` header.

### API constraints that shape the client

- **Rate limit: 300-request burst refilling at 5/second, enforced per IP, not per key.** Shared with anything else on the host. Everything goes through the token bucket in `gw2/api.py: _Bucket`; nothing should call `requests.get` directly.
- **Bulk `?ids=` is capped at 200** across all endpoints.
- **Omitting `v=` serves the *oldest* schema.** It is pinned in `gw2/api.py: SCHEMA`. Moving it forward past `2019-12-19` changes `/v2/characters` `equipment` to include legendary-armory and inactive-tab entries — `app.py: _equipped()` filters on `location`, and that filter is what stops them being listed as worn gear.
- **No `Retry-After` header is sent on 429**, and there are no `ETag`/`Cache-Control` headers. Backoff is exponential and caching is entirely client-side TTLs.
- **A 404 from `/v2/commerce/prices` means "none of these ids are tradable"** — a real answer, not an error. It is cached as such.
- Missing scope returns **403**; bad key returns **401**.

## Item Analysis Logic

Implemented in `gw2/items.py: classify()`. Five verdicts, evaluated in this order:

1. **Keep** — ANY of:
   - the **slot** has a `binding` (`Account` or `Character`). Binding lives on the slot, not the item — something can be bound from having been equipped while its own flags are clean.
   - item flags include `AccountBound`, `AccountBindOnUse`, `SoulbindOnAcquire`, or `SoulBindOnUse`.
   - flags include `Unique`.
   - rarity is Rare or above (`keep_rarity_min`).
   - the item is an **ingredient** in a recipe the account can craft.
2. **Sell** — has a TP price, and the **whole stack** nets more than `sell_threshold_copper` over vendoring it, after the 15% fees.
3. **Salvage** — no vendor value, but not flagged `NoSalvage`.
4. **Toss** — vendor it; or destroy it if flagged both `NoSell` and `NoSalvage`.
5. **Unknown** — item details could not be fetched. Deliberately not given a verdict.

### Things that are easy to get wrong here

- **Flag casing is inconsistent in the API itself**: `SoulbindOnAcquire` (lowercase `b`) but `SoulBindOnUse` (uppercase `B`). Flags are compared lowercased in `BOUND_FLAGS` so this can't bite again. `SoulboundOnAcquire` and `SoulboundOnUse` are **not** real API values.
- **Recipe direction is ingredient-first**: keep what you need *in order to* craft, not what you *could* craft. Use `/v2/recipes/search?input=<id>`, or the `ingredients` index in `gw2/api.py: build_recipe_index()`. The `outputs` index exists too but is not what drives the keep signal.
- **`/v2/account/recipes` excludes `AutoLearned` recipes.** Verified live: it returned 52 ids, containing 0 of the 2,096 auto-learned recipes. Always union the two — `api.craftable_recipe_ids()` does this.
- **`vendor_value` is non-zero on `NoSell` items** but cannot be realised. `items.vendor_value()` zeroes it; never read the raw field.
- **A missing item payload must never produce a verdict.** An empty dict falls through every branch and lands on "toss", so one API hiccup advises vendoring valuable items. `known=False` short-circuits to `unknown`.
- **Thresholds apply to the stack**, not the unit — 250 materials at 40c each is a gold.

## Development Commands

```bash
pip install -r requirements.txt

python3 app.py                              # run
GW2_RELOAD=1 python3 app.py                 # auto-reload, no debugger
GW2_CONFIG=/path/to/config.toml python3 app.py
python3 -m pytest tests/ -q                 # 83 tests, fully offline
```

**Never set `debug=True`.** The Werkzeug debugger runs arbitrary Python from the
browser and CVE-2024-34069 defeats the loopback bind. `GW2_RELOAD` covers the
legitimate use. `tests/test_app.py` asserts the flag never reappears.

## File Layout

```
gw2-portal/
  app.py              # Flask app, routes, error handlers, scheduler, preflight
  config.toml         # API key, thresholds, server settings (gitignored, mode 600)
  fixes.md            # Audit findings this codebase was remediated against
  gw2/
    api.py            # GW2 client: rate limiting, retries, schema pin, recipe index
    items.py          # Item analysis / classification logic (pure, no I/O)
    cache.py          # Atomic file cache with TTLs, pruning, corruption recovery
  templates/
    base.html         # 3-col dark theme + all CSS (inlined; no static/ dir yet)
    _items.html       # Shared item-row / verdict-section macros
    index.html        # Account dashboard
    inventory.html    # Per-character inventory
    bank.html         # Bank, materials, shared slots
    search.html       # Account-wide item search
    error.html
  tests/              # test_items, test_cache, test_api, test_app
```

Note `static/style.css` does not exist — the CSS lives in `base.html`. Extracting it
is the prerequisite for dropping `style-src 'unsafe-inline'` from the CSP.

## Invariants worth preserving

- `gw2/items.py` does no I/O beyond one import of `api` inside `enrich_slots`. Keep `classify()` pure so it stays trivially testable.
- Every upstream call goes through `api._get`/`_request` for rate limiting, retries, schema pinning, and the stale-on-error fallback.
- `RARITY_COLORS.get(...)` in `items.py` is a **security control**, not a convenience: its output is interpolated into a `style=` attribute, and Jinja escapes quotes but not `;` or `:`. Never replace the dict lookup with a raw API value.
- Anything expensive (the ~66-request recipe crawl) belongs in the scheduler, never in a request handler.
- Dict keys rendered in Jinja must not shadow dict methods — a key named `items` resolves to `dict.items` in `{{ s.items }}`.

## GW2 Color Palette (replacing EVE red/gold)

| Variable     | EVE value      | GW2 replacement     |
|-------------|---------------|---------------------|
| `--bg`       | `#12080a`     | `#0a0f14`           |
| `--red`      | `#c83c14`     | `#8b0000` (crimson) |
| `--gold`     | `#d4a52a`     | `#c8a84b` (Tyrian gold) |
| `--teal`     | `#20a090`     | `#1ea89a`           |
| `--ember`    | `#ff7a20`     | `#e06030`           |

Item rarity colors follow GW2 convention: Junk=grey, Basic=white, Fine=blue, Masterwork=green, Rare=yellow, Exotic=orange, Ascended=pink, Legendary=purple.
