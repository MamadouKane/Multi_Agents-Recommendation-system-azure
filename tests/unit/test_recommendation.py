"""Recommendation agent, with a fake model: US3, three to five items that exist."""

import csv
import json

import pytest

from src.api.agents.recommendation import NOTHING, Recommendation
from src.api.core.catalog import Catalog
from src.api.core.llm import ChatResult, Usage
from src.api.core.recommender import RecommendationArtifacts, Recommender
from src.api.core.schemas import ChatMessage, RecommendationRequest
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
from src.data_pipelines.recommendations import APRIORI_PATH, POPULARITY_PATH, convert


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


@pytest.fixture(scope="module")
def recommender(catalog):
    apriori = json.loads(APRIORI_PATH.read_text(encoding="utf-8"))
    with POPULARITY_PATH.open(encoding="utf-8", newline="") as f:
        popularity = list(csv.DictReader(f))
    return Recommender(convert(apriori, popularity, catalog).artifacts, catalog)


class FakeChat:
    def __init__(self, request):
        self.request = request
        self.calls = []

    def structured(self, messages, schema, sampling):
        self.calls.append(messages)
        return ChatResult(self.request, Usage(700, 30, 10), 600, "m")


def request(kind, product_ids=(), categories=()):
    return RecommendationRequest(
        kind=kind, product_ids=list(product_ids), categories=list(categories)
    )


def run(catalog, recommender, req, messages=None):
    chat = FakeChat(req)
    agent = Recommendation(chat, catalog, recommender)
    return agent.answer(messages or [ChatMessage(role="user", content="any suggestion?")]), chat


def test_us3_three_to_five_items_that_exist(catalog, recommender):
    reply, _ = run(catalog, recommender, request("basket", ["latte"]))
    assert 3 <= len(reply.trace["recommended"]) <= 5
    assert all(pid in catalog for pid in reply.trace["recommended"])
    assert reply.content.startswith("These go well with Latte:")


def test_an_item_without_rules_is_completed_with_best_sellers(catalog, recommender):
    # oatmeal-scone has no rule in the legacy artefacts.
    reply, _ = run(catalog, recommender, request("basket", ["oatmeal-scone"]))
    assert len(reply.trace["recommended"]) >= 3
    assert "oatmeal-scone" not in reply.trace["recommended"]


def test_an_empty_basket_request_uses_the_current_order(catalog, recommender):
    history = [
        ChatMessage(role="user", content="a croissant"),
        ChatMessage(
            role="assistant",
            content="...",
            memory={"order": {"items": [{"product_id": "croissant", "quantity": 1}]}},
        ),
        ChatMessage(role="user", content="what goes well with my order?"),
    ]
    reply, chat = run(catalog, recommender, request("basket"), history)
    assert "Current order: croissant" in chat.calls[0][0]["content"]
    assert reply.trace["kind"] == "basket"
    assert "croissant" not in reply.trace["recommended"]


def test_an_invented_identifier_falls_back_to_popular(catalog, recommender):
    reply, _ = run(catalog, recommender, request("basket", ["unicorn-frappe"]))
    assert reply.trace["kind"] == "popular"


def test_category_request(catalog, recommender):
    reply, _ = run(catalog, recommender, request("popular_in_category", categories=["Coffee"]))
    assert reply.trace["recommended"][0] == "cappuccino"
    assert reply.content.startswith("Our most popular coffees:")


def test_descriptions_come_from_the_catalogue(catalog, recommender):
    reply, _ = run(catalog, recommender, request("popular"))
    first = catalog.get(reply.trace["recommended"][0])
    assert first.description.split(". ")[0] in reply.content


def test_no_product_means_an_explicit_answer(catalog):
    empty = Recommender(RecommendationArtifacts(source="t", rules={}, popularity={}), catalog)
    reply, _ = run(catalog, empty, request("popular"))
    assert reply.content == NOTHING
