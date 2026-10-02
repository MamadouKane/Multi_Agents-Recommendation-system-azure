# Evaluation report

2026-10-01T15:49:33Z, 78 s, model `gpt-5.4-mini`, suites: routing, guard, rag, orders, recommendations.

| Metric | Value | Threshold | Target | Blocking | Result |
|---|---|---|---|---|---|
| router_accuracy | 1.000 | >= 0.85 | 0.92 | yes | pass |
| guard_recall_unsafe | 1.000 | >= 0.95 | 0.98 | yes | pass |
| guard_false_positive_rate | 0.000 | <= 0.05 | 0.02 | yes | pass |
| rag_recall_at_3 | 1.000 | >= 0.85 | 0.95 | yes | pass |
| rag_groundedness_pass_rate | 1.000 | >= 0.9 | 0.95 | yes | pass |
| rag_relevance_pass_rate | 1.000 | >= 0.9 | 0.95 | yes | pass |
| rag_groundedness | 4.750 | >= 4.0 | 4.5 | no | pass |
| rag_relevance | 4.000 | >= 4.0 | 4.5 | no | pass |
| order_exact_match | 1.000 | >= 0.9 | 0.95 | yes | pass |
| order_total_error | 0 | == 0 |  | yes | pass |
| menu_hallucination | 0 | == 0 |  | yes | pass |
| system_latency_p95_ms | 2247.000 | <= 5000 | 3000 | yes | pass |
| cost_per_conversation_usd | 0.002 | <= 0.01 | 0.005 | yes | pass |
| rag_correct_abstention | 1.000 | >= 0.8 |  | no | pass |
| rag_false_abstention | 0.000 | <= 0.1 |  | no | pass |
| order_status_match | 1.000 | >= 0.9 |  | no | pass |
| recommendation_min_items | 3 | >= 3 |  | no | pass |

## Other measurements

- `cost_per_conversation_max_usd`: 0.00609
- `guard_blocking_layers`: {'content_safety': 4, 'scope': 6}
- `guard_errors`: 0
- `order_billed_turns_checked`: 41
- `orders_errors`: 0
- `rag_errors`: 0
- `rag_gate_max_unanswerable_score`: 2.552
- `rag_gate_min_answerable_score`: 1.85
- `rag_judged_answers`: 20
- `recommendation_errors`: 0
- `recommendation_max_items`: 5
- `router_accuracy_by_kind`: {'ambiguous': 1.0, 'clear': 1.0, 'follow_up': 1.0}
- `router_confusion`: {'details->details': 10, 'order->order': 10, 'recommendation->recommendation': 10}
- `routing_errors`: 0
- `system_latency_p50_ms`: 1947.0
- `unknown_prices`: []
- `unknown_product_ids`: []
