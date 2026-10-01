"""Recommender training code: data keys, rule mining, offline evaluation, serving parity."""

import json
from itertools import combinations
from pathlib import Path

import pytest

from src.api.core.catalog import Catalog
from src.api.core.recommender import RecommendationArtifacts, Recommender
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.ml.reco.artifacts import build_artifacts
from src.ml.reco.data import (
    CATEGORIES,
    DROPPED_IDS,
    KAGGLE_TO_PRODUCT,
    Basket,
    load_baskets,
    popularity,
    split_by_date,
)
from src.ml.reco.evaluate import evaluate, paired_bootstrap
from src.ml.reco.register import gate
from src.ml.reco.rules import mine_rules
from src.ml.reco.serve import for_basket, popular

SALES = Path("data/raw/sales")


@pytest.fixture(scope="module")
def baskets():
    return load_baskets(SALES)


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


def basket(date, *items):
    return Basket(date, frozenset(items))


class TestData:
    def test_every_mapped_product_is_in_the_catalogue(self, catalog):
        assert set(KAGGLE_TO_PRODUCT.values()) == {p.product_id for p in catalog.products}
        assert {p.product_id: p.category for p in catalog.products} == CATEGORIES

    def test_the_packaged_bar_is_dropped_and_sizes_are_merged(self):
        assert 19 in DROPPED_IDS and 19 not in KAGGLE_TO_PRODUCT
        assert KAGGLE_TO_PRODUCT[58] == KAGGLE_TO_PRODUCT[59] == "dark-chocolate-drinking"

    def test_a_receipt_is_keyed_by_date_outlet_and_transaction(self, baskets):
        # D14: the legacy key merged unrelated anonymous receipts into "baskets" of up to 14 items.
        assert len(baskets) == 12434
        assert max(len(b.items) for b in baskets) == 4

    def test_baskets_are_in_time_order(self, baskets):
        dates = [b.date for b in baskets]
        assert dates == sorted(dates) and dates[0] == "2019-04-01" and dates[-1] == "2019-04-29"

    def test_the_split_is_temporal(self, baskets):
        past, future = split_by_date(baskets, "2019-04-22")
        assert max(b.date for b in past) < "2019-04-22" <= min(b.date for b in future)


class TestRules:
    def test_a_hand_computed_example(self):
        data = [basket("d", "latte", "croissant")] * 3 + [basket("d", "latte", "caramel-syrup")]
        rules = {
            (r.antecedent, r.consequent): r for r in mine_rules(data, min_support=0.5, min_lift=0)
        }
        latte_croissant = rules[("latte", "croissant")]
        assert latte_croissant.support == 0.75
        assert latte_croissant.confidence == 0.75  # 3 of the 4 lattes came with a croissant
        assert rules[("croissant", "latte")].confidence == 1.0
        assert ("latte", "caramel-syrup") not in rules  # support 0.25 < 0.5

    def test_single_item_receipts_do_not_dilute_support(self):
        data = [basket("d", "latte", "croissant"), basket("d", "latte")]
        assert mine_rules(data, min_support=1.0, min_lift=0)[0].support == 1.0

    def test_same_rules_as_mlxtend_on_pairs(self, baskets):
        pd = pytest.importorskip("pandas")
        mlxtend = pytest.importorskip("mlxtend.frequent_patterns")
        multi = [b for b in baskets if len(b.items) >= 2]
        items = sorted(CATEGORIES)
        onehot = pd.DataFrame([[i in b.items for i in items] for b in multi], columns=items)
        frequent = mlxtend.apriori(onehot, min_support=0.01, use_colnames=True, max_len=2)
        theirs = mlxtend.association_rules(frequent, metric="lift", min_threshold=1.0)
        theirs = {
            (next(iter(r.antecedents)), next(iter(r.consequents))): round(r.confidence, 9)
            for r in theirs.itertuples()
        }
        ours = {
            (r.antecedent, r.consequent): round(r.confidence, 9) for r in mine_rules(baskets, 0.01)
        }
        assert ours == theirs


class TestEvaluation:
    def test_leave_one_out_counts_every_hidden_item(self):
        test = [basket("d", "latte", "croissant")]
        always_croissant = lambda given: ["croissant", "latte"]  # noqa: E731
        scores = evaluate(always_croissant, test, CATEGORIES)
        assert scores.cases == 2  # each item hidden once
        assert scores.hit_rate == 1.0
        assert scores.mrr == pytest.approx((1 / 1 + 1 / 2) / 2)
        assert scores.precision == pytest.approx(1 / 5)

    def test_single_item_baskets_are_not_test_cases(self):
        with pytest.raises(ValueError):
            evaluate(lambda given: [], [basket("d", "latte")], CATEGORIES)

    def test_a_model_compared_with_itself_has_a_zero_interval(self):
        hits = [(1, 2), (0, 2), (2, 3)]
        result = paired_bootstrap(hits, hits, samples=200)
        assert result == {"delta": 0.0, "ci_low": 0.0, "ci_high": 0.0}

    def test_a_clearly_better_model_has_an_interval_above_zero(self):
        result = paired_bootstrap([(2, 2)] * 50, [(0, 2)] * 25 + [(1, 2)] * 25, samples=200)
        assert result["ci_low"] > 0


class TestServingParity:
    """The evaluation must score exactly what the API serves."""

    def test_basket_ranking_matches_the_api(self, baskets, catalog):
        artifacts = build_artifacts(mine_rules(baskets, 0.005), popularity(baskets), source="test")
        api = Recommender(RecommendationArtifacts.model_validate(artifacts), catalog)
        products = sorted(CATEGORIES)
        for size in (1, 2):
            for given in combinations(products, size):
                for complements in (False, True):
                    ours = for_basket(artifacts["rules"], CATEGORIES, given, 5, complements)
                    theirs = api.for_basket(given, 5, complements_only=complements)
                    assert ours == [p.product_id for p in theirs], given

    def test_popularity_matches_the_api(self, baskets, catalog):
        artifacts = build_artifacts([], popularity(baskets), source="test")
        api = Recommender(RecommendationArtifacts.model_validate(artifacts), catalog)
        assert popular(artifacts["popularity"], CATEGORIES) == [p.product_id for p in api.popular()]


class TestArtefact:
    def test_the_training_output_is_the_serving_format(self, baskets):
        artifacts = build_artifacts(
            mine_rules(baskets, 0.005), popularity(baskets), source="t", version="m:1"
        )
        loaded = RecommendationArtifacts.model_validate(json.loads(json.dumps(artifacts)))
        assert loaded.version == "m:1"

    def test_the_gate_passes_a_clean_artefact(self, baskets):
        assert (
            gate(build_artifacts(mine_rules(baskets, 0.005), popularity(baskets), source="t")) == []
        )

    def test_the_gate_refuses_a_product_outside_the_catalogue(self):
        bad = {
            "rules": {"latte": [{"product_id": "packaged-bar", "confidence": 0.5}]},
            "popularity": {"latte": 3},
        }
        assert gate(bad) == ["unknown consequent packaged-bar"]

    def test_the_gate_refuses_an_empty_fallback(self):
        assert "empty popularity" in gate({"rules": {}, "popularity": {}})[0]
