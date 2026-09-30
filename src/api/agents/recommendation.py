"""Recommendation agent: the model picks the kind of recommendation, the artefacts pick the items.

The legacy agent made two model calls, one to classify and one to write the list with "a very
small description", free to describe items in its own words. Here the model makes one structured
decision (`RecommendationRequest`), the `Recommender` chooses from the live catalogue, and the
answer is a template built from catalogue descriptions: nothing in it can be invented.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.api.agents.base import AgentReply, recent_turns
from src.api.agents.order import join, previous_order
from src.api.core.catalog import Catalog
from src.api.core.llm import DECISION, ChatClient
from src.api.core.recommender import Recommender
from src.api.core.schemas import ChatMessage, Product, RecommendationRequest

# US3: three to five items.
MIN_ITEMS = 3
MAX_ITEMS = 5

RECOMMENDATION_PROMPT = """\
You help customers of Merry's Way, a coffee shop, choose what to buy. Decide which kind of
recommendation fits the customer's latest message:

- basket: they ask what goes well with items (their order, or items they name). Put those items'
  identifiers in `product_ids`. Leave it empty to mean their current order.
- popular_in_category: they ask about a kind of item ("which coffee?", "a pastry?", "a syrup?").
  Put the categories in `categories`: Coffee, Bakery (pastries, scones, biscotti, croissants),
  Drinking Chocolate, Flavours (syrups).
- popular: anything else, such as "what do you recommend?" or "what is popular?".

Menu (identifier: name):
{menu}

Current order: {basket}
"""

# What a customer calls each catalogue category.
CATEGORY_LABELS: dict[str, str] = {
    "Coffee": "coffees",
    "Bakery": "pastries",
    "Drinking Chocolate": "chocolate drinks",
    "Flavours": "syrups",
}

NOTHING = "I don't have a suggestion for that right now. Can I help you with your order?"


class Recommendation:
    def __init__(self, chat: ChatClient, catalog: Catalog, recommender: Recommender) -> None:
        self._chat = chat
        self._catalog = catalog
        self._recommender = recommender

    def answer(self, messages: Sequence[ChatMessage]) -> AgentReply:
        basket = [item.product_id for item in previous_order(messages)[0].items]
        prompt = RECOMMENDATION_PROMPT.format(
            menu=self._catalog.render_menu_for_prompt(), basket=", ".join(basket) or "(empty)"
        )
        result = self._chat.structured(
            [{"role": "system", "content": prompt}, *recent_turns(messages)],
            RecommendationRequest,
            DECISION,
        )
        request = result.value
        products, kind = self.recommend(request, basket)
        return AgentReply(
            "recommendation",
            compose(products, kind, self._catalog, request),
            usage=result.usage,
            trace={
                "request": request.model_dump(mode="json"),
                "kind": kind,
                "recommended": [p.product_id for p in products],
                "artifacts": self._recommender.source,
            },
        )

    def recommend(
        self, request: RecommendationRequest, basket: Sequence[str]
    ) -> tuple[list[Product], str]:
        """The products to suggest, and the kind actually served after fallbacks."""
        if request.kind == "popular_in_category" and request.categories:
            return self._recommender.popular(request.categories, MAX_ITEMS), request.kind

        if request.kind == "basket":
            # Identifiers come from the model: keep only those the catalogue knows.
            anchors = [pid for pid in request.product_ids if pid in self._catalog] or list(basket)
            if anchors:
                products = self._recommender.for_basket(anchors, MAX_ITEMS)
                if len(products) < MIN_ITEMS:
                    # Some items have no rule: complete with best sellers, never repeating.
                    seen = {p.product_id for p in products} | set(anchors)
                    extra = [
                        p
                        for p in self._recommender.popular(top_k=MAX_ITEMS + len(seen))
                        if p.product_id not in seen
                    ]
                    products += extra[: MIN_ITEMS - len(products)]
                return products, "basket"

        return self._recommender.popular(top_k=MAX_ITEMS), "popular"


def compose(
    products: Sequence[Product], kind: str, catalog: Catalog, request: RecommendationRequest
) -> str:
    if not products:
        return NOTHING
    if kind == "basket":
        names = [p.name for pid in request.product_ids if (p := catalog.get(pid)) is not None]
        intro = (
            f"These go well with {join(names, 'and')}:"
            if names
            else "These go well with your order:"
        )
    elif kind == "popular_in_category":
        labels = [CATEGORY_LABELS.get(c, c) for c in request.categories]
        intro = f"Our most popular {join(labels, 'and')}:"
    else:
        intro = "Our customers' favourites:"
    lines = [f"- {p.name}: {first_sentence(p.description)}" for p in products]
    return "\n".join([intro, *lines, "", "Would you like to add any of these to your order?"])


def first_sentence(text: str) -> str:
    head, dot, _ = text.partition(". ")
    return head + "." if dot else text
