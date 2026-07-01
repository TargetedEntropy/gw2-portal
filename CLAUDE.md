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

- `/v2/characters` — list characters; `/v2/characters/{name}` for inventory/equipment
- `/v2/account/bank` — bank contents (slot, item id, count, upgrades)
- `/v2/account/materials` — material storage
- `/v2/items?ids=...` — bulk item details (name, type, rarity, flags, vendor value)
- `/v2/commerce/prices?ids=...` — Trading Post buy/sell prices
- `/v2/account/wallet` — currency

All authenticated endpoints require `Authorization: Bearer {api_key}` header.

## Item Analysis Logic

Items are classified in this priority order:
1. **Keep** — ANY of: flags include `AccountBound`/`Unique`/`SoulboundOnAcquire`; rarity is Rare or above; OR the item appears in an account-unlocked recipe (check `/v2/recipes/search?output=<id>` then filter by account unlocks)
2. **Sell** — has a TP listing (`/v2/commerce/prices`), and `(sell_price * 0.85) - vendor_value > 100` copper (configurable threshold)
3. **Toss** (vendor) — everything else: low rarity with no meaningful TP price, or vendor price is the best exit

All four keep-logic signals are combined: account/soulbound flag, crafting material usage, TP price vs vendor threshold, and rarity cutoff (Rare+).

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
python app.py
# or with uvicorn if FastAPI:
uvicorn app:app --reload

# Run with a specific config
GW2_CONFIG=./config.toml python app.py
```

## File Layout (planned)

```
gw2-portal/
  app.py              # Flask/FastAPI app, routes
  config.toml         # API key, thresholds (gitignored)
  gw2/
    api.py            # GW2 API client (rate limiting, caching)
    items.py          # Item analysis / classification logic
    cache.py          # Simple file or memory cache for API responses
  templates/
    base.html         # The adapted EVE portal layout (3-col dark theme)
    inventory.html    # Character inventory + bank view
  static/
    style.css         # Overrides / GW2 color variables
```

## GW2 Color Palette (replacing EVE red/gold)

| Variable     | EVE value      | GW2 replacement     |
|-------------|---------------|---------------------|
| `--bg`       | `#12080a`     | `#0a0f14`           |
| `--red`      | `#c83c14`     | `#8b0000` (crimson) |
| `--gold`     | `#d4a52a`     | `#c8a84b` (Tyrian gold) |
| `--teal`     | `#20a090`     | `#1ea89a`           |
| `--ember`    | `#ff7a20`     | `#e06030`           |

Item rarity colors follow GW2 convention: Junk=grey, Basic=white, Fine=blue, Masterwork=green, Rare=yellow, Exotic=orange, Ascended=pink, Legendary=purple.
