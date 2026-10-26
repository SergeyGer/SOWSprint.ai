# How answer quality is measured, and how unreliable output is prevented

A language model will produce a confident, well-formatted, plausible contract for a brief it
has misunderstood. Nothing about the output signals the misunderstanding: the formatting is
excellent, the clauses read professionally, and the tone is certain. This page is about the
machinery that stands between that output and a signed document.

The governing principle is narrow and worth stating plainly: **the system never trusts the
model's judgement about its own work.** Every number that decides a verdict is either computed
from evidence in code, or derived from a list a reader can inspect and argue with.

---

## 1. The score is not the model's score

The Critic agent is asked for a `quality_score` between 0 and 1. That number is **discarded**
and recomputed from the findings it returned.

`models.py` → `score_from_findings()`:

```
score = 1.0 − 0.30 × (blocking findings) − min(0.20, 0.05 × (advisory findings))
```

with `BLOCKING_WEIGHT = 0.30`, `ADVISORY_WEIGHT = 0.05`, `MAX_ADVISORY_PENALTY = 0.20` in
`models.py`.

Why: a holistic score cannot be reconstructed from the finding list printed beside it. Two
runs over the same findings would show different numbers, and the moment someone moves a
threshold the two definitions disagree about the outcome. The model's own figure is kept in
`raw_quality_score` rather than thrown away — a judge that is consistently harsher or gentler
than the formula is worth seeing.

**The pass bar is 0.72**, and passing also requires zero blocking findings
(`nodes.py`, the critic node).

---

## 2. Severity is decided by category, not by the model's label

A judge that labels a stylistic quibble `CRITICAL` halts a run for nothing. `models.py` →
`calibrate_severity()` maps each finding to a fixed set of categories and caps the advisory
ones at `MEDIUM`:

```python
BLOCKING_CATEGORIES = {
    FindingCategory.HALLUCINATION,    # a fabricated citation
    FindingCategory.MISSING_CLAUSE,   # a mandatory clause absent
    FindingCategory.COMMERCIAL_RISK,  # payment arithmetic, unbounded liability
    FindingCategory.SCOPE_DRIFT,      # the contract contradicts the plan
}
ADVISORY_SEVERITY_CEILING = Severity.MEDIUM
```

Anything outside those four is advisory however loudly the model labels it. The decision about
what blocks a signature is a property of the system, not of the model's mood.

---

## 3. Grounding is counted, not estimated

The score is capped by grounding: `min(score, max(0.40, grounding_ratio))`. That cap decides
verdicts, so the number under it had to be trustworthy.

It was not. The judge was asked for *"the share of substantive clauses whose citations all
exist in the evidence block"* — a quantity the document answers exactly — and it estimated.
Measured against live runs (`critic.grounding_estimate_off` in the log):

| Clauses considered | Actually grounded | Judge said | Reality |
|---|---|---|---|
| 16 | 7 | **1.0** | **0.44** |
| 24 | 12 | **1.0** | **0.50** |
| 14 | 7 | **0.96** | **0.50** |

Overestimated by more than a factor of two, every time, always in the direction that lets a
weak contract pass.

`models.py` → `compute_grounding()` now counts it: each clause records the evidence ids it
cites, the document records which ids were retrieved, and the share whose citations all
resolve is arithmetic. A citation naming an id nobody retrieved is reported separately in
`unsupported`, because it reads as substantiation and is worse than no citation at all.

Two further defects surfaced from measuring it:

* **Framing clauses were counted as ungrounded obligations.** The exclusion list compared
  headings for equality against `"Definitions"`, and a real draft titled its clauses
  `"Definitions and Interpretation"` and `"Milestones and Delivery Schedule"` — none matched.
  Four framing clauses sat in the denominator and the reported ratio was twenty points low.
  Headings are now matched as substrings of a normalised form.

* **Low grounding was invisible.** The cap silently lowered the score, so a reader saw *"one
  medium finding"* beside `FAILED` with no way to tell why. An `unsupported_claim` finding is
  now added naming the ungrounded clauses.

---

## 4. The judge's factual claims are checked against the document

A judge can report a clause absent when it is present. Observed live: a `CRITICAL` — *"the
contract does not include a clause addressing governing law and dispute resolution"* — raised
against a contract whose Clause 25 is titled exactly that.

`models.py` → `verify_missing_clause_findings()` cross-checks every `MISSING_CLAUSE` claim
against the document itself. A contradicted finding is removed and logged as
`critic.missing_clause_contradicted`, with the notes kept in `report.dismissed` so the removal
is visible rather than silent.

Pinned by `tests/test_offline_engine.py` → `TestJudgeSanityCheck`.

---

## 5. Agreed clauses are byte-identical by construction

When a contract is revised, clauses the parties have already agreed are **locked**. The
guarantee that they do not change is enforced in code, not requested of the model:
`offline.py` → `_restore_locked()` overwrites every locked clause from the previous revision
and re-marks it. The model can rewrite them; the result is discarded.

The revision diff (`models.py` → `SOWRevision.diff()`) reports `locked` as its own change
category, so an unexpected modification is visible rather than buried in a diff.

---

## 6. A second checker that is not a language model

