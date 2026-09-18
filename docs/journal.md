# Work journal

One entry per day: what got done, what is blocked, what was learned, and the cost incurred.

---

## Day 0 — 2026-09-18 — Preparation

**Done**
- Created the `Multi_Agents-Recommendation-system-azure` repository (public) and its folder structure
- Selective copy from the prototype: data (catalogue, 18 images, knowledge base, 49,894 transaction lines), legacy code kept for reference, project documents
- Hardened `.gitignore` before running `git init` (650 MB `venv`, `.env` files, `.azure/`, `.DS_Store`)
- Local toolchain: Python 3.11 venv (`.venv`, managed with `uv`), `Makefile` switched to `uv` and to `.venv/bin` tools; `azd`, `gh`, `gitleaks` installed; Azure CLI 2.90.0 installed with `uv tool`
- Azure: subscription selected (pay-as-you-go, spending limit off), the 13 resource providers the project needs are registered, $30 monthly budget with email alerts (actual 50/80/100%, forecast 100%)
- **Model decision (ADR-007)**: `gpt-5.6-luna` as target, `gpt-5.4-mini` as interim, Data Zone Standard in Sweden Central; quota request filed for `gpt-5.6-luna`
- `docs/commands.txt` cheat sheet (uv, make, az, azd)

**Learned / discovered**
- The catalogue holds **18 products, not 19**: the menu advertises two "Dark chocolate" entries (Drinking $5.00 / Packaged $3.00) but `products.jsonl` only has one. The popularity table has two. → debt **D11 reclassified as critical** (it breaks OM4, zero billing errors); to be settled on day 2, task 2.0.
- The original vector index holds **20 documents** (18 product records + about page + menu), not 21.
- The original folder was never a Git repository, so no key ever leaked and there is no history to rewrite.
- In the Kaggle sales data, the name "Dark chocolate" covers product_id 19 (Packaged Chocolate) **and** 58/59 (Drinking Chocolate, once the ` Rg`/` Lg` suffixes are stripped). The legacy notebook pivots baskets by product *name*, so the legacy Apriori rules mix both products → D11 also corrupts the recommender baseline. Fix on day 4: key on `product_id`.
- Homebrew on this Mac is the Intel (x86_64, Rosetta) install under `/usr/local`: no bottles for macOS 26 → it compiles everything from source (LLVM, hours). Azure CLI installed with `uv tool` instead.
- `uv tool install azure-cli` silently resolved **2.0.67 (2019)**: recent `azure-cli` pins pre-release SDKs (e.g. `azure-batch>=15.0.0b1`), which uv refuses by default. Fix: `--prerelease allow`. The shipped `az` script also calls whatever `python` is on PATH (Anaconda) → replaced by a wrapper pointing at the tool's own interpreter. Lesson: always check the version actually installed.
- `gpt-4.1-mini` (the model in the v1.1 spec) is **Legacy** (retires 2027-04-14) with batch-only quota → risk R1 materialised on day 0. `gpt-5.6-luna` is available in every EU region checked, but quota is 0 everywhere until requested.
- GPT-5 models are reasoning models: `temperature`/`top_p` expected unsupported → to verify with a live call on day 1.

**Blocked / to do**
- [x] Check Azure OpenAI model availability in the target region (critical path: quota requests take 24-48h)
- [x] Install the `gh` CLI and `gitleaks`
- [ ] Wait for the `gpt-5.6-luna` quota decision (filed 2026-09-18)
- [ ] Note the `gpt-5.6-luna` price per million tokens (input/output) from the model card
- [ ] Enable GitHub Secret Scanning + Push Protection
- [ ] First commit and push

**Cost today**: €0
