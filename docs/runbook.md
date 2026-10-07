# Runbook

Operational guide for running, verifying and troubleshooting SOWSprint.ai.

---

## 1. Deployment topologies

| Topology | Command | When to use |
| :-- | :-- | :-- |
| **Local, no containers** | `make dev` | Fastest iteration. Vector store is in-process; corpus re-ingests per process (~200 ms). |
| **Full stack** | `make up` | The documented configuration: app + Qdrant with persistent storage. |
| **Production-ish** | `docker compose -f docker-compose.yml up -d` behind a TLS reverse proxy | See §7 for the hardening checklist. |

The application container always binds `0.0.0.0:8000`. That is a hard requirement from
the brief: an iPhone on the same Wi-Fi must reach the build at
`http://<host-lan-ip>:8000`. A `127.0.0.1` bind silently breaks mobile testing, so do
not "fix" it to loopback.

### Changing the host port

Port 8000 is commonly occupied. Set `SOWSPRINT_HOST_PORT` in `.env`:

```bash
SOWSPRINT_HOST_PORT=8123
```

The container-internal port stays 8000. `make lan-ip` prints the URL to open on a phone.

---

## 2. First-run checklist

```bash
cp .env.example .env          # optional: the stack runs with an empty .env
make up
make ps                       # both containers must show (healthy)
make health                   # liveness + readiness, including the filter probe
```

Expected `make ps` output once warm:

```
NAME               STATUS
sowsprint-app      Up (healthy)
sowsprint-qdrant   Up (healthy)
```

The app's healthcheck has a **90-second start period** because a cold Qdrant volume
requires a full corpus ingestion before readiness. If `make ps` shows `(health:
starting)` for more than two minutes, see §6.

---

## 3. Ingesting the compliance corpus

Ingestion is **lazy and self-healing**: the first retrieval against an empty collection
triggers a full ingest, and a restart against a populated Qdrant volume refits the BM25
statistics from the stored payloads rather than re-embedding.

To do it explicitly:

```bash
make corpus-stats             # 69 entries → 141 chunks (70 EU / 71 US)
make ingest                   # index into the configured backend
make reingest                 # drop and rebuild from scratch
```

To extend the corpus without touching code, drop JSON files into `data/corpus/`:

```json
[
  {
    "id": "eu-custom-dpa-clause",
    "jurisdiction": "EU",
    "source": "Customer MSA, cl. 12.4 (adapted)",
    "doc_type": "contract_clause",
    "title": "Custom processor audit right",
    "text": "…at least 150 words…",
    "tags": ["audit", "processor"],
    "risk_level": "medium"
  }
]
```

`jurisdiction` **must** be exactly `"EU"` or `"US"` — it is the metadata filter key.

---

## 4. Switching providers

No code change is required; the capability matrix at the top of the chat reflects what
each subsystem resolved to.

**Move to cloud reasoning:**

```bash
SOWSPRINT_OPENAI_API_KEY=sk-...
# or SOWSPRINT_ANTHROPIC_API_KEY=sk-ant-...
make up
```

`SOWSPRINT_LLM_PROVIDER=auto` selects the best credentialed provider. To force one:

```bash
SOWSPRINT_LLM_PROVIDER=anthropic
```

**Judge independence.** Point the Critic at a different vendor than the drafting agent.
A model auditing its own output is measurably weaker:

```bash
SOWSPRINT_REASONING_MODEL=claude-sonnet-4
SOWSPRINT_CRITIC_MODEL=gpt-4o
```

**Self-hosted inference** (the local-guardrail tier):

```bash
SOWSPRINT_LLM_PROVIDER=ollama
SOWSPRINT_OLLAMA_BASE_URL=http://host.docker.internal:11434
```

**A real cross-encoder reranker** instead of the offline surrogate:

```bash
SOWSPRINT_TEI_RERANK_URL=http://reranker:80/rerank   # BGE via text-embeddings-inference
```

**Embedding dimension is coupled to the collection.** Changing
`SOWSPRINT_EMBEDDING_MODEL` or `SOWSPRINT_EMBEDDING_DIM` requires a rebuild:

```bash
make reingest
```

Switching from the offline hasher (which respects `EMBEDDING_DIM`) to OpenAI without
re-ingesting leaves a dimension mismatch; Qdrant will reject the upsert.

---

## 5. Going live with Jira / Notion

Integrations start in **dry-run**: payloads are built and validated, then displayed,
but never transmitted. This is a deliberate review gate.

1. Run a scoping session and open the *Integration plan* step in the UI.
2. Inspect the exact JSON that would be sent (visible in the step payload).
3. Confirm the project key, issue types, parent links and story points are correct.
4. Only then:

```bash
SOWSPRINT_DRY_RUN_INTEGRATIONS=false
SOWSPRINT_JIRA_BASE_URL=https://your-tenant.atlassian.net
SOWSPRINT_JIRA_EMAIL=automation@your-tenant.com
SOWSPRINT_JIRA_API_TOKEN=...          # https://id.atlassian.com/manage-profile/security/api-tokens
SOWSPRINT_NOTION_API_KEY=secret_...
SOWSPRINT_NOTION_PARENT_PAGE_ID=...
```

