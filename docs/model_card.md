# Model card: coffee-reco-apriori

Association rules that suggest what to add to a coffee shop basket, served by the
Recommendation agent and by the upsell after the first ordered items.

|                   |                                                                                                |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| **Registered as** | `coffee-reco-apriori` in the Azure ML registry (`mlw-coffeeai-dev-frc`), MLflow pyfunc         |
| **Served from**   | `model-artefacts/coffee-reco-apriori/current/recommendations.json`, version shown on `/health` |
| **Pipeline**      | `prep_data` -> `evaluate` -> `train_apriori` -> `register_model` (`src/ml/components/`)        |
| **Data**          | Azure ML data asset `coffee-sales:1`                                                           |
| **Owner**         | Mamadou Kane                                                                                   |
| **Date**          | 2026-10-01                                                                                     |

## Intended use

- **In scope**: suggesting complementary items for a basket ("what goes well with a latte?"), and the one-time upsell after the first items are ordered. Three to five suggestions, every one of them a product of the live catalogue (US3).
- **Out of scope**: personalisation (no customer history is used), prices or promotions, stock availability, any decision with a health impact. Allergens are never inferred from this model: the Details agent answers allergen questions from the catalogue.

## Data

Kaggle "Coffee Shop Sample Data" (`ylchang/coffee-shop-sample-data-1113`), April 2019 receipts, three outlets in New York, 49,894 sales lines.

| Step                                       | Result                                            |
| ------------------------------------------ | ------------------------------------------------- |
| Lines of the 18 catalogue products         | sizes (Rg, Lg) merged into one product            |
| Packaged dark chocolate bar (Kaggle id 19) | dropped: not in the catalogue (debt D11)          |
| Receipts                                   | **12,434**, keyed by date, outlet and transaction |
| Receipts with two items or more            | **2,946**, at most 4 items                        |
| Period                                     | 2019-04-01 to 2019-04-29                          |

**Debt D14, found while preparing the data.** The prototype keyed receipts by `transaction_id`
and `customer_id`. Transaction ids restart every day in every outlet and half of the sales are
anonymous (`customer_id` 0), so its key merged unrelated receipts: "baskets" of up to 14 items,
3.30 items on average, where real receipts hold 2.13 on average and never more than 4. Every
rule of the prototype was learned on those merged baskets.

## Method

Association rules with one antecedent and one consequent, the only kind the serving side looks up (debt D10), mined on receipts of two items or more:

- support(A, B) = share of receipts holding A and B; confidence = P(B | A); lift = confidence / P(B)
- kept: support >= `min_support`, lift >= 1 (bought together more often than chance)

Pairs are counted directly instead of running the full Apriori search: a unit test checks the rules are identical to mlxtend's `apriori` + `association_rules` on pairs, and the training environment needs no extra dependency.

**Serving** ranks the consequents of every basket item by their best confidence, leaves out the basket, caps two products per category, then completes the list with best sellers up to five. A parity test checks that the evaluation scores exactly this ranking.

## Evaluation

**Protocol.** Temporal split, never a random one. For every test receipt of two items or more, each item in turn is hidden, the model receives the others, and the hidden item must come back in its top 5 (leave one out).

| Split      | Training receipts   | Evaluated on                              | Use                     |
| ---------- | ------------------- | ----------------------------------------- | ----------------------- |
| Validation | 2019-04-01 to 04-14 | 04-15 to 04-21                            | choose `min_support`    |
| Test       | 2019-04-01 to 04-21 | 04-22 to 04-29, 603 receipts, 1,259 cases | report, looked at once  |
| Production | the whole month     | none                                      | the registered artefact |

**Sweep, on validation** (served model: rules completed by best sellers):

| min_support | 0.005     | 0.01  | 0.02  | 0.03  | 0.05  | 0.08  |
| ----------- | --------- | ----- | ----- | ----- | ----- | ----- |
| Rules       | 56        | 50    | 24    | 22    | 16    | 4     |
| Hit@5       | **0.669** | 0.660 | 0.645 | 0.649 | 0.620 | 0.496 |

Chosen: **0.005**. The prototype used 0.05.

**Test week, against the popularity baseline:**

| Model                                       | Hit@5     | Precision@5 | MRR@5     | Coverage | Diversity | List length |
| ------------------------------------------- | --------- | ----------- | --------- | -------- | --------- | ----------- |
| Popularity (best sellers not in the basket) | 0.496     | 0.099       | 0.292     | 0.39     | 2.95      | 5.0         |
| Rules alone                                 | 0.500     | 0.100       | 0.371     | 0.72     | 1.44      | 2.4         |
| **Rules + best sellers (served)**           | **0.638** | **0.128**   | **0.411** | **0.78** | **3.17**  | 5.0         |

Paired bootstrap over receipts (2,000 resamples), difference of hit@5 with the baseline:

| Model                                            | Difference | 95 % interval        | Verdict                      |
| ------------------------------------------------ | ---------- | -------------------- | ---------------------------- |
| Rules alone                                      | +0.004     | [-0.029, +0.036]     | no difference                |
| **Rules + best sellers**                         | **+0.142** | **[+0.117, +0.166]** | **better than the baseline** |
| Prototype setting, rules alone, min_support 0.05 | -0.110     | [-0.145, -0.074]     | **worse than the baseline**  |

**Honest verdict.**
- Rules alone do **not** beat "suggest the best sellers" on hit rate. They rank better (MRR +27 %) and cover twice as much of the catalogue, but they return 2.4 items on average.
- Completed by best sellers, they beat the baseline clearly: +14 points of hit@5, an interval far from zero. That combination is what the API serves.
- The prototype's recommender, at min_support 0.05, was worse than the baseline it never measured itself against.

Precision@5 is bounded by 0.2 in this protocol (one hidden item, five suggestions): read hit@5 and MRR first.

**Production artefact** (whole month, min_support 0.005): 48 rules, all 18 products have at
least one rule, mean confidence 0.23, median lift 1.21.

## Limitations

- **One month of data**, April 2019. No seasonality, no trend: a summer menu or a new product is invisible until retraining.
- **Three outlets in one city**, a Kaggle sample: the habits of another shop may differ.
- **Small test set**: 603 receipts. The interval above accounts for it; small differences between close settings (0.005 and 0.01) are not meaningful.
- **Co-purchase is not complementarity.** A latte and a cappuccino are often bought together (two people, one receipt); the upsell therefore leaves out the categories already in the basket.
- **Offline only.** Hit@5 on past receipts is a proxy. Whether customers accept suggestions
  (objective OM2, 20 % upsell acceptance) is measured online, from the conversations container.

## Biases and risks

- **Popularity bias**: best sellers complete most lists, so the long tail stays rarely
  suggested (coverage 0.78: four products are never suggested on the test week).
- **Anonymous majority**: half of the receipts are anonymous; nothing suggests they differ, but no segment analysis was possible.
- No personal data is used or stored by the model.

## Operations

- Retrain: `make train` (Azure ML, scale-to-zero cluster, about ten minutes, cents per run).
- Promote: `make publish-model` copies a registered version to `current`; the API loads it at start. Rollback: `python -m src.ml.publish --version N`.
- A run registers nothing if the quality gate fails (a product outside the catalogue, a confidence outside [0, 1], an empty fallback).
