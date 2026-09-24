"""Day 1 acceptance check for Azure OpenAI.

Two questions are answered here:

1. Does a keyless call work? The account has `disableLocalAuth: true`, so the only way in is an
   Entra ID token obtained by `DefaultAzureCredential` (objective OT2).
2. Which request parameters does the chat model accept? The GPT-5 family is made of reasoning
   models, and the prototype used `temperature=0, top_p=0.8`. ADR-007 leaves this open on purpose,
   because guessing would mean writing agents against an API that rejects them.

Run:
    export AZURE_OPENAI_ENDPOINT=$(az cognitiveservices account show \
        -g rg-coffeeai-dev -n aif-coffeeai-dev-frc --query properties.endpoint -o tsv)
    .venv/bin/python scripts/smoke_ai.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from azure.identity import DefaultAzureCredential
from openai import OpenAI

SCOPE = "https://cognitiveservices.azure.com/.default"
ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.4-mini")
EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

PROMPT = [
    {"role": "system", "content": "You are a coffee shop assistant. Answer in one short sentence."},
    {"role": "user", "content": "What is a cappuccino?"},
]


@dataclass
class Probe:
    """One request variant, and what the service replied."""

    name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    detail: str = ""
    latency_ms: int = 0
    tokens: str = ""


def build_client() -> OpenAI:
    """Azure OpenAI through the v1 endpoint, authenticated with an Entra ID token.

    No API key exists on this account. The token is fetched from the local `az login` session
    when running on a laptop, and from the managed identity once deployed: same code, same call.
    """
    if not ENDPOINT:
        sys.exit("AZURE_OPENAI_ENDPOINT is not set. See the docstring for the az command.")

    token = DefaultAzureCredential().get_token(SCOPE)
    print(f"Token acquired, expires in {int(token.expires_on - time.time())} s")
    return OpenAI(base_url=f"{ENDPOINT.rstrip('/')}/openai/v1/", api_key=token.token)


def run_probe(client: OpenAI, probe: Probe) -> Probe:
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=CHAT_DEPLOYMENT, messages=PROMPT, **probe.kwargs
        )
    except Exception as exc:  # the error message is the result we are after
        probe.ok = False
        probe.detail = str(exc).split("\n")[0][:150]
    else:
        probe.ok = True
        probe.detail = (response.choices[0].message.content or "").strip()[:60]
        usage = response.usage
        if usage:
            reasoning = getattr(
                getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
            )
            probe.tokens = f"in {usage.prompt_tokens} / out {usage.completion_tokens}" + (
                f" (reasoning {reasoning})" if reasoning else ""
            )
    probe.latency_ms = int((time.perf_counter() - started) * 1000)
    return probe


def check_determinism(client: OpenAI, runs: int = int(os.environ.get("SMOKE_RUNS", "4"))) -> None:
    """Accepting a parameter is not the same as honouring it.

    The service accepts `temperature=0` on this model, but that only means the request is valid.
    Sending the same prompt several times is the only way to know whether the answer is stable,
    which decides how the day 5 evaluation has to be read: a single run, or several with a variance.
    """
    print("\nDeterminism check, same prompt repeated")
    for label_text, kwargs in [
        ("temperature=0", {"temperature": 0}),
        ("temperature=0 + seed", {"temperature": 0, "seed": 42}),
        ("defaults", {}),
    ]:
        answers = []
        for _ in range(runs):
            response = client.chat.completions.create(
                model=CHAT_DEPLOYMENT, messages=PROMPT, **kwargs
            )
            answers.append((response.choices[0].message.content or "").strip())
        distinct = len(set(answers))
        verdict = "identical" if distinct == 1 else f"{distinct} different answers"
        print(f"  {label_text:24} {runs} runs: {verdict}")
        if distinct > 1:
            for answer in sorted(set(answers)):
                print(f"      - {answer[:90]}")

    check_decision_stability(client, runs)


def check_decision_stability(client: OpenAI, runs: int) -> None:
    """Free text wording is allowed to vary. Routing decisions are not.

    Every day 5 threshold reads a structured field (route, product ids, guard verdict), never the
    prose around it. This probe therefore measures what the evaluation actually gates on, using a
    deliberately ambiguous message: it asks for a recommendation and starts an order at once.
    """
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "route_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "route": {"type": "string", "enum": ["details", "order", "recommendation"]}
                },
                "required": ["route"],
                "additionalProperties": False,
            },
        },
    }
    system_prompt = "Route the user message to one agent. Answer with the schema."
    user_prompt = "I want something sweet with my latte, surprise me, and add it."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    print("\nDecision stability, ambiguous routing message")
    for label_text, kwargs in [
        ("temperature=0", {"temperature": 0}),
        ("reasoning_effort=minimal", {"reasoning_effort": "minimal"}),
        ("minimal + temperature=0", {"reasoning_effort": "minimal", "temperature": 0}),
        ("defaults", {}),
    ]:
        decisions = []
        error = ""
        for _ in range(runs):
            try:
                response = client.chat.completions.create(
                    model=CHAT_DEPLOYMENT, messages=messages, response_format=schema, **kwargs
                )
            except Exception as exc:  # a rejected combination is a result, not a crash
                error = str(exc).split("\n")[0][:120]
                break
            decisions.append((response.choices[0].message.content or "").strip())
        if error:
            print(f"  {label_text:24} rejected: {error}")
            continue
        counts = {d: decisions.count(d) for d in sorted(set(decisions))}
        stable = "stable" if len(counts) == 1 else "UNSTABLE"
        print(f"  {label_text:24} {runs} runs: {stable} {counts}")


def main() -> int:
    client = build_client()

    probes = [
        Probe("baseline, no sampling parameter"),
        Probe("temperature=0", {"temperature": 0}),
        Probe("temperature=1", {"temperature": 1}),
        Probe("top_p=0.8", {"top_p": 0.8}),
        Probe("max_tokens=50 (legacy name)", {"max_tokens": 50}),
        Probe("max_completion_tokens=200", {"max_completion_tokens": 200}),
        Probe("reasoning_effort=minimal", {"reasoning_effort": "minimal"}),
        Probe("reasoning_effort=low", {"reasoning_effort": "low"}),
        Probe("seed=42", {"seed": 42}),
        Probe(
            "structured outputs, strict schema",
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "route_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "route": {
                                    "type": "string",
                                    "enum": ["details", "order", "recommendation"],
                                }
                            },
                            "required": ["route"],
                            "additionalProperties": False,
                        },
                    },
                }
            },
        ),
    ]

    print(f"\nChat deployment: {CHAT_DEPLOYMENT}\n")
    header = f"{'probe':38} {'result':7} {'ms':>6}  detail"
    print(header)
    print("-" * len(header))

    results = [run_probe(client, probe) for probe in probes]
    for probe in results:
        status = "ok" if probe.ok else "FAILED"
        extra = probe.tokens or probe.detail
        print(f"{probe.name:38} {status:7} {probe.latency_ms:>6}  {extra}")

    # Embeddings, used by the day 2 index build.
    started = time.perf_counter()
    try:
        embedding = client.embeddings.create(
            model=EMBEDDING_DEPLOYMENT, input=["cappuccino with steamed milk"]
        )
    except Exception as exc:
        print(f"\nembeddings FAILED: {str(exc).split(chr(10))[0][:150]}")
        return 1
    dims = len(embedding.data[0].embedding)
    print(f"\nembeddings ok, {dims} dimensions, {int((time.perf_counter() - started) * 1000)} ms")

    check_determinism(client)

    accepted = [p.name for p in results if p.ok]
    rejected = [(p.name, p.detail) for p in results if not p.ok]
    print(f"\nAccepted: {len(accepted)} / {len(results)}")
    for name, detail in rejected:
        print(f"  rejected: {name}: {detail}")

    # The baseline call is the only hard requirement: everything else is discovery.
    return 0 if results[0].ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
