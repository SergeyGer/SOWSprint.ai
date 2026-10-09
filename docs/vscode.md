# Running SOWSprint.ai in VS Code

Everything below is verified against this repository as it stands. Where a step can
fail, the failure mode and its fix are stated, because most of the time lost on a
project like this is spent on configuration rather than code.

---

## 1. Prerequisites

| Requirement | Why | Check |
| :-- | :-- | :-- |
| Python 3.11 or 3.12 | 3.12 is the development interpreter, 3.11 the production image | `python3 --version` |
| Docker + Compose v2 | Qdrant runs in a container; the app can too | `docker compose version` |
| VS Code with the Python extension | — | install `ms-python.python` |

Open the **folder** (`File → Open Folder…`), not a single file — the workspace settings
and launch configurations only apply to a folder.

VS Code will offer the recommended extensions from `.vscode/extensions.json`. Accept
them; `charliermarsh.ruff` and `ms-python.debugpy` are the two that matter.

---

## 2. Create the environment

Two ways. The task is faster and reproducible:

**Command Palette** (`Ctrl/Cmd+Shift+P`) → **Tasks: Run Task** → **Setup: create venv and install**

Or in a terminal:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e . --no-deps      # src-layout, without re-resolving deps
```

Then select the interpreter: `Ctrl/Cmd+Shift+P` → **Python: Select Interpreter** →
`./.venv/bin/python`. `.vscode/settings.json` already points at it, so this is usually
automatic.

> **`pip install -e .` matters.** The package uses a `src/` layout. Without the editable
> install, `import sowsprint` fails outside pytest, and the debugger cannot resolve
> breakpoints inside the package. The `PYTHONPATH` in the launch configurations is a
> belt-and-braces backup for the same problem.

Verify:

```bash
.venv/bin/python -c "import sowsprint; print(sowsprint.__file__)"
.venv/bin/python -m pytest tests -o addopts= -q      # 461 tests, ~20 s, no credentials needed
```

The suite is deliberately credential-free: every subsystem falls back to a local
adapter, so a revoked API key cannot break it and CI needs no secrets.

---

## 3. Start the vector store

The default configuration uses Qdrant. Start just that container and keep the app on
the host, where the debugger works properly:

**Tasks: Run Task** → **Docker: start Qdrant only**

```bash
docker compose up -d sowsprint-qdrant
curl -s localhost:6333/healthz        # prints: healthz check passed
```

Then load the corpus:

**Tasks: Run Task** → **Corpus: ingest into Qdrant**

```bash
.venv/bin/python -m sowsprint.rag.ingest
# corpus entries : 69
# chunks         : 141   (EU 70 / US 71)
```

> If you would rather not run Docker at all, set `SOWSPRINT_VECTOR_BACKEND=memory` in
> `.env`. Retrieval then works in-process with identical filtering semantics — the
> in-memory store mirrors the Qdrant filter, and a test asserts the two agree.

---

## 4. Run the application

Press **F5** with **Run the app (Chainlit, debug)** selected. The UI opens at
<http://127.0.0.1:8000> with the debugger attached — breakpoints work inside the graph
nodes, the retriever and the connectors.

Other configurations in `.vscode/launch.json`:

| Configuration | Use |
| :-- | :-- |
| Run the app (auto-reload) | Reloads on save. Convenient, but restarts the process constantly |
| Probe all cloud services | The fastest answer to "do my credentials work?" |
| End-to-end demo (offline engine) | A full engagement, no credentials, no network |
| End-to-end demo (live models) | The same, against real models (~$0.05–0.12) |
| Pytest: current file | Set a breakpoint in a test and debug it |
| Attach to Docker container | Debug the code actually serving your phone |

### Sign in

Authentication is **on by default**. Without an account the app refuses to start rather
than serving everyone, so create one first:

**Tasks: Run Task** → **Auth: create a user** (enter a username, then a password at the
prompt — not on the command line, where it lands in shell history)

Paste the printed JSON into `SOWSPRINT_AUTH_USERS` in `.env`, and set a cookie secret:

```bash
.venv/bin/python -m chainlit.cli create-secret    # or: chainlit create-secret
```

put it in `CHAINLIT_AUTH_SECRET`, and restart.

To run an intentionally open deployment for a quick local look, set
`SOWSPRINT_AUTH_ENABLED=false`. The capability panel in the UI will say
`auth  DISABLED (open access)` so you cannot forget.

---

## 5. Testing the cloud services

This is the part worth doing deliberately, because a misconfigured credential usually
surfaces as an opaque failure several steps into an engagement rather than at startup.

### One command, every service

**F5** → **Probe all cloud services**, or:

```bash
.venv/bin/python scripts/probe_providers.py
```

Each probe makes the smallest real request that proves the credential, the endpoint and
the model id are correct, and reports latency and cost. Probes are independent, so one
failure does not stop the rest.

```
  ✅ anthropic       [1143 ms]  $0.000004
       claude-haiku-5-5 replied 'ready' · in=18 out=4
  ✅ openai          [1440 ms]  $0.000116
       gpt-5.1 replied 'ready' · in=13 out=10 cached=0
  ⏭️  embeddings
       provider is 'hash': local feature hashing, no cloud call, no cost
  ✅ transcription   [899 ms]
       provider=openai accepted a 1 s silent WAV
  ✅ qdrant          [101 ms]
       collection 'sowsprint_legal' holds 141 vectors
  ✅ retrieval       [1512 ms]
       3 passage(s), regimes=['EU'], best=f64d3a28c051 (EU)
  ✅ jira            [182 ms]
       signed in as <you> · mode=dry-run (dry-run: no writes)
  ✅ notion          [2285 ms]
       integration '<name>' can write to the parent page · mode=dry-run

  7 working · 0 failing · 1 not configured
  total spend for this probe: $0.000120
