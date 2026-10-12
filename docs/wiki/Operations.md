# Deployment & operations

## Two containers, one command

```mermaid
flowchart LR
    subgraph host["Host"]
        P8000["0.0.0.0:8000"]
        P6333["0.0.0.0:6333"]
    end
    subgraph compose["docker compose"]
        APP["sowsprint-app<br/>Chainlit + graph<br/>non-root uid 10001"]
        QD["sowsprint-qdrant<br/>named vectors"]
    end
    subgraph volumes["Persistent"]
        V1[("artifact_data<br/>deliverables")]
        V2[("./data/corpus<br/>drop-in corpora")]
    end

    P8000 --> APP
    P6333 --> QD
    APP --> QD
    APP --> V1
    APP --> V2

    style APP fill:#065f46,stroke:#10b981,color:#d1fae5
    style QD fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
```

```bash
docker compose up -d
```

The app binds `0.0.0.0` deliberately, so a phone on the same Wi-Fi can dictate a brief.
Qdrant is exposed so the index can be inspected and rebuilt without entering the container.

## First run

```mermaid
flowchart TB
    S1["1. Create an account<br/>python -m sowsprint.security.passwd"] --> S2["2. Set CHAINLIT_AUTH_SECRET"]
    S2 --> S3["3. docker compose up -d"]
    S3 --> S4["4. Ingest the corpus<br/>make ingest"]
    S4 --> S5["5. Verify<br/>scripts/probe_providers.py"]
    S5 --> S6{"All green?"}
    S6 -->|yes| S7["Ready"]
    S6 -->|no| S8["Read the remedy<br/>each probe prints"]

    style S7 fill:#065f46,stroke:#10b981,color:#d1fae5
    style S8 fill:#78350f,stroke:#f59e0b,color:#fef3c7
```

`scripts/probe_providers.py` makes one minimal real call per configured service and prints
the **specific remedy** for the failures this project has actually hit — the OpenAI
organisation-verification 404, the Jira account-email 401, the Notion page-not-shared 404.
A full pass costs about $0.0001.

## Container behaviour worth knowing

These caused real confusion during development and are worth internalising:

| Behaviour | Consequence |
| :-- | :-- |
| The image **bakes in** `src/`; it is not a bind mount | Source edits need `docker compose build`, not just `up -d`. A fix that works locally appears not to work in the container |
| `docker compose exec` runs in the **existing** container | It does not re-read `.env`. An edited credential appears to have no effect |
| Readiness warms the index | The container reports healthy only when it can actually draft, not merely answer HTTP |
| `data/corpus` is mounted read-only | Drop-in corpora need no rebuild |

`docker compose up -d --force-recreate` is the correct response to any `.env` change.

## Health and readiness

```bash
docker compose exec sowsprint-app python scripts/healthcheck.py --deep
```

```
[OK ] liveness: http 200
[OK ] readiness: {"store": "qdrant", "chunks": 615, "bm25_fitted": true,
                  "eu_probe": "6a0638b72bca", "us_probe": "defea8be8215"}
```

The deep probe goes further than "is the port open": it asserts the corpus is indexed, the
BM25 statistics are fitted, and **both jurisdictions return distinct results**. That last
check is what catches a filter that has silently stopped working.

## Rebuilding the index

```bash
make reingest          # required after changing the embedding provider or dimension
```

An index built with one embedding model is meaningless with another. The dimension is
enforced at collection creation, so a mismatch fails loudly rather than returning nonsense.

## Observability

Structured logs with stable event names, greppable in production:

```bash
docker compose logs sowsprint-app | grep -oE '"event": "[a-z._]+"' | sort | uniq -c | sort -rn
```

Events worth alerting on:

| Event | Meaning |
| :-- | :-- |
| `auth.failed` | Repeated failures may be an attack or a misconfigured client |
| `llm.degraded_to_offline` | A cloud provider failed; quality has dropped |
| `critic.degraded_audit` | The audit is not a real audit — the verdict is unreliable |
| `llm.output_truncated` | A budget is too small for a node |
| `jira.story_points_field_absent` | Tenant adaptation engaged; estimates are being dropped |

## Backup and recovery

The state that matters is the artifact volume and the corpus. Neither is large.

```bash
docker run --rm -v sowsprint_artifact_data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/artifacts-$(date +%F).tar.gz -C /data .
```

Qdrant can be rebuilt from the corpus at any time, so it does not need backing up.

## Failure modes and their responses

```mermaid
flowchart TB
    F1["Blank page, no error"] --> R1["POSIX-flavoured browser locale.<br/>Launch with --lang=en-US"]
    F2["Login fails with the<br/>correct password"] --> R2["Digest in dollar form;<br/>Compose interpolated it. Re-generate"]
    F3["ValueError: JWT secret"] --> R3["Authentication on<br/>without CHAINLIT_AUTH_SECRET"]
    F4["APIConnectionError<br/>on every call"] --> R4["base_url is an empty string.<br/>The SDK needs it absent, not blank"]
    F5["Edits absent in container"] --> R5["The image bakes in src/;<br/>rebuild"]
    F6["OpenAI 404 must be verified"] --> R6["gpt-5-mini needs organisation<br/>verification. Use gpt-5.1"]
    F7["Jira 400 projectLead"] --> R7["Cloud v3 needs leadAccountId,<br/>not a lead email"]

    style R1 fill:#065f46,stroke:#10b981,color:#d1fae5
    style R2 fill:#065f46,stroke:#10b981,color:#d1fae5
    style R4 fill:#065f46,stroke:#10b981,color:#d1fae5
```

Every one of those was hit and diagnosed during this project's development. The failure
table exists because the error messages alone do not lead to the fix.

→ [Security](Security.md) · [Testing](Testing.md)
