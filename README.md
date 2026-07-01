# GW2 Portal

A single-user Guild Wars 2 character portal. Connects to the GW2 API to show your character inventory, bank, material storage, and wallet — and tells you what to keep, sell on the Trading Post, or vendor.

## Features

- **Account dashboard** — wallet across all currencies, character list
- **Inventory analysis** — per-character bag contents with keep/sell/toss verdict for every item
- **Bank & materials** — full bank tab contents and material storage with the same analysis
- **Item tooltips** — hover any item for description, type, and rarity
- **Sell potential** — estimated gold after TP fees for everything recommended to sell

### Keep / Sell / Toss logic

| Verdict | Criteria |
|---------|----------|
| **Keep** | Account/soulbound flag · Rare+ rarity · Used in an unlocked recipe |
| **Sell** | TP sell price × 0.85 − vendor value > threshold (default 1s) |
| **Toss** | Everything else — vendor or salvage |

Thresholds are configurable in `config.toml`.

## Setup

1. **Get a GW2 API key** from [account.arena.net](https://account.arena.net/applications) with these permissions: `account`, `inventories`, `characters`, `bank`, `wallet`, `tradingpost`

2. **Copy the example config and add your key:**
   ```bash
   cp config.toml.example config.toml
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

## Config

```toml
[gw2]
api_key = "YOUR-API-KEY-HERE"

[analysis]
sell_threshold_copper = 100   # min profit over vendor to recommend selling (copper)
keep_rarity_min = 4           # 4=Rare, 5=Exotic, 6=Ascended, 7=Legendary
```

## Stack

- Python / Flask backend (API key stays server-side)
- Jinja2 templates — dark RPG-sheet layout adapted from [web-templates](https://github.com/TargetedEntropy/web-templates)
- File-based cache (`.cache/`) — items cached 1h, prices 2m, account data 1–5m
