# Evaluation report

2026-10-28T10:42:49Z, 91 s, model `gpt-5.4-mini`, suites: routing, guard, rag, orders, recommendations.

| Metric | Value | Threshold | Target | Blocking | Result |
|---|---|---|---|---|---|
| router_accuracy | 1.000 | >= 0.85 | 0.92 | yes | pass |
| guard_recall_unsafe | 1.000 | >= 0.95 | 0.98 | yes | pass |
| guard_false_positive_rate | 0.000 | <= 0.05 | 0.02 | yes | pass |
| rag_recall_at_3 | 1.000 | >= 0.85 | 0.95 | yes | pass |
| rag_groundedness_pass_rate | 1.000 | >= 0.9 | 0.95 | yes | pass |
| rag_relevance_pass_rate | 0.952 | >= 0.9 | 0.95 | yes | pass |
| rag_groundedness | 4.714 | >= 4.0 | 4.5 | no | pass |
| rag_relevance | 4.000 | >= 4.0 | 4.5 | no | pass |
| order_exact_match | 0.964 | >= 0.9 | 0.95 | yes | pass |
| order_total_error | 0 | == 0 |  | yes | pass |
| menu_hallucination | 0 | == 0 |  | yes | pass |
| order_reply_violations | 0 | == 0 |  | yes | pass |
| rag_unsold_mentions | 0 | == 0 |  | yes | pass |
| system_latency_p95_ms | 2332.000 | <= 5000 | 3000 | yes | pass |
| cost_per_conversation_usd | 0.003 | <= 0.01 | 0.005 | yes | pass |
| rag_correct_abstention | 1.000 | >= 0.8 |  | no | pass |
| rag_false_abstention | 0.000 | <= 0.1 |  | no | pass |
| order_status_match | 1.000 | >= 0.9 |  | no | pass |
| recommendation_min_items | 3 | >= 3 |  | no | pass |

## Other measurements

- `cost_per_conversation_max_usd`: 0.006607999999999999
- `guard_blocking_layers`: {'content_safety': 4, 'scope': 6}
- `guard_errors`: 0
- `order_billed_turns_checked`: 46
- `orders_errors`: 0
- `rag_errors`: 0
- `rag_gate_max_unanswerable_score`: 2.368
- `rag_gate_min_answerable_score`: 1.85
- `rag_judged_answers`: 21
- `recommendation_errors`: 0
- `recommendation_max_items`: 5
- `router_accuracy_by_kind`: {'ambiguous': 1.0, 'clear': 1.0, 'follow_up': 1.0}
- `router_confusion`: {'details->details': 10, 'order->order': 10, 'recommendation->recommendation': 10}
- `routing_errors`: 0
- `system_latency_p50_ms`: 2017.0
- `unknown_prices`: []
- `unknown_product_ids`: []

## Failed cases

- rag g19: groundedness=4.0, relevance=3.0: 'Yes. For orders of more than twenty items, please order at least 24 hours in advance so we can prepare everything fresh.'
- orders o16 (ambiguous): expected {'chocolate-chip-biscotti': 2} open, got {'chocolate-chip-biscotti': 3} open
