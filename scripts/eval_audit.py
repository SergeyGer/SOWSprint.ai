"""Measure the audit instead of asserting it.

The project's weakest claim was always that the audit is good. This script turns it into a
number: build documents that are *known* to be sound, inject exactly one defect into each, and
report whether the audit finds that defect and nothing else.

The corpus is synthetic on purpose. Ground truth has to be unambiguous, and a defect injected
into a controlled document is exactly that — no legal expertise is required to know that a
contract missing its mandatory data-protection clause has a missing clause.

    python scripts/eval_audit.py                 # print the report
    python scripts/eval_audit.py --json out.json # write the machine-readable result
    python scripts/eval_audit.py --check         # fail if the figure regressed

Scoring, stated precisely because a metric nobody can reproduce is worse than none:

*   **Recall** = defects detected / defects injected.
*   **Precision** = findings matching the injected defect / all findings raised across all
    injected documents. A judge that flags everything gets 100% recall and near-zero precision,
    which is the failure mode a recall-only number hides.
*   **Clean false positives** = findings raised on the untouched baseline. Any is a failure of
    the baseline, and the script says so rather than scoring around it.

Only the deterministic checker is measured. The cloud judge costs money per call and varies
between providers; measuring it needs a fixed model, a budget and a decision about which
provider defines the published number. That is a separate piece of work, and the README says
which judge the badge refers to.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from sowsprint.llm.offline import critic_offline
from sowsprint.models import FindingCategory

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_JSON = REPO_ROOT / "docs" / "eval.json"

#: Evidence ids the sound document cites. They are fictional but must be *retrieved* ids, so
#: the clean document has to declare the same set it cites.
EVIDENCE_IDS = [f"eval{i:08d}" for i in range(1, 9)]

JURISDICTION = "EU"

#: Milestone names that must appear in the contract, or the scope-drift check fires.
MILESTONES = [
    "Discovery and Requirements",
    "Core Platform Build",
    "Integration and Hardening",
]

BLUEPRINT = {
    "solution_overview": "A controlled fixture used to measure the quality audit.",
    "estimated_duration_weeks": 9,
    "estimated_total_points": 60,
    "milestones": [
        {
            "name": m,
            "objective": f"Complete {m.lower()} to the agreed acceptance criteria.",
            "duration_weeks": 3,
            "epics": [],
        }
        for m in MILESTONES
    ],
}

BODY = (
    "The Supplier shall deliver the obligation described here in accordance with the agreed "
    "schedule, and shall report progress to the Customer in writing every second week. "
    "Deliverables are accepted only when they meet the criteria recorded in this agreement."
)


def _clause(number: str, heading: str, citations: list[str], body: str = BODY) -> dict[str, Any]:
    return {
        "number": number,
        "heading": heading,
        "body": body,
        "citations": citations,
        "jurisdiction_tags": [JURISDICTION],
        "locked": False,
    }


#: The clean baseline. Every mandatory clause is present, every substantive clause cites a
#: retrieved id, the payment schedule sums to 100, and no ambiguity phrase appears.
CLAUSES: list[tuple[str, str, int]] = [
    ("1", "Definitions", 0),
    ("2", "Scope of Services", 0),
    ("3", "Deliverables and Milestones", 2),
    ("4", "Acceptance Procedure", 1),
    ("5", "Change Control", 1),
    ("6", "Fees and Payment", 1),
    ("7", "Data Protection and GDPR Compliance", 2),
    ("8", "Artificial Intelligence Act Compliance", 1),
    ("9", "Confidentiality", 1),
    ("10", "Intellectual Property", 1),
    ("11", "Warranties and Service Levels", 1),
    ("12", "Limitation of Liability", 1),
    ("13", "Term and Termination", 1),
    ("14", "Governing Law and Jurisdiction", 1),
]


def sound_sow() -> dict[str, Any]:
    """A document that passes every check the audit performs."""
    clauses = []
    cursor = 0
    for number, heading, count in CLAUSES:
        citations = [EVIDENCE_IDS[(cursor + i) % len(EVIDENCE_IDS)] for i in range(count)]
        cursor += count
        body = BODY
        if heading == "Deliverables and Milestones":
            body = BODY + " Milestones: " + "; ".join(MILESTONES) + "."
        clauses.append(_clause(number, heading, citations, body))
    return {
        "title": "Statement of Work — Evaluation Fixture",
        "client_name": "Fixture Customer Ltd",
        "vendor_name": "Fixture Supplier GmbH",
        "jurisdiction": JURISDICTION,
        "governing_law": "Ireland",
        "executive_summary": "A controlled fixture used to measure the quality audit.",
        "clauses": clauses,
        "payment_schedule": [
            {
                "name": "Mobilisation",
                "trigger": "On signature",
                "percentage": 30.0,
                "amount_usd": None,
            },
            {
                "name": "Final acceptance",
                "trigger": "On acceptance of all deliverables",
                "percentage": 70.0,
                "amount_usd": None,
            },
        ],
        "total_value_usd": 120000.0,
        "compliance_notes": [],
        "assumptions_and_dependencies": [],
        "retrieved_evidence_ids": list(EVIDENCE_IDS),
    }


# ---------------------------------------------------------------------------------------
# Injections
# ---------------------------------------------------------------------------------------
@dataclass
class Injection:
    name: str
    category: FindingCategory
    describe: str
    mutate: Callable[[dict[str, Any]], None]


def _drop_clause(target: str) -> Callable[[dict[str, Any]], None]:
    def mutate(sow: dict[str, Any]) -> None:
        sow["clauses"] = [c for c in sow["clauses"] if c["number"] != target]

    return mutate


def _fabricate_citation(sow: dict[str, Any]) -> None:
    # An id that was never retrieved. This is the failure mode the corpus rule exists for:
    # it reads as substantiation and is not.
    sow["clauses"][5]["citations"] = ["deadbeefdeadbeef"]


def _break_payment(sow: dict[str, Any]) -> None:
    sow["payment_schedule"][1]["percentage"] = 50.0


def _ambiguous(sow: dict[str, Any]) -> None:
    sow["clauses"][4]["body"] = BODY + " Timelines are to be determined by mutual agreement."


def _empty_body(sow: dict[str, Any]) -> None:
    # Deliberately NOT "TBD.": that phrase also trips the ambiguity scan, so the fixture would
    # carry two defects and the report would count the second as a precision failure.
    sow["clauses"][9]["body"] = "Placeholder."


def _unground(sow: dict[str, Any]) -> None:
    for clause in sow["clauses"]:
        clause["citations"] = []


def _scope_drift(sow: dict[str, Any]) -> None:
    for clause in sow["clauses"]:
        if clause["heading"] == "Deliverables and Milestones":
            clause["body"] = BODY + " Milestones: phase one, phase two."


def _break_payment_up(sow: dict[str, Any]) -> None:
    sow["payment_schedule"][1]["percentage"] = 90.0


def _ambiguous_second(sow: dict[str, Any]) -> None:
    sow["clauses"][10]["body"] = BODY + " Support is provided on a best efforts basis."


def _empty_second(sow: dict[str, Any]) -> None:
    sow["clauses"][12]["body"] = "Placeholder."


def _fabricate_second(sow: dict[str, Any]) -> None:
    sow["clauses"][11]["citations"] = ["0000000000000000"]


def _unground_partial(sow: dict[str, Any]) -> None:
    # Half the clauses lose their citations. Grounding lands near 45%, which is below the
    # 50% threshold the check uses — a partial loss must still be caught, or the check only
    # measures the easy case.
    for clause in sow["clauses"][7:]:
        clause["citations"] = []


#: Two variants of most defect kinds. One injection per kind measures whether the check
#: exists; two measure whether it works on more than the example it was written against.
def _scope_drift_partial(sow: dict[str, Any]) -> None:
    # One milestone of three no longer appears. The full-drift case proves the check runs;
    # this proves it is sensitive to a single missing milestone rather than only to total
    # replacement, which is the state a real draft drifts into.
    for clause in sow["clauses"]:
        if clause["heading"] == "Deliverables and Milestones":
            clause["body"] = BODY + " Milestones: " + "; ".join(MILESTONES[:2]) + "."


INJECTIONS: list[Injection] = [
    Injection(
        "missing_gdpr_clause",
        FindingCategory.MISSING_CLAUSE,
        "Drop the GDPR clause a EU engagement cannot ship without",
        _drop_clause("7"),
    ),
    Injection(
        "missing_confidentiality_clause",
        FindingCategory.MISSING_CLAUSE,
        "Drop the confidentiality clause",
        _drop_clause("9"),
    ),
    Injection(
        "missing_liability_clause",
        FindingCategory.MISSING_CLAUSE,
        "Drop the limitation-of-liability clause",
        _drop_clause("12"),
    ),
    Injection(
        "fabricated_citation",
        FindingCategory.HALLUCINATION,
        "Cite an evidence id that was never retrieved",
        _fabricate_citation,
    ),
    Injection(
        "fabricated_citation_other_clause",
        FindingCategory.HALLUCINATION,
        "Cite a fabricated id from a different clause",
        _fabricate_second,
    ),
    Injection(
        "payment_schedule_short",
        FindingCategory.COMMERCIAL_RISK,
        "Make the payment percentages sum to 80 instead of 100",
        _break_payment,
    ),
    Injection(
        "payment_schedule_over",
        FindingCategory.COMMERCIAL_RISK,
        "Make the payment percentages sum to 120 instead of 100",
        _break_payment_up,
    ),
    Injection(
        "ambiguous_obligation",
        FindingCategory.AMBIGUITY,
        "Replace a measurable obligation with 'to be determined'",
        _ambiguous,
    ),
    Injection(
        "ambiguous_second_phrase",
        FindingCategory.AMBIGUITY,
        "Introduce 'best efforts' into a different clause",
        _ambiguous_second,
    ),
    Injection(
        "empty_clause",
        FindingCategory.TEMPLATE_DEVIATION,
        "Leave a clause body as a placeholder",
        _empty_body,
    ),
    Injection(
        "empty_other_clause",
        FindingCategory.TEMPLATE_DEVIATION,
        "Leave a different clause body as a placeholder",
        _empty_second,
    ),
    Injection(
        "ungrounded_contract",
        FindingCategory.UNSUPPORTED_CLAIM,
        "Remove every citation, so nothing is traceable to evidence",
        _unground,
    ),
    Injection(
        "ungrounded_partial",
        FindingCategory.UNSUPPORTED_CLAIM,
        "Remove citations from half the clauses",
        _unground_partial,
    ),
    Injection(
        "scope_drift",
        FindingCategory.SCOPE_DRIFT,
        "Replace the blueprint milestones with different ones",
        _scope_drift,
    ),
    Injection(
        "scope_drift_partial",
        FindingCategory.SCOPE_DRIFT,
        "Drop one milestone of three from the contract",
        _scope_drift_partial,
    ),
]


# ---------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------
def _evidence_block() -> str:
    """Render the evidence exactly as `Chunk.to_context_block` does.

    The format is load-bearing. `_evidence_index` parses lines of the shape

        [1] <source> (jurisdiction=EU) id=<chunk_id>
        <passage text>

    and silently produces zero entries for anything else — which disables the citation-
    existence check and the grounding check at once. The first version of this fixture used
    an XML-looking block, and the audit duly reported nothing, which read like three missed
    detections and was three unrun checks.
    """
    blocks = []
    for index, eid in enumerate(EVIDENCE_IDS, start=1):
        blocks.append(
            f"[{index}] Compliance corpus (jurisdiction=EU) id={eid}\n"
            f"Retrieved compliance passage {eid} covering the obligations of this agreement."
        )
    return "\n\n".join(blocks)


def audit(sow: dict[str, Any]):
    return critic_offline(
        sow,
        _evidence_block(),
        JURISDICTION,
        blueprint_payload=BLUEPRINT,
    )


def run() -> dict[str, Any]:
    clean = sound_sow()
    baseline = audit(clean)

    rows = []
    for injection in INJECTIONS:
        subject = json.loads(json.dumps(clean))  # deep copy; the mutation must not leak
        injection.mutate(subject)
        report = audit(subject)
        categories = [f.category for f in report.findings]
        detected = injection.category in categories
        rows.append(
            {
                "injection": injection.name,
                "expected": injection.category.value,
                "detected": detected,
                "categories_raised": sorted({c.value for c in categories}),
                "finding_categories": [c.value for c in categories],
                "findings": len(report.findings),
                "passed": report.passed,
                "description": injection.describe,
            }
        )

    detected = sum(1 for r in rows if r["detected"])
    # A finding "matches" when its category is the one injected. Everything else that the
    # audit raised on an injected document is a mismatch, and mismatches are the denominator
    # of precision — this is what stops a flag-everything judge from scoring well.
    matched = sum(
        1 for r in rows for c in r["finding_categories"] if c == r["expected"]
    )
    raised = sum(r["findings"] for r in rows)

    # Categories that fire on a document with exactly one defect of a known kind, and that
    # are not that kind. Some are legitimate — removing a clause also lowers grounding — so
    # the report lists them rather than treating every one as an error.
    collateral: Counter[str] = Counter()
    for r in rows:
        for c in r["categories_raised"]:
            if c != r["expected"]:
                collateral[c] += 1

    clean_findings = [f.category.value for f in baseline.findings]

    return {
        "judge": "deterministic (offline engine)",
        "jurisdiction": JURISDICTION,
        "injections": len(INJECTIONS),
        "recall": round(detected / len(INJECTIONS), 4),
        "precision": round(matched / raised, 4) if raised else 1.0,
        "detected": detected,
        "clean_findings": len(clean_findings),
        "clean_categories": clean_findings,
        "clean_passed": baseline.passed,
        "clean_quality_score": baseline.quality_score,
        "by_injection": rows,
        "collateral_categories": dict(collateral.most_common()),
        # Preformatted for the README badge, because shields.io's dynamic/json endpoint
        # substitutes values verbatim and cannot turn 1.0 into "100%". The numbers above stay
        # authoritative; this is a rendering of them, regenerated by the same run.
        "badge": (
            f"{detected}/{len(INJECTIONS)} defects · "
            f"{round(matched / raised, 4) if raised else 1.0:.0%} precision"
        ),
    }


def render(result: dict[str, Any]) -> str:
    lines = [
        "Audit measurement — deterministic checker",
        "",
        f"  injections            {result['injections']}",
        f"  recall                {result['recall']:.1%}  "
        f"({result['detected']}/{result['injections']} defects found)",
        f"  precision             {result['precision']:.1%}",
        f"  false positives on a clean document   {result['clean_findings']}",
        "",
        "  per injection",
    ]
    for row in result["by_injection"]:
        mark = "found" if row["detected"] else "MISSED"
        lines.append(
            f"    {mark:7} {row['injection']:26} expected {row['expected']:20} "
            f"raised {row['findings']}"
        )
    if result["collateral_categories"]:
        lines += ["", "  other categories raised on injected documents"]
        for name, count in result["collateral_categories"].items():
            lines.append(f"    {name:26} {count}")
    if result["clean_categories"]:
        lines += ["", "  FALSE POSITIVES on the clean baseline", ]
        for name in result["clean_categories"]:
            lines.append(f"    {name}")
    else:
        lines += ["", "  the clean baseline raises no findings"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=pathlib.Path, nargs="?", const=DEFAULT_JSON,
                        help=f"write the result to a file (default {DEFAULT_JSON})")
    parser.add_argument("--check", action="store_true",
                        help="fail if recall is below the threshold or the baseline is noisy")
    parser.add_argument("--min-recall", type=float, default=0.90)
    args = parser.parse_args(argv)

    result = run()
    print(render(result))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\n  written to {args.json.relative_to(REPO_ROOT)}")

    if args.check:
        problems = []
        if result["clean_findings"]:
            problems.append(
                f"the clean baseline raised {result['clean_findings']} finding(s) — the "
                "corpus is wrong, not the audit"
            )
        if result["recall"] < args.min_recall:
            problems.append(
                f"recall {result['recall']:.1%} is below the {args.min_recall:.0%} threshold"
            )
        for problem in problems:
            print(f"\n  FAIL: {problem}", file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
