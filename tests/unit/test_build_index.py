"""The index definition and the document mapping, checked without any network call."""

from decimal import Decimal

import pytest

from src.api.core.schemas import KnowledgeDoc
from src.data_pipelines.build_index import (
    EMBEDDING_DIMENSIONS,
    batched,
    index_definition,
    to_search_document,
)


@pytest.fixture(scope="module")
def index():
    return index_definition("coffee-knowledge")


def fields_by_name(index):
    return {f.name: f for f in index.fields}


def test_id_is_the_key(index):
    keys = [f.name for f in index.fields if f.key]
    assert keys == ["id"]


def test_text_fields_use_the_english_analyzer(index):
    fields = fields_by_name(index)
    for name in ("title", "content"):
        assert fields[name].searchable
        assert fields[name].analyzer_name == "en.microsoft"


def test_vector_field_matches_the_embedding_model(index):
    vector = fields_by_name(index)["content_vector"]
    assert vector.vector_search_dimensions == EMBEDDING_DIMENSIONS == 1536
    assert vector.vector_search_profile_name == index.vector_search.profiles[0].name
    assert vector.retrievable is False


def test_filters_needed_by_the_agents(index):
    fields = fields_by_name(index)
    assert fields["doc_type"].filterable and fields["category"].filterable
    assert fields["product_id"].filterable
    assert fields["price"].sortable


def test_hnsw_uses_cosine(index):
    params = index.vector_search.algorithms[0].parameters
    assert params.metric == "cosine"


def test_semantic_configuration_reads_title_and_content(index):
    config = index.semantic_search.configurations[0]
    assert index.semantic_search.default_configuration_name == config.name
    assert config.prioritized_fields.title_field.field_name == "title"
    assert [f.field_name for f in config.prioritized_fields.content_fields] == ["content"]


def doc(**overrides):
    values = {
        "id": "product-latte",
        "doc_type": "product",
        "title": "Latte",
        "content": "Latte (Coffee)",
        "product_id": "latte",
        "category": "Coffee",
        "price": Decimal("4.75"),
        "source": "catalogue",
    }
    values.update(overrides)
    return KnowledgeDoc(**values)


def test_document_mapping():
    mapped = to_search_document(doc(), [0.0] * EMBEDDING_DIMENSIONS)
    assert mapped["id"] == "product-latte"
    assert mapped["price"] == 4.75
    assert len(mapped["content_vector"]) == EMBEDDING_DIMENSIONS


def test_documents_without_price_or_product():
    mapped = to_search_document(
        doc(id="about-hours", doc_type="about", product_id=None, category=None, price=None),
        [0.0] * EMBEDDING_DIMENSIONS,
    )
    assert mapped["price"] is None and mapped["product_id"] is None


def test_a_vector_of_the_wrong_size_is_rejected():
    with pytest.raises(ValueError, match="1536"):
        to_search_document(doc(), [0.0] * 3072)


def test_batching_covers_every_item_once():
    batches = list(batched(list(range(10)), 4))
    assert [len(b) for b in batches] == [4, 4, 2]
    assert [x for b in batches for x in b] == list(range(10))
