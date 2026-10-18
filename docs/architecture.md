# Architecture

Diagrams stay at service level. Why each choice was made is recorded in an architecture decision record (ADR), named under each section.

Region: **France Central**. Environments: `dev`, `stg`. Naming and tagging: ADR-006.

---

## 1. Target architecture

![Cloud architecture](assets/archi-simplified-v2.svg)

Service icons are Microsoft's official Azure architecture icons, used unmodified.

<details>
<summary>The same view in Mermaid</summary>

```mermaid
flowchart LR
    user(["Customer"]) -->|"REST, JSON"| api["FASTAPI<br/>CONTAINER"]
    api -->|"Prompt Shields"| safety["CONTENT<br/>SAFETY"]
    api -->|"gpt-5.4-mini"| llm["AZURE<br/>OPENAI"]
    api -->|"vector + reranker gate"| search["AI SEARCH"]
    api -->|"catalogue, conversations"| cosmos["COSMOS DB"]
    api -->|"OpenTelemetry"| appi["APP<br/>INSIGHTS"]

    sales[("SALES DATA")] -->|"Apriori, MLflow"| aml["AZURE ML"]
    aml -->|"model artefacts"| blob["BLOB<br/>STORAGE"]
    blob -->|"loaded at startup"| api

    gh["GITHUB<br/>ACTIONS"] -->|"Docker build"| acr["CONTAINER<br/>REGISTRY"]
    acr -->|"revision, Bicep, OIDC"| aca["CONTAINER<br/>APPS"]
    aca -.runs the image.-> api

    classDef box fill:#c9443c,stroke:#a5332c,color:#ffffff,font-weight:bold
    classDef store fill:#7a5c9e,stroke:#5f4780,color:#ffffff,font-weight:bold
    class api,safety,llm,search,cosmos,appi,aml,blob,gh,acr,aca box
    class sales store
```

</details>

---

## 2. Agent chain

One turn goes through at most one specialised agent.

```mermaid
flowchart LR
    req(["Chat turn"]) --> guard["GUARD<br/>safety + scope"]
    guard -->|refused| out(["Response"])
    guard -->|allowed| router["ROUTER"]
    router --> details["DETAILS<br/>RAG"]
    router --> order["ORDER"]
    router --> reco["RECOMMENDATION"]
    details --> out
    order --> out
    reco --> out
    order -.upsell.-> reco

    classDef box fill:#c9443c,stroke:#a5332c,color:#ffffff,font-weight:bold
    class guard,router,details,order,reco box
```

- Guard is two layers, Content Safety then an LLM scope check, inside a hand-written orchestrator: ADR-001.
- Details retrieves before answering, and stays silent when nothing is relevant: ADR-008 (vector ranking gated by the semantic reranker; it superseded the hybrid retrieval of ADR-002 after measurement).
- Order extracts items, Python owns the prices: ADR-003.
- Recommendation reads model artefacts and answers only with catalogue items: ADR-004 and ADR-009.
- Every turn is persisted, none is held in session: ADR-005.

---

## 3. An order, in sequence

The most critical path, because business objective OM4 tolerates zero billing error.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant API as Orchestrator
    participant CS as Content Safety
    participant LLM as Azure OpenAI
    participant CAT as Catalogue
    participant DB as Cosmos

    C->>API: messages + memory
    API->>CS: check the user message
    CS-->>API: safe
    API->>LLM: guard, then router
    LLM-->>API: allowed, order agent
    API->>CAT: menu rendered from the catalogue
    API->>LLM: order prompt
    LLM-->>API: items only, no prices
    API->>CAT: resolve items, read prices
    Note over API: totals computed in Python,<br/>reply written from a template
    API-->>C: answer + memory + conversation_id
    API->>DB: persist the turn (in the background)
```

Steps 8 to 10 are the point: the model returns identifiers and quantities only, and the reply is a template, so no number on a bill ever passes through the model, not even to be copied (ADR-003).

---

## 4. Data flow

What produces the data the runtime reads. None of this runs during a chat turn.

```mermaid
flowchart LR
    raw[("data/raw<br/>catalogue, images<br/>knowledge, sales")] -->|"ingest"| cosmos["COSMOS DB"]
    raw -->|"upload"| blob["BLOB<br/>STORAGE"]
    cosmos -->|"chunk + embed"| search["AI SEARCH"]
    raw -->|"Azure ML pipeline"| artefacts["MODEL<br/>ARTEFACTS"]
    artefacts --> blob

    cosmos --> api["FASTAPI<br/>ORCHESTRATOR"]
    search --> api
    blob --> api

    classDef box fill:#c9443c,stroke:#a5332c,color:#ffffff,font-weight:bold
    classDef store fill:#7a5c9e,stroke:#5f4780,color:#ffffff,font-weight:bold
    class cosmos,blob,search,artefacts,api box
    class raw store
```

Cosmos DB is the source of truth, and the search index is derived from it: ADR-004.

---

## 5. Identity

No API key anywhere. The same code path authenticates in all three places.

```mermaid
flowchart LR
    dev["LAPTOP<br/>az login"] --> entra["ENTRA ID"]
    app["CONTAINER APP<br/>managed identity"] --> entra
    ci["GITHUB ACTIONS<br/>OIDC"] --> entra
    entra -->|"RBAC roles"| azure["AZURE<br/>SERVICES"]

    classDef box fill:#c9443c,stroke:#a5332c,color:#ffffff,font-weight:bold
    class dev,app,ci,entra,azure box
```

| Principal | Authenticates with | Roles |
|---|---|---|
| - | `az login`, read by `DefaultAzureCredential` | same as the app, for local runs |
| The API | user-assigned managed identity | Cognitive Services OpenAI User and User (Content Safety), Search Index Data Reader, Cosmos DB data contributor (data plane role), Storage Blob Data Reader, Key Vault Secrets User, AcrPull, Monitoring Metrics Publisher |
| CI/CD | OIDC federated credential, no stored secret | Contributor on the resource group |

---

## 6. Delivery

```mermaid
flowchart LR
    pr["PULL REQUEST"] -->|"lint, tests, eval gate"| main["MAIN"]
    main -->|"build image"| acr["CONTAINER<br/>REGISTRY"]
    acr -->|"revision at 0 percent"| aca["CONTAINER<br/>APPS"]
    aca -->|"smoke tests pass"| live(["100 percent traffic"])
    aca -->|"smoke tests fail"| back(["Previous revision kept"])

    classDef box fill:#c9443c,stroke:#a5332c,color:#ffffff,font-weight:bold
    class pr,main,acr,aca box
```

Blue and green comes from Container Apps revisions, so no extra infrastructure is needed (OT5, ADR-011).
The evaluation gate blocks a change that degrades quality (ADR-010).

---

## 7. Traceability

One `conversation_id` links every span, metric and stored turn, which is what makes a failed conversation replayable (US6).

```
POST /chat                   conversation_id
└── chat.turn                agent, route, guard verdict, tokens
    ├── guard
    │   ├── content_safety   moderation and Prompt Shields verdict
    │   └── chat <model>     scope decision
    ├── router
    │   └── chat <model>     chosen agent
    └── agent <route>
        ├── search.query     retrieved documents (details)
        └── chat <model>     answer or order extraction
```

Model spans follow the OpenTelemetry GenAI convention (`gen_ai.*`).
Custom metrics: `chat.turns`, `chat.tokens`, `chat.cost.usd`, `chat.turn.duration`, `guard.blocked`, `order.total.mismatch`.
The last one alerts at severity 1 above zero: it means a customer was quoted the wrong amount.
