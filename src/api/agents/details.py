"""Details agent: answers questions about the shop and the menu from retrieved documents (ADR-008).

Three layers stand between a question and an invented answer:

1. Retrieval abstains: vector ranking, gated by the best semantic reranker score. When nothing
   relevant exists, the model is not called at all and a fixed answer is returned.
2. Documents derived from the catalogue (products, menus, dietary lists) are rendered again from
   the live catalogue before the model reads them, so a price or an allergen can never come from
   a stale index. A product removed from the catalogue is dropped, not described.
3. The prompt: answer only from the context, and say so when the context lacks the answer. This is
   the layer for the "caffeine in a latte" case, which retrieval scores as relevant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.api.agents.base import AgentReply, last_user_message, recent_turns
from src.api.core.catalog import Catalog
from src.api.core.llm import DECISION, ChatClient, Usage
from src.api.core.schemas import ChatMessage, SearchQuery
from src.api.core.search import Hit, Retriever, Strategy
from src.data_pipelines.knowledge import dietary_docs, menu_docs, product_doc

# Recall@3 was 1.00 on the ablation: three documents always held the answer.
TOP_K = 3
CATALOGUE_DOC_TYPES = {"product", "menu", "dietary"}

NO_ANSWER = (
    "I'm sorry, I don't have that information. A member of our team in the shop will be happy "
    "to help."
)

REWRITE_PROMPT = """\
Rewrite the customer's latest message as one question that can be understood without the
conversation, for a search engine. Replace pronouns such as "it" or "that one" with the item they
refer to. Keep the customer's wording otherwise. If the message already stands on its own, return
it unchanged.
"""

ANSWER_PROMPT = """\
You are the assistant of Merry's Way, a coffee shop. Answer the customer's latest question using
only the context below.

Rules:
- If the context does not contain the answer, say you do not have that information and suggest
  asking the team in the shop. Never guess, even when the answer seems obvious.
- Allergens: repeat exactly what the context says, including "may contain". Never state that an
  item is safe for an allergy beyond what the context says.
- Prices: quote them exactly as written in the context.
- Never mention "the context", documents or sources: speak as the shop's assistant.
- When the context lists items by category, answer for the category asked, including "none".
- Keep it short: two to four sentences, in a friendly tone. Do not end with an offer of more help.
- Do not take orders. If the customer says they want something, invite them to order it.

Context:
{context}
"""


class Details:
    def __init__(self, retriever: Retriever, chat: ChatClient, catalog: Catalog) -> None:
        self._retriever = retriever
        self._chat = chat
        products = catalog.products
        live = [product_doc(p) for p in products] + menu_docs(products) + dietary_docs(products)
        self._live_content = {doc.id: doc.content for doc in live}

    def answer(self, messages: Sequence[ChatMessage]) -> AgentReply:
        usage = Usage()
        query = last_user_message(messages)
        if len(messages) > 1:
            # A follow-up such as "does it contain lactose?" means nothing to a search engine.
            rewrite = self._chat.structured(
                [{"role": "system", "content": REWRITE_PROMPT}, *recent_turns(messages)],
                SearchQuery,
                DECISION,
            )
            query = rewrite.value.query or query
            usage += rewrite.usage

        retrieval = self._retriever.search(query, Strategy.VECTOR_GATED, top_k=TOP_K)
        hits = self.refresh(retrieval.hits)
        trace = {
            "query": query,
            "documents": [h.id for h in hits],
            "best_reranker_score": retrieval.best_reranker_score,
            "retrieval_ms": retrieval.latency_ms,
            "reranked": retrieval.reranked,
        }
        if not hits:
            return AgentReply("details", NO_ANSWER, usage=usage, trace=trace | {"abstained": True})

        context = "\n\n".join(f"[{i}] {h.title}\n{h.content}" for i, h in enumerate(hits, 1))
        # Kept in the trace for offline groundedness scoring; never persisted nor put on spans.
        trace["context"] = context
        result = self._chat.text(
            [
                {"role": "system", "content": ANSWER_PROMPT.format(context=context)},
                *recent_turns(messages),
            ]
        )
        return AgentReply(
            "details", result.text, usage=usage + result.usage, trace=trace | {"abstained": False}
        )

    def refresh(self, hits: Sequence[Hit]) -> list[Hit]:
        """Catalogue-derived documents are replaced by their live version, or dropped."""
        fresh = []
        for hit in hits:
            if hit.doc_type not in CATALOGUE_DOC_TYPES:
                fresh.append(hit)
            elif hit.id in self._live_content:
                fresh.append(replace(hit, content=self._live_content[hit.id]))
        return fresh
