# Raw data

This folder is **read-only**. Transformations write to `data/processed/` (Git-ignored) or directly to Azure.

| Path | Contents | Used by |
|---|---|---|
| `raw/products.jsonl` | Product catalogue: **18 products** (name, category, description, ingredients, price, rating, image) | Day 2: Cosmos DB ingestion + AI Search index |
| `raw/images/` | 18 product images (jpg/webp), 1:1 match with the catalogue's `image_path` | Day 2: Blob Storage upload |
| `raw/knowledge/about_us.txt` | Shop profile: history, delivery areas, opening hours, sustainability | Day 2: RAG knowledge base |
| `raw/knowledge/menu_items.txt` | Priced menu, **19 lines** (⚠️ see below) | Day 2: reference only, not the source of truth |
| `raw/sales/201904 sales reciepts.csv` | 49,894 transaction lines, April 2019, 3 outlets | Day 4: Apriori training |
| `raw/sales/product.csv` | Product reference table from the Kaggle dataset (88 rows) | Day 4: category join |
| `raw/sales/*.csv` | customer, staff, sales_outlet, Dates, generations, pastry inventory, sales targets | Day 4: exploration, unused in v1 |

**Sales dataset source**: [Kaggle: Coffee Shop Sample Data](https://www.kaggle.com/datasets/ylchang/coffee-shop-sample-data-1113). Kept in Git for v1 (2.9 MB); it becomes a **versioned Azure ML Data Asset** on day 4 (task 4.2).

## Resolved inconsistency: "Dark chocolate" (debt D11)

| Source | What it contains |
|---|---|
| `raw/knowledge/menu_items.txt` | **2 lines**: Drinking Chocolate $5.00 · Packaged Chocolate $3.00 |
| `raw/products.jsonl` | **1 record**: "Dark chocolate", category Drinking Chocolate, $5.00 |
| `legacy/recommendation_objects/popularity_recommendation.csv` | **2 rows**: Drinking (947 transactions) · Packaged (22) |

Consequences: a customer ordering "Dark chocolate" can be charged $5.00 or $3.00 depending on which source is consulted, and the recommender can suggest the Packaged variant, which has no catalogue record.

**Decided on 2026-09-28 (task 2.0): the packaged bar is out of scope.**

| Finding | Value |
|---|---|
| Packaged bar price, menu | 3.00 USD |
| Packaged bar price, Kaggle `product.csv` (id 19) | **6.40 USD**, so even the menu price is unreliable |
| Sales in April 2019 | drink (ids 58 and 59): 2894 units; packaged bar (id 19): **36 units, 1.2 %** |
| Catalogue record, description, image for the bar | none |

Consequences:
- The catalogue keeps its **18 products**. The drink gets the explicit identifier `dark-chocolate-drinking`.
- The recommender maps sales ids 58 and 59 to `dark-chocolate-drinking` and **drops id 19**. At 1.2 % of the volume it would never reach Apriori's 5 % minimum support anyway.
- `raw/knowledge/menu_items.txt` stays untouched (this folder is read only). It is no longer a source: the menu shown to customers is rendered from the catalogue (ADR-004), so the 3.00 USD line disappears on its own.
- Display names are never used as keys again, which also removes the legacy notebook bug that merged ids 19, 58 and 59 under one name.
