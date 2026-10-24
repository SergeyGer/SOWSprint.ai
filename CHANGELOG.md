# Changelog

Notable changes to this project. The format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

The repository has not been tagged yet — the image is published as `2.0.0`. Everything below
is therefore grouped by area rather than by release date, and entries record **why** a change
was made, because a changelog that lists only what changed cannot help anyone decide whether
to upgrade.

---

## Unreleased

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

`2.0.0` is the image tag. Until the first tag, treat `main` as the only supported version and
pin the image by digest if you need reproducibility:

```bash
docker pull ghcr.io/sergeyger/sowsprint.ai@sha256:<digest>
```
