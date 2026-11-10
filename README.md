# Team Store Pack List

Desktop app (Python/tkinter) for Rugby Imports team store orders. Pick the order export CSV and it:

1. Builds a packing-list PDF (`Team Store MM-DD-YYYY.pdf`) — one section per order that has embroidered (EMB) items, with a product photo, the team name, and a table of product + embroidery SKUs and quantities.
2. Copies the EMB totals to the clipboard as `MASTER_SKU,QTY,Each,COST,$0,0` lines, ready to paste.

## Shopify token

Images are looked up **read-only**:

1. **Shopify Admin GraphQL API** by exact SKU — needs an Admin API token (`shpat_...`) with `read_products`. The same token the Price Comparator uses works.
2. **Public website fallback** — if there's no token, the token is rejected, or a SKU isn't found, it searches rugbyimports.com instead (how the old 4.0 app worked).

The token is **not in the code**. On first run the app asks for it and saves it to `team_store_config.json` next to the script/.exe (git-ignored). You can also set the `SHOPIFY_TOKEN` environment variable. To change the token, edit or delete that file.

If the token stops working, the app still makes the PDF using the website, and the finish message says the token was rejected.

## Run

```
pip install -r requirements.txt
python team_store.py
```

## Build the .exe

```
pyinstaller --onefile --windowed --name Team_Store_App team_store.py
```

The .exe ends up in `dist/`. Put `team_store_config.json` next to it (or let it ask on first run).

## CSV format

Columns are read by position, so messy exports with extra columns still work:
`B` order number · `C` item SKU · `D` master SKU · `E` cost · `F` item name · `G` quantity (if `G` contains "OUTLINE", it's appended to the name and quantity is read from `H`). Header rows containing "ORDER" are skipped.

## Changes from 7.0

- Token moved out of the code into a config file / env var.
- API version updated (2024-04 was retired); SKU passed as a GraphQL variable instead of pasted into the query.
- Public-website fallback from 4.0 added back.
- Bad token detected once, instead of failing silently on every SKU.
- Leftover test code removed (it ran extra API calls after every PDF).
- Finish message lists any items with no image.
