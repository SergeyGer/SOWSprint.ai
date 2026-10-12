# Contributing

The conventions here are short because each one exists for a specific reason, and the
reason is written down. Rules without reasons get ignored the first time they are
inconvenient.

---

## Getting set up

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e . --no-deps      # src layout; without this `import sowsprint` fails
.venv/bin/python -m pytest tests -o addopts= -q
```

**No credentials are needed.** Every subsystem resolves to a local adapter, so the suite
runs identically on a laptop, in CI and inside the container, and a revoked API key cannot
turn the build red. Please keep it that way: a test that needs a key will be rejected.

Running the application and its services is covered in
[**docs/vscode.md**](docs/vscode.md). There is also a zero-install demo in
[`demo/streamlit_app.py`](demo/streamlit_app.py) that runs the offline engine in a
browser — useful for reviewing a change without setting anything up.

---

## Before opening a pull request

```bash
.venv/bin/python -m pytest tests -o addopts= -q     # 483 tests, ~20s
.venv/bin/python -m ruff check src tests app.py scripts
.venv/bin/python scripts/check_docs.py              # every referenced asset exists and is tracked
.venv/bin/python scripts/check_wiki.py              # sidebar matches the pages
```

All four run in CI. Running them locally is faster than reading a failure notification.

---

## Keeping the wiki honest

**The rule: a pull request that changes behaviour updates the page that documents that
behaviour.**

This is not bureaucracy. The failure mode it prevents is specific: documentation written
once and trusted for a year, describing a system that no longer exists. A reader cannot
tell the difference between a page that is current and one that was current in March.

The wiki content lives in `docs/wiki/` **inside this repository**, and CI copies it to the
GitHub wiki on merge. That arrangement is the whole point: a GitHub wiki is a separate git
repository, so a change to it cannot appear in a pull request. Documentation that cannot be
reviewed is documentation that drifts.

### Which page owns what

| Changed | Update |
| :-- | :-- |
| The agent graph, a node, the repair loop | [`docs/wiki/Agent-Pipeline.md`](docs/wiki/Agent-Pipeline.md) |
| Retrieval, the corpus, jurisdiction filtering | [`docs/wiki/Compliance-RAG.md`](docs/wiki/Compliance-RAG.md) |
| The audit, severity calibration, a guard | [`docs/wiki/Quality-Assurance.md`](docs/wiki/Quality-Assurance.md) |
| Cost, pricing, budgets, telemetry | [`docs/wiki/Cost-Observability.md`](docs/wiki/Cost-Observability.md) |
| Jira, Notion, dry-run, provisioning | [`docs/wiki/Integrations.md`](docs/wiki/Integrations.md) |
| Authentication, secrets, threat model | [`docs/wiki/Security.md`](docs/wiki/Security.md) |
| Deployment, health checks, failure modes | [`docs/wiki/Operations.md`](docs/wiki/Operations.md) |
| A new architectural decision | A new file in [`docs/adr/`](docs/adr/), plus [`docs/wiki/Decisions.md`](docs/wiki/Decisions.md) |
| What is planned or deliberately not planned | [`docs/wiki/Roadmap.md`](docs/wiki/Roadmap.md) |

Adding a page means linking it from `docs/wiki/_Sidebar.md`; `check_wiki.py` fails the
build otherwise, because a page nobody links to is a page nobody reads.

### Diagrams

Use **Mermaid**, not screenshots, for anything structural. A Mermaid diagram is text: it
appears in the diff, it can be reviewed, and it does not silently contradict the code six
months later. Screenshots are for the product's appearance, where a real render is the
point.

---

## Architecture decisions

A decision that is expensive to reverse gets a record in [`docs/adr/`](docs/adr/), in the
standard form:

> **Context** → **Decision** → **Consequences** → **Alternatives considered**

The consequences section is expected to contain real costs, not a restatement of the
benefits. An ADR with no downside means the analysis stopped early, and the next person to
touch that code will rediscover the downside the hard way.

---

## Things that have bitten before

Each of these caused a real incident in this repository. The PR template asks about them
because a checklist item is cheaper than the incident was.

**Never log a credential, or part of one.** Printing the last four characters of an API key
counts as logging it. CodeQL flags this and the finding is correct.

**`.env` values must survive Docker Compose.** Compose interpolates `$`, so a digest in the
conventional `scrypt$16384$8$1$…` form reaches the container as `scrypt`. Every login then
fails *with a correct password*. Secrets stored in `.env` use a dot separator for this
reason, and there is a test asserting no `$` appears in a digest.

**An empty setting is not the same as an absent one.** `OpenAI(base_url="")` produces a
client whose every request fails with `UnsupportedProtocol`, surfaced as a bare
`APIConnectionError: Connection error` — a network fault that is not one. This cost days.
When a setting is optional, omit the argument; do not pass an empty string.

**Never rewrite `.env` with a pattern match.** A section-rewriting regex once matched a
larger span than intended and emptied ten credential fields, silently. Use
`scripts/setenv.py`, which replaces exactly one line and refuses to write when a change
would empty a credential or when a key appears twice.

**Directories that could be empty are created, not copied.** Git does not track empty
directories, so `COPY data/corpus/` works on the machine where the directory exists and
fails on every fresh clone. CI caught this one on its first run.

**Test an artifact by running it.** The published image aborted at boot with a JWT-secret
error that no unit test saw, because Chainlit decides whether it needs a secret from
whether an auth callback is *registered* — not from whether authentication is enabled.
The image workflow now runs what it publishes.

---

## Style

- **Comments explain why, not what.** The code says what it does. A comment earns its place
  by recording the reason, the alternative that was rejected, or the incident that
  motivated the line.
- **Tests document behaviour.** A test name should read as a claim about the system, and a
  regression test should say what went wrong before.
- **Honest limitations belong in the documentation.** Every page in this repository has a
  section on what does not work. That is deliberate: a reader deciding whether to trust
  something needs the negative results as much as the positive ones.

---

## Licence

By contributing you agree that your contributions are licensed under the
[MIT Licence](LICENSE).
