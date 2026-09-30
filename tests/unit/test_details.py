"""Details agent, with fakes: abstention, live catalogue content, and query rewriting."""

from decimal import Decimal

import pytest

from src.api.agents.details import NO_ANSWER, Details
from src.api.core.catalog import Catalog
from src.api.core.llm import ChatResult, TextResult, Usage
from src.api.core.schemas import ChatMessage, SearchQuery
from src.api.core.search import Hit, Retrieval, Strategy
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl


@pytest.fixture(scope="module")
def catalog():
    return Catalog(build_catalog(read_jsonl(RAW_PATH)))


def hit(doc_id, doc_type="about", content="stale", reranker=2.5):
    return Hit(doc_id, doc_type, doc_id, content, score=0.7, reranker_score=reranker)


class FakeRetriever:
    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    def search(self, query, strategy, top_k):
        self.queries.append((query, strategy, top_k))
        return Retrieval(query, strategy, hits=self.hits, candidates=self.hits)


class FakeChat:
    def __init__(self, rewrite="rewritten question"):
        self.rewrite = rewrite
        self.structured_calls = []
        self.text_calls = []

    def structured(self, messages, schema, sampling):
        self.structured_calls.append(messages)
        return ChatResult(SearchQuery(query=self.rewrite), Usage(50, 10, 0), 200, "m")

    def text(self, messages, sampling=None):
        self.text_calls.append(messages)
        return TextResult("An answer.", Usage(400, 40, 0), 900, "m")


def user(text):
    return ChatMessage(role="user", content=text)


def details(catalog, hits, chat=None):
    retriever = FakeRetriever(hits)
    chat = chat or FakeChat()
    return Details(retriever, chat, catalog), retriever, chat


def system_prompt(chat):
    return chat.text_calls[0][0]["content"]


def test_nothing_relevant_means_a_fixed_answer_and_no_model_call(catalog):
    agent, _, chat = details(catalog, [])
    reply = agent.answer([user("Do you have parking?")])
    assert reply.content == NO_ANSWER
    assert reply.trace["abstained"] is True
    assert chat.text_calls == []  # nothing to invent from, so the model is never asked


def test_uses_the_gated_strategy_on_three_documents(catalog):
    agent, retriever, _ = details(catalog, [hit("about-hours")])
    agent.answer([user("When do you open?")])
    assert retriever.queries[0][1:] == (Strategy.VECTOR_GATED, 3)


def test_a_product_document_is_rendered_from_the_live_catalogue(catalog):
    # The index says 9.99; the catalogue says 4.75. The model must only ever read the catalogue.
    stale = hit("product-latte", "product", content="Latte. Price: 9.99 USD.")
    agent, _, chat = details(catalog, [stale])
    agent.answer([user("How much is a latte?")])
    prompt = system_prompt(chat)
    assert f"Price: {catalog.get('latte').price} USD." in prompt
    assert catalog.get("latte").price == Decimal("4.75")
    assert "9.99" not in prompt


def test_a_product_no_longer_in_the_catalogue_is_dropped(catalog):
    agent, _, chat = details(catalog, [hit("product-discontinued", "product")])
    reply = agent.answer([user("Tell me about the discontinued item")])
    assert reply.content == NO_ANSWER
    assert chat.text_calls == []


def test_curated_documents_are_passed_as_indexed(catalog):
    agent, _, chat = details(catalog, [hit("about-hours", content="Open 7am to 7pm.")])
    agent.answer([user("When do you open?")])
    assert "Open 7am to 7pm." in system_prompt(chat)


def test_a_first_message_is_searched_as_typed(catalog):
    agent, retriever, chat = details(catalog, [hit("about-hours")])
    agent.answer([user("When do you open?")])
    assert retriever.queries[0][0] == "When do you open?"
    assert chat.structured_calls == []


def test_a_follow_up_is_rewritten_before_the_search(catalog):
    chat = FakeChat(rewrite="Does the latte contain lactose?")
    agent, retriever, _ = details(catalog, [hit("product-latte", "product")], chat)
    history = [
        user("How much is a latte?"),
        ChatMessage(role="assistant", content="A latte is 4.75 USD."),
        user("does it contain lactose?"),
    ]
    reply = agent.answer(history)
    assert retriever.queries[0][0] == "Does the latte contain lactose?"
    assert reply.usage.input_tokens == 450  # rewrite plus answer
