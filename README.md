# GW2 Portal

A single-user Guild Wars 2 character portal. Connects to the GW2 API to show your character inventory, bank, material storage, and wallet — and tells you what to keep, sell on the Trading Post, or vendor.

## Features

- **Account dashboard** — wallet across all currencies, character list
- **Inventory analysis** — per-character bag contents with a verdict for every item
- **Bank, materials & shared slots** — all account storage with the same analysis
- **Account-wide search** — find any item across every character, bank, and material storage at once, or list everything worth selling in one view
- **Item tooltips** — description, type, rarity, both TP prices, and the stack maths
- **Sell potential** — gold after TP fees for everything recommended to sell

### Verdicts

| Verdict | Criteria |
|---------|----------|
| **Keep** | Bound to your account or character · `Unique` · Rare+ rarity · an ingredient in a recipe you can craft |
| **Sell** | The whole stack nets more on the TP (after the 15% fees) than vendoring it, by more than the threshold |
| **Salvage** | No vendor value, but a salvage kit will take it |
| **Toss** | Vendor it, or destroy it if the item is flagged both `NoSell` and `NoSalvage` |
| **Unknown** | Item data could not be fetched — deliberately *not* given a verdict, because a failed lookup is not evidence an item is worthless |

Binding is read from the **slot**, not just the item's flags, so items that are bound
from having been equipped are never recommended for sale. Items flagged `NoSell` have
their `vendor_value` treated as zero — the API reports a price for them, but it is
informational and cannot actually be realised.

The crafting signal covers recipes unlocked from sheets **and** `AutoLearned` recipes.
The latter never appear in `/v2/account/recipes` (on this account: 0 of 2,096), so
leaving them out shrinks the signal from ~2,150 recipes to ~50.

Thresholds and the price basis are configurable in `config.toml`.

## Setup

1. **Get a GW2 API key** from [account.arena.net](https://account.arena.net/applications) with these permissions: `account`, `wallet`, `characters`, `inventories`, `unlocks`

   The app checks these at startup via `/v2/tokeninfo` and tells you exactly which one is missing rather than failing later inside a page.

2. **Copy the example config and add your key:**
   ```bash
   cp config.toml.example config.toml
   chmod 600 config.toml          # it holds a credential
   # edit config.toml and paste your API key
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run:**
   ```bash
   python3 app.py
   ```
   Then open [http://localhost:5000](http://localhost:5000)

   On first run a background job crawls all ~13,000 recipes to build the crafting
   index. It takes two to three minutes, runs once, and never blocks a page — until
   it finishes the pages say so and simply omit the crafting signal.

### Development

```bash
GW2_RELOAD=1 python3 app.py     # auto-reload on file changes
GW2_CONFIG=/path/config.toml python3 app.py
python3 -m pytest tests/ -q     # 83 tests, no network required
```

The interactive debugger is never enabled. It executes arbitrary Python from the
browser, and CVE-2024-34069 means binding to loopback is not a boundary — a site you
visit in another tab can reach it. `GW2_RELOAD` gives auto-reload without it.

## Config

```toml
[gw2]
api_key = "YOUR-API-KEY-HERE"

[analysis]
sell_threshold_copper = 100   # min profit over vendor, judged on the WHOLE STACK
keep_rarity_min = 4           # 4=Rare, 5=Exotic, 6=Ascended, 7=Legendary
price_basis = "sell"          # "sell" = list and wait; "buy" = the instant-sell bid

[server]
host = "127.0.0.1"
port = 5000
allowed_hosts = ["127.0.0.1:5000", "localhost:5000"]
api_rate_per_sec = 4.0        # stays under the GW2 API's 5/sec, 300-burst limit
api_burst = 100
```

## Stack

- Python / Flask backend (API key stays server-side)
- Jinja2 templates — dark RPG-sheet layout adapted from [web-templates](https://github.com/TargetedEntropy/web-templates)
- File-based cache (`.cache/`, mode 0700) — items 24h, prices 2m, account data 1–5m,
  recipe index 7d. Writes are atomic; a corrupt entry degrades to a cache miss and
  self-heals rather than failing the request. Stale entries are served if the API is
  down, in preference to an error page.
- All upstream calls pass through a shared token bucket and retry with backoff. The
  GW2 rate limit is per-IP, so the budget is shared with anything else on the host.
- API schema version is pinned (`gw2/api.py: SCHEMA`). Unpinned requests get the
  *oldest* schema, which can shift behaviour without a code change.

> **Note:** `.cache/` contains your bank, wallet, and inventory contents in plaintext.
> It is gitignored — don't share it.
