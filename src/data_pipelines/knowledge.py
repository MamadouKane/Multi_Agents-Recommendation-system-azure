"""Assemble the knowledge corpus that the search index is built from (roadmap task 2.3).

Three families of documents, with different authors on purpose:

- **about** and **faq**: written by hand in data/knowledge/, reviewed like code.
- **product**, **menu** and **dietary**: generated from the catalogue on every run. "Which items
  are nut free?" is never a sentence someone typed: it is computed from the allergens, so it
  cannot drift from the catalogue (ADR-004).

    data/raw/products.jsonl + data/knowledge/*.md  ->  data/processed/knowledge.jsonl

Run from the repository root:  python -m src.data_pipelines.knowledge
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from src.api.core.schemas import Allergen, Category, KnowledgeDoc, Product
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl, slugify

KNOWLEDGE_DIR = Path("data/knowledge")
OUT_PATH = Path("data/processed/knowledge.jsonl")
CATALOGUE_SOURCE = "catalogue (data/raw/products.jsonl)"

ALLERGEN_LABELS: dict[Allergen, str] = {
    "milk": "milk",
    "eggs": "eggs",
    "gluten": "gluten",
    "tree_nuts": "tree nuts",
    "soy": "soy",
}

CROSS_CONTACT_NOTE = (
    "All products are prepared in the same kitchen, so traces of any allergen are possible."
)


# ---- hand-written documents -------------------------------------------------------------


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Read a `---` delimited header of `key: value` lines, and return it with the body."""
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("document must start with a --- front matter block")
    end = lines.index("---", 1)
    meta = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1 :]).strip()


def strip_comments(body: str) -> str:
    while "<!--" in body:
        start = body.index("<!--")
        body = body[:start] + body[body.index("-->", start) + 3 :]
    return body.strip()


def load_about(path: Path) -> KnowledgeDoc:
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    return KnowledgeDoc(
        id=meta["id"],
        doc_type="about",
        title=meta["title"],
        content=f"{meta['title']}\n\n{body}",
        source=meta["source"],
    )


def load_faq(path: Path) -> list[KnowledgeDoc]:
    """One `## question` section becomes one document: a chunk is one answer, not a page."""
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    docs = []
    # The leading newline makes the first heading split like every other one: without it the
    # first question is silently dropped.
    for section in ("\n" + strip_comments(body)).split("\n## ")[1:]:
        question, _, answer = section.partition("\n")
        question = question.strip()
        docs.append(
            KnowledgeDoc(
                id=f"faq-{slugify(question)}"[:80].rstrip("-"),
                doc_type="faq",
                title=question,
                content=f"Question: {question}\nAnswer: {answer.strip()}",
                source=meta["source"],
            )
        )
    return docs


# ---- documents generated from the catalogue ---------------------------------------------


def allergen_sentence(product: Product) -> str:
    if product.allergens:
        text = "Contains " + ", ".join(ALLERGEN_LABELS[a] for a in product.allergens) + "."
    else:
        text = "Contains none of the major allergens in its recipe."
    if product.may_contain:
        text += " May contain " + ", ".join(ALLERGEN_LABELS[a] for a in product.may_contain) + "."
    return text


def product_doc(product: Product) -> KnowledgeDoc:
    lines = [f"{product.name} ({product.category})"]
    if product.aliases:
        # Other spellings customers type, for example "Carmel syrup": indexed so BM25 matches them.
        lines.append(f"Also written: {', '.join(product.aliases)}.")
    content = "\n".join(
        [
            *lines,
            product.description,
            f"Ingredients: {', '.join(product.ingredients)}.",
            f"Allergens: {allergen_sentence(product)}",
            f"Price: {product.price} {product.currency}.",
        ]
    )
    return KnowledgeDoc(
        id=f"product-{product.product_id}",
        doc_type="product",
        title=product.name,
        content=content,
        product_id=product.product_id,
        category=product.category,
        price=product.price,
        source=CATALOGUE_SOURCE,
    )


def menu_docs(products: list[Product]) -> list[KnowledgeDoc]:
    """The full menu, plus one document per category for questions such as 'which pastries?'."""
    categories: list[Category] = sorted({p.category for p in products})
    docs = []
    for category in categories:
        items = [p for p in products if p.category == category]
        lines = [f"- {p.name}: {p.price} {p.currency}" for p in items]
        docs.append(
            KnowledgeDoc(
                id=f"menu-{slugify(category)}",
                doc_type="menu",
                title=f"Menu, {category}",
                content=f"Menu, {category} ({len(items)} items)\n" + "\n".join(lines),
                category=category,
                source=CATALOGUE_SOURCE,
            )
        )
    everything = [f"- {p.name} ({p.category}): {p.price} {p.currency}" for p in products]
    docs.append(
        KnowledgeDoc(
            id="menu-full",
            doc_type="menu",
            title="Full menu",
            content=f"Full menu ({len(products)} items)\n" + "\n".join(everything),
            source=CATALOGUE_SOURCE,
        )
    )
    return docs


def dietary_docs(products: list[Product]) -> list[KnowledgeDoc]:
    """'Which items are free of X?', computed from certain AND possible allergens."""
    docs = []
    for allergen, label in ALLERGEN_LABELS.items():
        if allergen == "soy":
            continue  # nobody asks for a soy-free coffee; the product documents still say it
        free = [p.name for p in products if allergen not in {*p.allergens, *p.may_contain}]
        not_free = [p.name for p in products if p.name not in free]
        content = (
            f"Items without {label}: " + (", ".join(free) if free else "none") + ".\n"
            f"Items with {label}, certain or possible: "
            + (", ".join(not_free) if not_free else "none")
            + f".\n{CROSS_CONTACT_NOTE}"
        )
        docs.append(
            KnowledgeDoc(
                id=f"dietary-{slugify(label)}-free",
                doc_type="dietary",
                title=f"{label.capitalize()}-free items",
                content=content,
                source=CATALOGUE_SOURCE,
            )
        )
    return docs


def build_corpus(products: list[Product], knowledge_dir: Path) -> list[KnowledgeDoc]:
    docs: list[KnowledgeDoc] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        if path.name == "faq.md":
            docs.extend(load_faq(path))
        else:
            docs.append(load_about(path))
    docs += [product_doc(p) for p in products if p.is_active]
    docs += menu_docs([p for p in products if p.is_active])
    docs += dietary_docs([p for p in products if p.is_active])

    ids = [d.id for d in docs]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate document ids: {duplicates}")
    return docs


def summary(docs: Iterable[KnowledgeDoc]) -> Counter[str]:
    return Counter(d.doc_type for d in docs)


def main() -> int:
    products = build_catalog(read_jsonl(RAW_PATH))
    docs = build_corpus(products, KNOWLEDGE_DIR)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as handle:
        for doc in docs:
            handle.write(doc.model_dump_json() + "\n")

    print(f"{len(docs)} documents written to {OUT_PATH}")
    for doc_type, count in sorted(summary(docs).items()):
        print(f"  {doc_type:8} {count:3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
