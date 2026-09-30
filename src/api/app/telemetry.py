"""Export to Application Insights, and the business metrics of a turn (roadmap 3.4, OT4).

`configure_telemetry` turns the no-op OpenTelemetry API used across the code into real exports:
traces (FastAPI requests, Azure SDK calls, the spans in `core` and `agents`), logs and metrics.
Ingestion authenticates with Entra ID: the Application Insights resource refuses its own keys
(`DisableLocalAuth`), so the connection string only names the target.

Without a connection string, locally or in tests, nothing is exported and nothing breaks.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any

from azure.identity import DefaultAzureCredential
from opentelemetry import metrics
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource

from src.api.agents.assistant import Turn
from src.api.core.catalog import Catalog
from src.api.core.settings import Settings
from src.api.core.tracing import ConversationSpanProcessor

logger = logging.getLogger(__name__)

SERVICE = "coffee-ai-api"
_configured = False

meter = metrics.get_meter("coffee_ai")
TURNS = meter.create_counter("chat.turns", description="Turns answered, by agent and route.")
TOKENS = meter.create_counter("chat.tokens", unit="{token}", description="Model tokens, by type.")
COST = meter.create_counter("chat.cost.usd", unit="USD", description="Model cost of the turns.")
BLOCKED = meter.create_counter("guard.blocked", description="Refused turns, by layer and reason.")
DURATION = meter.create_histogram("chat.turn.duration", unit="ms", description="Turn latency.")
# Alert at severity 1 when above zero: a bill that disagrees with the catalogue (OM4).
MISMATCH = meter.create_counter(
    "order.total.mismatch", description="Orders whose billed total disagrees with the catalogue."
)


def configure_telemetry(settings: Settings) -> bool:
    global _configured
    if _configured:
        return True
    if not settings.applicationinsights_connection_string:
        logger.info("telemetry export disabled: APPLICATIONINSIGHTS_CONNECTION_STRING is not set")
        return False

    # Imported here: the distribution patches libraries on import, which tests do not need.
    from azure.monitor.opentelemetry import configure_azure_monitor

    # Health probes arrive every few seconds and would drown the useful requests.
    os.environ.setdefault("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "health")
    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        credential=DefaultAzureCredential(),
        resource=Resource.create({SERVICE_NAME: SERVICE, SERVICE_VERSION: settings.app_version}),
        span_processors=[ConversationSpanProcessor()],
        logger_name="src",
    )
    _configured = True
    logger.info("telemetry export enabled")
    return True


def record_turn(turn: Turn, cost_usd: Decimal, catalog: Catalog) -> None:
    labels = {"agent": turn.agent, "route": turn.route or "none"}
    TURNS.add(1, labels)
    TOKENS.add(turn.usage.input_tokens, {"type": "input"})
    TOKENS.add(turn.usage.output_tokens, {"type": "output"})
    TOKENS.add(turn.usage.reasoning_tokens, {"type": "reasoning"})
    COST.add(float(cost_usd), labels)
    DURATION.record(turn.latency_ms, labels)
    if not turn.guard.allowed:
        BLOCKED.add(1, {"layer": turn.guard.layer, "reason": turn.guard.reason})
    if "order_total" in turn.trace and not order_is_consistent(turn.trace, catalog):
        MISMATCH.add(1)
        logger.error("order total mismatch", extra={"order": turn.trace.get("order")})


def order_is_consistent(trace: dict[str, Any], catalog: Catalog) -> bool:
    """Recompute the bill from the catalogue, independently of the pricing code."""
    total = Decimal("0")
    for line in trace.get("order", []):
        product = catalog.get(line["product_id"])
        if product is None or Decimal(line["unit_price"]) != product.price:
            return False
        total += product.price * line["quantity"]
    return total == Decimal(trace["order_total"])