The offline engine contains a rule-based auditor with no model in it at all —
`llm/offline.py`, seven independent checks:

1. **Required clause coverage** against the jurisdiction's mandatory set
2. **Citation existence** — every cited id must be in the retrieved set
3. **Evidence grounding** — the counted ratio
4. **Commercial arithmetic** — payment percentages must sum to 100
5. **Ambiguity scan** — forbidden subjective phrasing (`reasonable efforts`, `as soon as
   possible`, `TBD`)
6. **Empty clause bodies**
7. **Scope drift** against the approved blueprint

It can be wrong — it is a keyword checker, and on cloud-drafted text it produces false
positives, which it labels. But it cannot be *persuaded*, which is the property that matters
when the thing it checks was written by a model.

---

## 7. The repair loop is bounded

A failed audit returns the contract to the Legal agent with a numbered repair list. The loop is
bounded by `max_critic_retries`; `agents/graph.py` records the bound in the graph state and the
run halts with a stated reason rather than looping. A system that retries forever is a system
that spends money until someone notices.

---

## 8. Validation at the boundary

Every model response is validated against a Pydantic schema before anything reads it —
`llm/parsing.py` → `loads_lenient()` and `extract_tagged_json()`. A response that does not
parse becomes a typed error, not a partially-populated object.

Prompt injection is treated as a data problem: a brief is never interpolated into a system
prompt, and retrieved passages are wrapped in tags with an explicit instruction that their
contents are evidence, not instructions.

---

## 9. Spending is capped

`telemetry/tracker.py` prices every call as it happens and raises `BudgetExceededError` at
`session_budget_usd` (default \$2.50). The run halts with a reason instead of continuing.

---

## How well the audit actually works

Everything above describes mechanism. This describes outcome, and it is measured rather than
asserted:

```
15 injected defects · 15 found · 0 findings on a clean contract · 100% precision
```

`scripts/eval_audit.py` builds a contract that passes every check, then injects exactly one
defect into a copy of it and asks the audit what it thinks.

| Injection | Defects injected | Found | Expected category |
| :-- | --: | --: | :-- |
| Remove a mandatory clause | 3 | 3 | `missing_clause` |
| Cite an evidence id nobody retrieved | 2 | 2 | `hallucination` |
| Payment schedule that does not sum to 100 | 2 | 2 | `commercial_risk` |
| Non-deterministic phrasing | 2 | 2 | `ambiguity` |
| Clause body left as a placeholder | 2 | 2 | `template_deviation` |
| Nothing traceable to evidence | 2 | 2 | `unsupported_claim` |
| Contract drifts from the blueprint milestones | 2 | 2 | `scope_drift` |

Two variants of most kinds, in different clauses, so the figure measures that the checks work
rather than that they exist. Reproduce it:

```bash
python scripts/eval_audit.py           # the report
python scripts/eval_audit.py --check   # exits non-zero on a regression
```

`tests/test_eval_audit.py` asserts four separate properties, because they fail for different
reasons: a missed detection means a check stopped working, collateral findings mean a check
fires on the wrong thing, a noisy baseline means the fixture is wrong and every number under
it is meaningless, and the committed `docs/eval.json` matching means the README badge still
describes this code.

**What the number rules out** is the failure this category is full of: an audit that finds
nothing, and an audit that finds everything. Both score badly here.

**What it does not tell you** is in the next section.

---

## What is *not* guaranteed

Statements this page cannot honestly make:

* **A passing contract is a correct contract.** A 0.72 score means no blocking finding was
  raised and the grounding cap did not bite. It does not mean a lawyer read it.
* **The measurement covers mechanical defects, not judgement.** The corpus is synthetic and was
  written alongside the checks, so it measures detection of *known defect classes*. A contract
  that is subtly unbalanced, or that is internally consistent but commercially unwise, is
  exactly the case this number says nothing about.
* **Only the deterministic checker is measured.** The cloud judge costs money per call and
  differs between providers; publishing its figure needs a pinned model, a budget and a
  decision about which provider defines the released number.
* **The judge is consistent between models.** `gpt-5.1` and `claude-sonnet-4-5` produce
  different finding lists for the same document. The scoring formula is stable; the findings
  it scores are not.
* **The grounding count measures relevance.** It verifies that cited ids exist and that
  anything cited resolves. It does not verify that a passage *supports* the clause citing it —
  that is what the judge's `HALLUCINATION` findings are for, and they are why that category is
  blocking.
* **The cloud mode is free of the same class of error.** The measurement above exists because
  the judge misreported a countable quantity. Wherever a model is asked for a number that
  could be computed, the same failure is available.

---

## How to verify any of this

```bash
pytest -q                                    # 490 tests, including the ones named above
pytest tests/test_offline_engine.py -q -k "Grounding or JudgeSanity"
ruff check src tests app.py
```

Live evidence of the grounding correction is in the application logs under
`critic.grounding_estimate_off` — the judge's figure and the counted one, side by side, for
every run where they diverge by more than 0.2.

---

*See also: [Quality assurance](Quality-Assurance) for the audit's role in the pipeline, and
[Agent pipeline](Agent-Pipeline) for where the Critic sits.*