Jira's Story Points field id varies per tenant. The connector writes
`customfield_10016`, which is the near-universal default; if your instance differs,
change it in `src/sowsprint/tools/jira.py::create_issue`.

The human approval gate in the UI remains in force regardless of this setting unless
the operator enables *Skip the approval gate* in chat settings.

---

## 6. Troubleshooting

### `(unhealthy)` on sowsprint-app

```bash
docker compose logs --tail=100 sowsprint-app
docker compose exec sowsprint-app python scripts/healthcheck.py --deep
```

| Symptom | Cause | Fix |
| :-- | :-- | :-- |
| `readiness: vector store empty or unreachable` | Qdrant not reachable | `docker compose logs sowsprint-qdrant`; confirm `SOWSPRINT_QDRANT_URL` |
| `readiness: jurisdiction filter is not discriminating` | Both regimes returned the same chunk | Corpus not filtered on ingest — check that every entry has `jurisdiction` of `EU`/`US` |
| `liveness: http 000` | Chainlit has not finished starting | Wait out the 90 s start period; check for a port conflict |
| Container restarts in a loop | OOM during ingestion | Raise the memory limit; the hash embedder over 141 chunks is light, OpenAI embeddings are batched |

### `Error: address already in use` on startup

The host port is taken. **This is not a container problem** — pick another:

```bash
SOWSPRINT_HOST_PORT=8123 make up
```

Inside the container the port is always 8000.

### Phone cannot reach the app

1. Confirm the bind: `docker compose port sowsprint-app 8000` should show `0.0.0.0:...`.
2. Confirm the phone is on the **same Wi-Fi** (not a guest network with client isolation).
3. Check the host firewall: `sudo ufw allow 8000/tcp`.
4. `make lan-ip` and use the address it prints — not `localhost`.

### Voice capture does nothing

The audio widget requires a **secure context** in Safari. `http://<lan-ip>` is not one,
except for `localhost`. Two options:

* Terminate TLS in front of the app (see §7) and use `https://`.
* Use Chrome on Android, or type the requirement.

With no Whisper key the transcription step returns an explicit, actionable error rather
than a silent failure — check the capability table at the top of the chat.

### Budget exhausted mid-run

Expected behaviour: the graph halts *before* the offending call and reports
`budget_exceeded`. Raise the ceiling in chat settings (slider next to the composer) or
via `SOWSPRINT_SESSION_BUDGET_USD`. Non-protected nodes automatically downgrade to the
fast model tier above 70% consumption; the Legal and Critic nodes never downgrade,
because their output is contractual.

### Costs look wrong

With the offline engine, calls are priced against a **shadow tier** and the dashboard
labels the figure `simulated`. That is intentional — it keeps the observability layer
demonstrable without credentials. Real spend appears as soon as a cloud key is present.

---

## 7. Production hardening checklist

- [ ] **Authentication.** Add `@cl.password_auth_callback` or OAuth in `app.py`, or put
      the app behind an authenticating reverse proxy. There is no auth by default.
- [ ] **TLS.** Terminate HTTPS in front of the container (Caddy, nginx, Traefik). This
      also enables the voice widget on mobile Safari.
- [ ] **Durable checkpoints.** Replace `MemorySaver` with `SqliteSaver` or
      `PostgresSaver` in `agents/runtime.py` so an in-flight session survives a restart.
- [ ] **Qdrant API key.** Set `SOWSPRINT_QDRANT_API_KEY` and stop publishing 6333/6334
      to the host.
- [ ] **Secrets management.** Move credentials out of `.env` into your platform's secret
      store; `.env` is gitignored but is still plaintext on disk.
- [ ] **Budget caps per tenant.** `SOWSPRINT_SESSION_BUDGET_USD` is per session; enforce
      an aggregate ceiling at the gateway if you expose this to third parties.
- [ ] **Log shipping.** `SOWSPRINT_LOG_JSON=true` emits one JSON object per line, ready
      for Loki/Datadog. `run_id` correlates every line of one engagement.
- [ ] **Backups.** `qdrant_storage` and `artifact_data` are the only stateful volumes.

---

## 8. Verification

```bash
make test          # 317 unit + integration + e2e tests
make verify-ui     # drives the live UI over its real socket.io protocol
make health        # deep readiness probe
```

`scripts/verify_ui.py` is the strongest single check: it connects to the running server
over the same websocket protocol the browser uses, sends a real requirement, answers the
clarification questions, approves the contract, and asserts that eleven agent steps and
seven deliverable files came back.

To run it against a container:

```bash
SOWSPRINT_HOST_PORT=8123 make up
.venv/bin/python scripts/verify_ui.py --url http://127.0.0.1:8123
```

---

## 9. Reading a failed run

Every run exposes its full trace. In order of usefulness:

1. **`run_report.json`** in `data/artifacts/<run_id>/` — scope, blueprint summary, audit,
   integration results and the cost report in one file.
2. **`quality_audit.md`** — the Critic's findings with severities and concrete
   remediation instructions.
3. **The step trace in the UI** — expand any step for timings, tokens and cost.
4. **Structured logs** — `docker compose logs sowsprint-app | grep <run_id>`.

A run that ends `failed` with `halt_reason` mentioning *"rejected by the reviewer"* is a
successful human override, not a system error: no external system was modified.
