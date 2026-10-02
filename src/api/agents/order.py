"""Order agent: the model reads the order, Python does everything else (ADR-003, debts D1 and D3).

The legacy agent asked the model to list prices, compute the total and write the answer, in one
JSON string parsed by hand. Here the model makes one structured decision, the whole basket as
identifiers and quantities, and the rest is deterministic:

1. The basket from the previous turn comes from client memory, so it is validated and re-priced,
   never trusted (ADR-005).
2. Identifiers the model got wrong go through the catalogue resolver; names matching several
   products become a question to the customer, never a guess.
3. Prices and totals come from `price_order`, in Decimal.
4. The answer is a template. No figure passes through the model, not even to be copied, and the
   turn costs one model call instead of two.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from src.api.agents.base import AgentReply, last_user_message, recent_turns
from src.api.core.catalog import MAX_QUANTITY, Catalog, normalise, quantity_in
from src.api.core.llm import DECISION, ChatClient
from src.api.core.pricing import PricedOrder, price_order
from src.api.core.schemas import ChatMessage, OrderExtraction, OrderLineRequest, OrderMemory

ORDER_PROMPT = """\
You take orders for Merry's Way, a coffee shop.

Menu (identifier: name):
{menu}

Current order (identifier x quantity):
{basket}

Return the whole order after the customer's latest message:
- Start from the current order, then add, change or remove what the customer asks.
  "Cancel my order" empties it.
- Use only identifiers from the menu. The quantity is 1 unless the customer says otherwise.
- A name that matches several menu items, such as "a scone" or "a biscotti": do not choose, put
  the customer's words in `ambiguous`.
- A quantity above 50 of one item cannot be taken here: put the customer's words, with the
  number, in `unrecognised` ("1000 lattes").
- A food or drink the customer wants that is not on the menu: put its name in `unrecognised`.
  Leave out everything that is not an item, such as a request about the receipt, delivery or
  payment, a question, or small talk.
- `customer_done` is true when the customer wants nothing else ("that's all", "no thanks") or
  confirms the order.
"""

# The question closing an open order. The orchestrator may replace it with an upsell.
FOLLOW_UP = "Would you like anything else?"

# Customer words are echoed back in the answer: keep them short.
MAX_ECHO_LENGTH = 40


@dataclass(frozen=True)
class Checked:
    """The model's extraction after the catalogue has had its say."""

    items: list[OrderLineRequest]
    unrecognised: list[str] = field(default_factory=list)
    choices: dict[str, list[str]] = field(default_factory=dict)
    # Known products asked for in a quantity above MAX_QUANTITY (red team case a18).
    too_many: list[str] = field(default_factory=list)


