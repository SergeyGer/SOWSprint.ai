# Documentation map

Every documentation source in this repository, on one page, in the order a person actually
needs them. If you are looking for something and cannot find it here, that is a defect in this
page rather than in your search.

The documentation lives in four places and they serve different purposes:

| Where | What it is | Updated by |
| :-- | :-- | :-- |
| [README](../README.md) | The landing page: what this is, why it matters, how to run it | Hand |
| **`docs/wiki/`** | The substance — architecture, operations, quality, limitations | Hand, published to the GitHub Wiki by CI |
| **`docs/`** | Guides tied to a task: setting up VS Code, operating a deployment | Hand |
| [CONTRIBUTING](../CONTRIBUTING.md) | How to change this repository, and what CI will check | Hand |

The wiki is **written here and published to GitHub**. If you are reading this on GitHub and the
pages below are not links, the published copy is at
**[github.com/SergeyGer/SOWSprint.ai/wiki](https://github.com/SergeyGer/SOWSprint.ai/wiki)**.

---

## Start by role

### "I want to see what it does"

1. **[Live demo](https://sowsprintai-tr52wy95c3da5fg2ewqwj3.streamlit.app)** — paste a brief, no
   install, no keys. It sleeps when idle; the wake takes about 30 seconds.
2. **[README → See it working](../README.md#see-it-working)** — the recorded engagement, with
   the Jira project and the Notion page it actually created.
3. **[Business case](wiki/Business-Case.md)** — the economics, and where this is the wrong tool.

### "I want to run it"

1. **[README → Start here](../README.md#start-here)** — three ways in, none needing an API key.
2. **[VS Code and Dev Containers](vscode.md)** — the development container, the debugger, the
   task shortcuts.
3. **[Operations](wiki/Operations.md)** — running the Docker stack, health checks, what the
   containers expect.
4. **[Runbook](runbook.md)** — the operational procedures, and how to verify them.

### "I want to understand how it works"

1. **[Architecture](wiki/Architecture.md)** — the shape of the system and the decisions behind it.
2. **[Agent pipeline](wiki/Agent-Pipeline.md)** — the eight stages, and what each is for.
3. **[Compliance-aware RAG](wiki/Compliance-RAG.md)** — how jurisdiction is enforced at query time
   rather than in a prompt.
4. **[LLM quality & reliability](wiki/LLM-Quality.md)** — how each number that decides a verdict
   is produced, and what is **not** guaranteed. Read this before trusting any output.
5. **[Architecture decisions](wiki/Decisions.md)** — the choices, with the alternatives that were
   rejected and why.

### "I want to change it"

1. **[CONTRIBUTING](../CONTRIBUTING.md)** — the checks to run, and the pull-request rules.
2. **[Testing and verification](wiki/Testing.md)** — what the suite covers, and what it does not.
3. **[Security policy](../SECURITY.md)** — how to report a vulnerability, and what is in scope.

### "I am deciding whether to trust it"

1. **[Honest limitations](../README.md#honest-limitations)** — stated in the README rather than
   buried.
2. **[LLM quality & reliability](wiki/LLM-Quality.md)** — including the section on what the
   guards do not cover.
3. **[Security](wiki/Security.md)** — the threat model and the gaps.
4. **[Quality assurance](wiki/Quality-Assurance.md)** — how the audit avoids trusting itself.

---

## The wiki, page by page

**Getting oriented**

| Page | What it answers |
| :-- | :-- |
| [Home](wiki/Home.md) | The front page: what this is in one screen |
| [Business case](wiki/Business-Case.md) | The economics, and where this is the wrong tool |
| [Who it is for](wiki/Who-It-Is-For.md) | Five buyer profiles, with the honest answer for each |

**How it works**

| Page | What it answers |
| :-- | :-- |
| [Architecture](wiki/Architecture.md) | System design and the decisions behind it |
| [Agent pipeline](wiki/Agent-Pipeline.md) | What each of the eight stages does |
| [Compliance-aware RAG](wiki/Compliance-RAG.md) | Why a clause from the wrong regime cannot reach the drafter |
| [Quality assurance](wiki/Quality-Assurance.md) | How the audit is kept honest |
| [LLM quality & reliability](wiki/LLM-Quality.md) | Which numbers decide a verdict, and what is not guaranteed |

**Running it**

| Page | What it answers |
| :-- | :-- |
| [Deployment & operations](wiki/Operations.md) | Containers, health, configuration |
| [Security & authentication](wiki/Security.md) | Threat model, and the gaps |
| [Integrations](wiki/Integrations.md) | Jira, Notion, and what dry-run protects you from |
| [Cost & observability](wiki/Cost-Observability.md) | Where the money goes |

**Engineering practice**

| Page | What it answers |
| :-- | :-- |
| [Testing & verification](wiki/Testing.md) | What is tested, and what is asserted instead |
| [Architecture decisions](wiki/Decisions.md) | The rejected alternatives |

**Looking ahead**

| Page | What it answers |
| :-- | :-- |
| [Roadmap](wiki/Roadmap.md) | What is next, and what is deliberately not planned |
| [Corporate LLM & data residency](wiki/Corporate-LLM.md) | The item that unblocks enterprise adoption |

---

## Guides in this directory

| File | What it is |
| :-- | :-- |
| [vscode.md](vscode.md) | Setting up VS Code: the dev container, debugging, tasks |
| [runbook.md](runbook.md) | Operating a deployment: procedures and how to verify them |
| [architecture.md](architecture.md) | The module map — where each responsibility lives |
| [corpus-research.md](corpus-research.md) | How the compliance corpus was sourced and verified |

---

## Files at the repository root

| File | What it is |
| :-- | :-- |
| [README](../README.md) | The landing page |
| [CONTRIBUTING](../CONTRIBUTING.md) | How to contribute, and what CI checks |
| [SECURITY](../SECURITY.md) | Reporting a vulnerability, and what is in scope |
| [CHANGELOG](../CHANGELOG.md) | What changed, and why |
| [CODE_OF_CONDUCT](../CODE_OF_CONDUCT.md) | Community standards |

---

## How this documentation is kept honest

Documentation rots silently, so three checks run in CI on every change:

- **`scripts/check_docs.py`** — every referenced asset exists and is tracked by git. A
  screenshot deleted from the repository breaks the build rather than becoming a broken image
  in a published page.
- **`scripts/check_wiki.py`** — every wiki page is reachable from the sidebar, and every link
  resolves. A page nobody links to is a page nobody reads.
- **`scripts/gen_badges.py --check`** — the version badges are read from the files that govern
  them, so they cannot drift from what the repository declares.

Claims in the wiki that could be checked by running something are accompanied by the command
that checks them. Where a claim cannot be verified, the page says so — see
[LLM quality & reliability](wiki/LLM-Quality.md), which has a section titled *What is not
guaranteed*.
