# SOWSprint.ai

**Autonomous AI agent for B2B contract automation and project planning.**

Turns a raw requirement — typed or dictated — into an enforceable Statement of Work
plus an instantly deployable agile backlog, in one pass, with a real-time compute-cost
dashboard.

## What happens when you send a requirement

1. **Triage Agent** parses it into a normalised scope. If critical variables are
   missing it returns `INCOMPLETE` and asks **exactly three** targeted questions —
   each with a one-line explanation of why the contract cannot be drafted without it.
2. **Architect Agent** decomposes the validated scope into milestones, epics and user
   stories with testable GIVEN/WHEN/THEN acceptance criteria.
3. **Legal/SOW Agent** retrieves jurisdiction-filtered compliance evidence and drafts
   the contract, binding every substantive clause to the retrieved passages.
4. **Critic Agent** runs an adversarial *LLM-as-a-Judge* audit. High or critical
   findings bounce the contract back to the responsible agent for repair.
5. **Approval gate** — nothing touches Jira or Notion until you approve.
6. **Tool calling** provisions the delivery workspace, and the deliverables are
   attached below.

## Things worth trying

| Try this | What it demonstrates |
| :-- | :-- |
| The **EU logistics** starter | Full happy path with GDPR + EU AI Act evidence |
| The **US insurance** starter | The same graph under SEC / Delaware / CCPA / HIPAA evidence |
| The **vague brief** starter | The bounded clarification loop |
| Switch the compliance regime mid-conversation | Retrieval changes at the database query level, not after the fact |
| Dictate the brief with the microphone | Native voice capture → Whisper → Triage |

## Good to know

* Every step is expandable — timings, token counts and USD cost are attached to each
  agent step.
* The sticky dashboard tracks session spend against a hard budget ceiling; the graph
  halts rather than overshoot it.
* With no API keys configured the platform runs on its deterministic offline engine.
  Everything works; only drafting quality changes. The capability table is shown at the
  top of the chat.
* Integrations default to **dry-run**: payloads are validated and displayed but never
  sent until you turn that off in settings.
