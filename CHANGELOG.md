# Changelog

Notable changes to this project. The format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

Entries record **why** a change was made, not only what changed: a changelog that lists only
the what cannot help anyone decide whether to upgrade.

---

## 2.1.0 — 2026-11-10

Two release-blocking defects, both of the same shape: the system did something reasonable and
said nothing, and the absence of a statement was read as success.

**A dry run could write to a live tracker.** `ToolExecutor` never knew its own mode — the flag
defaulted to `True` while the adapters read settings that said live — so a "validated, nothing
sent" run created and deleted six real Jira projects and three Notion pages before anyone
looked. The executor now takes its mode explicitly and refuses to send a live call without a
publication decision on record.

**A partial batch created a second project on retry.** No record of what had already been sent
meant a retry re-created everything. Every operation now carries a key stable across attempts;
the executor reads the journal first, skips what succeeded, and reuses the saved identifier, so
a retry cannot create a second epic.

### Added

- **Publication policy** (`src/sowsprint/policy.py`) — one decision, made before anything is
  provisioned: audit passed, not degraded, approved, snapshot matches, not already completed.
  The snapshot hashes the contract, the backlog, the plan and the destinations, with volatile
  fields excluded, so approval covers an exact revision rather than "whatever is current".
- **Persistence** (`src/sowsprint/storage/`) — runs, revisions, decisions, operations and
  events in SQLite, portable to Postgres; the checkpointer is chosen from a URL. A run is
  persisted when it starts, so it is listable and reopenable before it finishes.
- **Return to a run by identifier** — `/runs` lists them newest first and `/open <run_id>`
  reopens one, including the approval card, with the actor who reopened it recorded.
- **Operation journal** (`src/sowsprint/tools/journal.py`) — one row per attempted call, with
  created / existing / skipped / uncertain / failed outcomes, per-destination counts, and a
  next action computed from them.
- **Role enforcement** (`src/sowsprint/security/roles.py`) — author, approver, admin, service.
  An author cannot approve their own run; admin is appointed.
- **Separated Jira and Notion results** — the result card reports each destination on its own
  row, with links and a next action, instead of one aggregate count that hides a failure.
- **Google Gemini** as a provider, alongside Anthropic, OpenAI and the offline engine.
- **Engine line on every result** — which providers actually answered, and how many calls each
  made. Two recordings were made of runs that looked like failing audits and were in fact a
  401: every node retried, fell back, and completed silently.
- **`demo/default_brief.md`** — the starting brief the interface offers through `/brief`. It was
  chosen by measurement, not by writing: richer and longer briefs did not raise the score, and a
  wider retrieval window scored *worse*.

### Changed

- **The approval gate cannot be bypassed.** `after_plan_tools` routed straight to provisioning
  when `auto_deploy` was set, and `after_critic` did the same when the budget ran out, so a run
  could publish without a decision. Both paths now go through the gate in live mode.
- **The demo runs the current pipeline** and offers the same brief `/brief` does; its sample was
  verified byte-identical to `demo/default_brief.md`.
- **Documentation reconciled with the code.** The wiki claimed sessions were memory-only, that
  roles were recorded but unenforced, and that there was no database. All three were true in
  2.0.0 and none was true in 2.1.0; the roadmap entries were split into what shipped and what
  remains.

### Fixed

- **The deterministic drafter put the brief's own heading into the contract.** A goal came back
  from triage as `# Customer Analytics Platform — Nordwind Logistics The work is organised
  into…` and appeared verbatim in the executive summary and in clause 2 — in the document a
  client reads.
- **`build_default_registry()` read global settings**, so a run constructed with dry-run
  settings still wrote to live Jira.
- **The reviewer was appointed too late** to approve, so a correctly authorised approver was
  refused on their own run.
- **`member` mapped to the service role** rather than author, which denied approval to ordinary
  accounts.
- **`save_run` refreshed `updated_at` on every write**, which made the run list reorder itself
  for reasons unrelated to the run.
- **CI hung for five minutes** installing ffmpeg in the documentation job; the video checks now
  skip with a note when `ffprobe` is absent.

---

## 2.0.0 — 2026-10-24

The first tagged release. `2.0.0` is the version the repository already declared in
`pyproject.toml` and the image tag in `docker-compose.yml`; it is tagged as declared rather
than renumbered to suit the absence of an earlier tag.

**Image:** `ghcr.io/sergeyger/sowsprint.ai:2.0.0` (also `2.0` and `latest`), linux/amd64,
with provenance and SBOM attached.

**Before this tag, the README offered a `docker run` command for an image that did not
exist.** No `v*` tag had ever been pushed, `publish.yml` therefore never ran, and nothing
contradicted the claim. The tag is what makes it true; the lesson is that a documented command
nobody has executed is a claim, not a feature.

### Added

