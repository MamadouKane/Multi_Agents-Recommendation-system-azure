"""What a turn costs, from its token usage (NFR4: under 0.01 USD per conversation).

Prices are configuration, not code: they change, and they differ between deployment types. The
defaults are OpenAI's list prices for gpt-5.4-mini, checked on 2026-09-30; the Data Zone EU
deployment used here costs a little more, so confirm them on the Azure pricing page. Reasoning
tokens are billed as output and are already counted in `output_tokens`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from src.api.core.llm import Usage

MILLION = Decimal(1_000_000)
MICRO_DOLLAR = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def cost(self, usage: Usage) -> Decimal:
        total = (
            usage.input_tokens * self.input_usd_per_million
            + usage.output_tokens * self.output_usd_per_million
        ) / MILLION
        return total.quantize(MICRO_DOLLAR, rounding=ROUND_HALF_UP)