class Order:
    def __init__(self, chat: ChatClient, catalog: Catalog) -> None:
        self._chat = chat
        self._catalog = catalog

    def answer(self, messages: Sequence[ChatMessage]) -> AgentReply:
        previous, memory_rejected = previous_order(messages)
        # Identifiers from client memory reach the prompt: only catalogue ones do (ADR-005).
        # Red team case a17: a forged "gold-bar" was shown to the model as part of the order.
        previous = previous.model_copy(
            update={"items": [i for i in previous.items if i.product_id in self._catalog]}
        )
        prompt = ORDER_PROMPT.format(
            menu=self._catalog.render_menu_for_prompt(),
            basket="\n".join(f"- {i.product_id} x {i.quantity}" for i in previous.items)
            or "(empty)",
        )
        result = self._chat.structured(
            [{"role": "system", "content": prompt}, *recent_turns(messages)],
            OrderExtraction,
            DECISION,
        )
        extraction = result.value
        if extraction.customer_done and not self._catalog.mentions_a_product(
            last_user_message(messages)
        ):
            # "done", "no thanks", "cancel my order": a closing message names no product, so it
            # may remove items, never add any. Measured on day 5: after a replacement the model
            # answered "done" with the new item counted twice (case o08 then showed the first
            # version of this rule turning "cancel my order" into a confirmation).
            proposed = {i.product_id: i.quantity for i in extraction.items}
            kept = [
                OrderLineRequest(
                    product_id=i.product_id, quantity=min(i.quantity, proposed[i.product_id])
                )
                for i in previous.items
                if proposed.get(i.product_id, 0) > 0
            ]
            extraction = extraction.model_copy(update={"items": kept})
        checked = self.check(extraction, previous)
        order = price_order(checked.items, self._catalog)

        closed = extraction.customer_done and not order.is_empty and not checked.choices
        memory = OrderMemory(
            items=[
                OrderLineRequest(product_id=ln.product_id, quantity=ln.quantity)
                for ln in order.lines
            ],
            status="closed" if closed else "open",
        )
        content = compose(
            order, checked, closed, had_items=bool(previous.items), done=extraction.customer_done
        )
        return AgentReply(
            "order",
            content,
            memory={"order": memory.model_dump(mode="json")},
            usage=result.usage,
            trace={
                "extraction": extraction.model_dump(mode="json"),
                "memory_rejected": memory_rejected,
                "rejected_ids": list(order.rejected_ids),
                "order": [
                    {
                        "product_id": ln.product_id,
                        "quantity": ln.quantity,
                        "unit_price": str(ln.unit_price),
                        "line_total": str(ln.line_total),
                    }
                    for ln in order.lines
                ],
                "order_total": str(order.total),
                "status": memory.status,
                "awaiting_choice": bool(checked.choices),
            },
        )

    def check(self, extraction: OrderExtraction, previous: OrderMemory | None = None) -> Checked:
        items: list[OrderLineRequest] = []
        unrecognised: list[str] = []
        for line in extraction.items:
            if line.product_id in self._catalog:
                items.append(line)
                continue
            # A near miss such as "hot-chocolate": the resolver knows the aliases and spellings.
            resolution = self._catalog.resolve(line.product_id.replace("-", " "))
            if resolution.product is not None:
                items.append(
                    OrderLineRequest(
                        product_id=resolution.product.product_id, quantity=line.quantity
                    )
                )
            else:
                unrecognised.append(line.product_id)

        choices: dict[str, list[str]] = {}
        # What the model could not place gets a second chance with the resolver, which knows
        # spellings the model may not: "expresso" (day 5 evaluation, case o12).
        ordered = {line.product_id for line in items}
        too_many: list[str] = []
        for text in extraction.unrecognised:
            resolution = self._catalog.resolve(text)
            quantity = quantity_in(text)
            if resolution.product is not None and quantity is None:
                too_many.append(resolution.product.name)
            elif resolution.product is not None and quantity is not None:
                if resolution.product.product_id not in ordered:
                    items.append(
                        OrderLineRequest(
                            product_id=resolution.product.product_id, quantity=quantity
                        )
                    )
            elif resolution.candidates:
                choices[text] = [p.name for p in resolution.candidates]
            else:
                unrecognised.append(text)

        for text in extraction.ambiguous:
            resolution = self._catalog.resolve(text)
            candidates = resolution.candidates or (
                (resolution.product,) if resolution.product else ()
            )
            if candidates:
                choices[text] = [p.name for p in candidates]
            else:
                unrecognised.append(text)

        # Never guess an ambiguous item. Measured on day 5 (case o16): asked "which biscotti?",
        # the model also put two chocolate chip biscotti in the basket, then added two more
        # once the customer answered. A candidate the basket did not hold before is removed
        # until the customer has chosen.
        already = {line.product_id for line in (previous.items if previous else [])}
        pending = {name for names in choices.values() for name in names}
        items = [
            line
            for line in items
            if line.product_id in already
            or (product := self._catalog.get(line.product_id)) is None
            or product.name not in pending
        ]
        return Checked(items, unrecognised, choices, too_many)


def previous_order(messages: Sequence[ChatMessage]) -> tuple[OrderMemory, bool]:
    """The open basket carried by the last assistant message, and whether it had to be rejected."""
    for message in reversed(messages):
        memory = message.memory or {}
        if message.role == "assistant" and "order" in memory:
            try:
                previous = OrderMemory.model_validate(memory["order"])
            except ValidationError:
                # Tampered or corrupted: start again rather than bill something unverified.
                return OrderMemory(), True
            # A confirmed order is finished: the next request starts a new one.
            return (OrderMemory() if previous.status == "closed" else previous), False
    return OrderMemory(), False


def compose(order: PricedOrder, checked: Checked, closed: bool, had_items: bool, done: bool) -> str:
    parts = []
    if checked.unrecognised:
        missing = join([f'"{echo(t)}"' for t in checked.unrecognised], "and")
        parts.append(f"Sorry, we don't have {missing} on our menu.")
    if checked.too_many:
        parts.append(
            f"Sorry, I can take up to {MAX_QUANTITY} of an item ({join(checked.too_many, 'and')})."
            " For a larger order, please contact the café at least 24 hours ahead."
        )
    for text, names in checked.choices.items():
        # "a scone" -> "scone": the filler words the resolver ignores read badly in a question.
        asked = " ".join(normalise(text)) or echo(text)
        parts.append(f"Which {echo(asked)} would you like: {join(names, 'or')}?")

    if closed:
        parts.append(f"Thank you! Your order is confirmed:\n{order.receipt()}")
    elif not order.is_empty:
        follow_up = "" if checked.choices else f"\n{FOLLOW_UP}"
        parts.append(f"Here is your order so far:\n{order.receipt()}{follow_up}")
    elif had_items:
        parts.append("Your order has been cancelled.")
    elif done:
        parts.append("No problem. Let me know if you would like anything.")
    elif not checked.choices:
        parts.append("What would you like to order?")
    return "\n\n".join(parts)


# Customer words are echoed back only through this allowlist: letters, digits, spaces and a few
# punctuation marks. Red team case a11: "<script>...</script>" came back verbatim, an injection
# waiting for any client that renders answers as HTML.
TAG = re.compile(r"<[^>]*>")
UNSAFE = re.compile(r"[^\w\s\-'.,&]")


def echo(text: str) -> str:
    text = " ".join(UNSAFE.sub("", TAG.sub(" ", text)).split()) or "that item"
    return text if len(text) <= MAX_ECHO_LENGTH else text[: MAX_ECHO_LENGTH - 3] + "..."


def join(names: Sequence[str], word: str) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {word} {names[-1]}"