- **Dev Containers support** (`.devcontainer/`) — a development container with Python 3.11,
  pytest, ruff, git and ffmpeg, attached to from VS Code on any laptop while the code and the
  database stay on the server. Namespaced per developer, and it publishes no ports on the
  host: VS Code tunnels them, so several developers work simultaneously without competing for
  8000.
- **Start / stop / status scripts** for Windows (`.ps1`) and Linux (`.sh`), with a port check
  that names the container *and* the compose project holding a port. On a machine running
  several projects, "address already in use" is not a useful error message.
- **Live voice transcription** — the words appear in the message box as they are spoken, then
  wait for confirmation before anything is sent. Speech recognition mishears names and
  numbers, and a brief that went in wrong produces a contract about the wrong engagement.
- **Repository ruleset `Protect Main`** — direct pushes are rejected, four CI checks must pass
  with the branch up to date, force-push and deletion are refused. Applies to the maintainer
  too.
- **Wiki: [LLM quality & reliability](https://github.com/SergeyGer/SOWSprint.ai/wiki/LLM-Quality)**
  — how each number that decides a verdict is produced, and what is not guaranteed.
- `SECURITY.md`, `CHANGELOG.md`, issue templates and `CODE_OF_CONDUCT.md`.
- `docs/README.md` — a map of every documentation source in the repository.

### Changed

- **The demo is genuinely offline.** `build_offline_settings()` pinned `llm_provider` but not
  `critic_provider`, which is separate and defaulted to OpenAI — so on any machine configured
  to run the real application, the demo made billable calls while its sidebar said it made
  none. Every provider is pinned now. Measured: the same run went from 110 seconds to 30.
- **The demo mirrors the application** — verdict first, then the schedule, the resources
  counted from the story roles, the milestone timeline and the artefact table, in the same
  order the product uses. A demo that answers different questions than the product is a demo
  of a different product.
- **README restructured.** "Start here" and a table of contents now sit above the first
  screenful; the quickstart used to begin at line 252 of 353.

### Fixed

- **Grounding was estimated by a language model and used as a hard cap on the score.** It is
  arithmetic: each clause records the evidence ids it cites and the document records which were
  retrieved. Measured against live runs the judge reported **1.0** where the count was
  **0.44** — overestimated by more than a factor of two, always in the direction that lets a
  weak contract pass. `compute_grounding()` counts it now.
- **Framing clauses counted as ungrounded obligations.** The exclusion list compared headings
  for equality against `"Definitions"`, and real drafts title clauses `"Definitions and
  Interpretation"` — none matched, so four framing clauses sat in the denominator and the
  reported ratio was twenty points low.
- **The voice panel belonged to the first recording, not the latest.** `getElementById` returns
  the first match, and every recording left its panel in the transcript — so every later
  recording re-displayed the *first* brief ever spoken.
- **`on_audio_start` fires once per session, not per recording** (measured). The panel was
  created there, so the second recording had none.
- **`chunk.elapsedTime` is milliseconds**, read as seconds it triggered a Whisper call on every
  chunk — 128 calls for two recordings.
- **Long non-streaming Anthropic requests are cut server-side**, regardless of the client
  timeout; raising it to 420 seconds changed nothing. The adapter streams now.
- **`"system": None` sent to Anthropic** — rejected as a malformed array, only on the paths
  without a system prompt. Fixed once before, lost, restored, and now pinned by two tests.
- **Milestones ended on a Sunday** — `start + weeks × 7 − 1` lands on a weekend for a Monday
  start, so the whole schedule was drawn with Sunday boundaries.
- **The corpus panel reported "0 corpus entries"** whenever the index was reused, because the
  reuse branch of `IngestStats` set only `chunks` and `backend`.
- **Start and end dates did not land in Jira**, then did — the schedule is now computed in code
  and stamped over the model's plan rather than requested of it.
- **CI checks that were red for reasons that were not defects** — the live-demo check reported a
  broken demo whenever Streamlit Cloud had put it to sleep, and the badge check failed after any
  dependency bump until the data file was regenerated.

### Security

- Four classes of CodeQL alert on the new code: a URL substring check that also matched
  `https://example.com/atlassian.net/…`, a `if True:` left in a test, two genuine import
  cycles, and seven implicit string concatenations in lists.
- `openai` widened to `<4.0.0` after verifying the full suite passes with openai 3.31, anthropic
  1.13 and httpx2 installed.

---

## Versioning

Releases are git tags of the form `vX.Y.Z`; pushing one triggers `.github/workflows/publish.yml`,
which builds the image, pushes it to GHCR, and **starts the pushed image** before reporting
success — a green push is not evidence that a newcomer can run it.

The image tags `X.Y.Z`, `X.Y` and `latest` are derived from the git tag. Pin by digest if you
need reproducibility beyond a version:

```bash
docker pull ghcr.io/sergeyger/sowsprint.ai:2.0.0
docker pull ghcr.io/sergeyger/sowsprint.ai@sha256:<digest>
```

Only the latest release receives fixes; there are no maintained branches to back-port to.
