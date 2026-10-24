# Security policy

## Reporting a vulnerability

Report privately through GitHub's [private vulnerability
reporting](https://github.com/SergeyGer/SOWSprint.ai/security/advisories/new) — the **Security**
tab → *Report a vulnerability*. That opens a private advisory only you and the maintainer can
see.

Please do not open a public issue for a security problem, and please do not include working
credentials in a report.

**What to expect**

| | |
| :-- | :-- |
| First response | within 7 days |
| Assessment and severity | within 14 days |
| Fix, or a public explanation of why there will not be one | within 90 days |

This is a single-maintainer project, so those are honest targets rather than a contractual
commitment. If 90 days pass without a fix and without an explanation, publish the finding —
that deadline is the point of the policy.

## What is in scope

Anything that lets someone read, change or exfiltrate data they should not reach:

- **Credential handling** — `.env` parsing, keys leaking into logs, transcripts, artefacts,
  error messages or the run report
- **The tool executor** — a way to make the pipeline call Jira, Notion or any HTTP endpoint
  outside the declared tool set, or outside the dry-run guard
- **Prompt injection with a real effect** — a brief or a retrieved passage that makes an agent
  perform an action rather than draft text. This system treats retrieved text as data and the
  approval gate stands between a plan and provisioning; a way *around* the gate is a
  vulnerability, a way to *influence wording* is not
- **Path traversal** in artefact writing or file upload
- **Dependency vulnerabilities** with a reachable exploit path in this codebase
- **Authentication bypass** in the Chainlit login

## What is not in scope

- **The absence of TLS and role separation.** The application ships as a LAN pilot deployment
  and says so ([Security](https://github.com/SergeyGer/SOWSprint.ai/wiki/Security),
  [Honest limitations](https://github.com/SergeyGer/SOWSprint.ai#honest-limitations)). Running
  it on the public internet without a reverse proxy is a deployment error, not a defect.
- **A drafted contract being commercially wrong.** This is not legal advice, and that is
  stated in the README rather than buried.
- **The demo.** `demo/streamlit_app.py` runs the deterministic engine with no credentials and
  cannot reach a third-party service by construction.
- **Cost.** The budget guard limits spend; it does not make cloud models free. A brief that
  costs more than you expected is not a vulnerability.

## Secrets in the repository

`.env` is gitignored and has never been committed. If you find a credential in the repository,
in a release artefact or in the demo deployment, treat it as the highest-severity report and
use the private channel above.

## What is already in place

- Dependencies are scanned by Dependabot and CodeQL on every push
- `main` is protected by a ruleset: direct pushes are rejected, four CI checks must pass, and
  the branch cannot be force-pushed or deleted
- Dry-run is the default for every integration, so a misconfigured run validates and sends
  nothing
- Every model call is priced as it happens and the run halts at the session budget

## Supported versions

The project is pre-1.0 in everything but its image tag (`2.0.0`). Only the tip of `main`
receives fixes; there are no maintained release branches to back-port to.