```

Useful flags:

```bash
.venv/bin/python scripts/probe_providers.py --only anthropic,openai   # a subset
.venv/bin/python scripts/probe_providers.py --skip-retrieval          # skip the index
.venv/bin/python scripts/probe_providers.py --json                    # machine-readable
```

### What each service actually needs

**Anthropic** (the drafting agent) — `SOWSPRINT_ANTHROPIC_API_KEY`. Key from
<https://console.anthropic.com/settings/keys>. The configured model is
`claude-haiku-5-5`; the app retries without `temperature` on models that reject it, so a
`temperature is deprecated` message in a raw SDK call is harmless.

**OpenAI** (the Judge) — `SOWSPRINT_OPENAI_API_KEY`. Two traps:

- **`gpt-5-mini` requires organisation verification** on some accounts and returns
  `404 … must be verified`. `gpt-5.1`, `gpt-4.1-mini` and `o4-mini` do not. The probe
  prints this specific remedy when it sees it.
- The judge should be a **different vendor** from the drafter. A model auditing its own
  output is measurably worse at catching its own hallucinations. That is why
  `SOWSPRINT_CRITIC_PROVIDER=openai` exists alongside an Anthropic drafter; setting a
  cross-vendor model id *without* it posts the id to the wrong API.

**Jira** — `SOWSPRINT_JIRA_BASE_URL`, `SOWSPRINT_JIRA_EMAIL`, `SOWSPRINT_JIRA_API_TOKEN`.
The email must be the **Atlassian account address** you sign in with, which is often not
the company domain. A 401 with a correct-looking token almost always means this.
`SOWSPRINT_JIRA_PROJECT_KEY` exists in settings but is unused — do not fill it in.

**Notion** — `SOWSPRINT_NOTION_API_KEY`, `SOWSPRINT_NOTION_PARENT_PAGE_ID`. A valid key
still returns **404** until the parent page is shared with the integration: open the page
in Notion, `•••` → **Connections** → connect it. The probe reports exactly this when it
sees a 404.

**Whisper** — voice input uses OpenAI by default. The probe uploads one second of
silence, which is enough to prove the endpoint, key and request shape without paying for
a real transcription.

**Qdrant** — no key needed locally. A Qdrant Cloud cluster needs
`SOWSPRINT_QDRANT_API_KEY` and a `SOWSPRINT_QDRANT_URL` pointing at it.

### Going live with the integrations

Integrations start in **dry-run**: payloads are validated and shown in the UI, but
nothing is sent. To actually write:

```bash
SOWSPRINT_DRY_RUN_INTEGRATIONS=false
```

The approval gate lists every planned call before it happens, which is the moment to
check that the workspace, project key and page look right. Test against a throwaway Jira
site and an empty Notion page first.

### Getting the backlog into Jira

The application creates the project, epics and stories itself through the API — flip
`SOWSPRINT_DRY_RUN_INTEGRATIONS=false` and approve at the gate. The generated CSV is a
fallback for importing by hand, or for another tool.

If you do import the CSV manually, convert it first:

```bash
make jira-csv     # writes <run>/jira_backlog-import.csv
```

Three columns of the raw export break Jira's manual importer, and the converter removes
them: `Issue Key` (Jira reads a mapped key as "update this issue", so every row fails),
`Story Points` and `Epic Link` (custom fields this tenant does not have), and
`Acceptance Criteria` (folded into the description rather than dropped).

Then in Jira: **Settings (⚙) → System → Import and Export → External System Import →
CSV**, choose the project, and map each column. Anything left unmapped is ignored rather
than fatal.

**The connector cannot attach files.** Epics and stories are text; uploading a generated
PDF as an issue attachment is a manual step, or a small addition to
`JiraConnector` (Jira's `/rest/api/3/issue/{key}/attachments` endpoint).

**Two known caveats, stated because they will bite:**

- The Jira connector creates a **company-managed** project from the Scrum template,
  which requires Jira Software (not just Work Management) and permission to create
  projects.
- Story Points are written to whichever field the tenant actually has, discovered at
  runtime. If the tenant has no such field the estimate is dropped rather than rejecting
  the whole issue — this account has no Story Points field at all.

---

## 6. Testing from a phone

The stack binds `0.0.0.0`, so a phone on the same Wi-Fi reaches it directly.

**Tasks: Run Task** → **Show the LAN address for phone testing**

```
http://192.168.x.x:8000
```

Voice capture needs a secure context in most mobile browsers: `localhost` counts, a bare
LAN IP over HTTP generally does not, so the microphone may be blocked on the phone while
working perfectly in the desktop browser. Use `ngrok http 8000` or a self-signed
certificate if you need voice on the phone specifically.

---

## 7. Debugging tips that are specific to this codebase

**The offline engine is the best debug target.** `SOWSPRINT_LLM_PROVIDER=mock` gives a
deterministic run: same input, same output, no network, no cost. Debug the *pipeline*
there and the *prompts* against the live models, rather than doing both at once.

**Breakpoints in graph nodes work** because the graph is compiled with plain Python
callables. Set one in `src/sowsprint/agents/nodes.py`, run the app, send a brief.

**Cost is instrumented per call.** If something looks expensive, the dashboard in the
transcript breaks it down by agent, and `llm.completed` log lines carry the token counts.

**Logs are structured.** `SOWSPRINT_LOG_JSON=false` makes them readable in the terminal
while still keeping the event names, which are greppable — for example
`auth.failed`, `jira.story_points_field_absent`, `anthropic.prompt_cache`.

**A failing container usually means a stale image.** The Dockerfile copies `src/` into
the image rather than bind-mounting it, so **source edits need `docker compose build`**,
not just `up -d`. This has caused real confusion: a fix that works locally appears not
to work in the container.

---

## 8. Common failures and what they actually mean

| Symptom | Cause | Fix |
| :-- | :-- | :-- |
| Blank page, title loads, no error | Browser locale is POSIX-flavoured (`en-US@posix`); Chainlit's own server rejects it with HTTP 422 | Launch the browser with `--lang=en-US`, or fix the host's `LANG`. Documented in `.chainlit/config.toml` |
| `ValueError: You must provide a JWT secret` | Authentication enabled without `CHAINLIT_AUTH_SECRET` | `chainlit create-secret` |
| Every login fails with the correct password | Digest in the dollar-separated form; Docker Compose interpolates `$` as variables | Regenerate with `python -m sowsprint.security.passwd` — the tool emits the dot form |
| `404 … must be verified` from OpenAI | `gpt-5-mini` needs organisation verification | Verify the org, or use `gpt-5.1` / `gpt-4.1-mini` / `o4-mini` |
| Jira 401 with a valid-looking token | The email is not the Atlassian **account** address | Check <https://id.atlassian.com/manage-profile/security> |
| Notion 404 with a valid key | The page is not shared with the integration | `•••` → Connections on the page |
| Edits do not appear in the container | The image bakes in `src/`; no bind mount | `docker compose up -d --build` |
| `docker compose exec` shows old values | `exec` runs in the *existing* container, which does not re-read `.env` | `docker compose up -d --force-recreate` |
| Port 8000 already in use | Another service holds it | Change `SOWSPRINT_HOST_PORT`, or stop the other service |
