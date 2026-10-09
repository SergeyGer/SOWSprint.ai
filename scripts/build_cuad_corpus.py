#!/usr/bin/env python
"""Convert CUAD v1 into corpus entries for ``data/corpus/``.

CUAD (Contract Understanding Atticus Dataset) is 510 US commercial contracts filed as
SEC Exhibit 10.*, annotated by lawyers across 41 clause categories. It is CC BY 4.0.

Why this dataset and not a procurement one
------------------------------------------
Several widely-cited procurement datasets contain only tender *notices* — agency,
NAICS code, deadline, set-aside — with no contract text at all, so they cannot support
clause retrieval. CUAD contains the clauses themselves.

Its limits, stated because they shape what the corpus can answer:

* **US only.** There is no GDPR, EU AI Act or CCPA clause language in it. It upgrades
  the US side of the corpus and leaves the EU side on hand-written material.
* **No jurisdiction field.** The governing-law answer is a short label ("Nevada"), not
  a state tag, and 24 of 434 spans truncate before the state name. The state is captured
  as a tag where it parses cleanly and omitted where it does not, rather than guessed.
* **Skewed to licence and distribution agreements**, not statements of work. Clauses are
  filed under the category the annotators assigned and reused as drafting precedent.

Usage::

    python scripts/build_cuad_corpus.py --source CUAD_v1.json
    python scripts/build_cuad_corpus.py --source CUAD_v1.json --per-category 12
    python scripts/build_cuad_corpus.py --source CUAD_v1.json --stats-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "corpus" / "cuad_v1.json"

#: CUAD categories worth retrieving for a B2B services engagement, mapped to a risk
#: level. The 41 categories include a lot of licence-specific machinery (source code
#: escrow, affiliate licences, most-favoured-nation) that a statement of work never
#: needs; including it would dilute retrieval without adding drafting value.
CATEGORY_RISK: dict[str, str] = {
    # Commercial core
    "Cap On Liability": "high",
    "Uncapped Liability": "high",
    "Liquidated Damages": "high",
    "Indemnification": "high",
    "Limitation Of Liability": "high",
    "Warranty Duration": "medium",
    "Insurance": "medium",
    "Governing Law": "medium",
    "Termination For Convenience": "medium",
    "Post-Termination Services": "medium",
    "Renewal Term": "low",
    "Expiration Date": "low",
    "Notice Period To Terminate Renewal": "medium",
    "Change Of Control": "high",
    "Anti-Assignment": "medium",
    "Third Party Beneficiary": "low",
    "Audit Rights": "medium",
    "Confidentiality": "medium",
    "Ip Ownership Assignment": "high",
    "Joint Ip Ownership": "high",
    "License Grant": "medium",
    "Non-Compete": "medium",
    "Exclusivity": "medium",
    "No-Solicit Of Employees": "medium",
    "No-Solicit Of Customers": "medium",
    "Non-Disparagement": "low",
    "Covenant Not To Sue": "medium",
    "Revenue/Profit Sharing": "medium",
    "Minimum Commitment": "medium",
    "Volume Restriction": "low",
    "Price Restrictions": "medium",
    "Competitive Restriction Exception": "low",
    "Rofr/Rofo/Rofn": "medium",
    "Most Favored Nation": "medium",
    "Unlimited/All-You-Can-Eat-License": "low",
    "Irrevocable Or Perpetual License": "medium",
    "Non-Transferable License": "low",
    "Affiliate License-Licensee": "low",
    "Affiliate License-Licensor": "low",
    "Source Code Escrow": "low",
}

#: Categories that are document metadata rather than contract language. Including them
#: would fill the index with party names and dates that no clause question retrieves.
SKIP = {"Document Name", "Parties", "Agreement Date", "Effective Date"}

#: Matches CUAD's question template, e.g.
#: ``Highlight the parts (if any) of this contract related to "Governing Law" ...``
QUESTION_RE = re.compile(r'related to "([^"]+)"', re.I)

#: US states, to tag a clause with its governing law where the answer names one.
STATE_RE = re.compile(
    r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|"
    r"Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|"
    r"Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|"
    r"Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|"
    r"North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
    r"South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|"
    r"Wisconsin|Wyoming|Ontario|England)\b"
)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def clean(text: str) -> str:
    """Collapse CUAD's whitespace without destroying sentence boundaries."""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build(source: Path, per_category: int, min_chars: int) -> tuple[list[dict], Counter]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    documents = payload.get("data") or []

    # Collect every answer span first, then cap per category, so the sample is spread
    # across contracts rather than concentrated in whichever document comes first.
    by_category: dict[str, list[dict]] = defaultdict(list)
    seen_text: set[str] = set()
    stats: Counter = Counter()

    for document in documents:
        filename = str(document.get("title") or "")
        for paragraph in document.get("paragraphs") or []:
            for qa in paragraph.get("qas") or []:
                match = QUESTION_RE.search(str(qa.get("question") or ""))
                if not match:
                    continue
                category = match.group(1).strip()
                if category in SKIP or category not in CATEGORY_RISK:
                    continue
                answers = qa.get("answers") or []
                if not answers:
                    stats["unanswered"] += 1
                    continue

                text = clean(str(answers[0].get("text") or ""))
                if len(text) < min_chars:
                    stats["too_short"] += 1
                    continue
                # Many clauses are extracted more than once under different questions;
                # identical text in the index would be retrieved as the same passage.
                fingerprint = text[:200].casefold()
                if fingerprint in seen_text:
                    stats["duplicate"] += 1
                    continue
                seen_text.add(fingerprint)

                states = STATE_RE.findall(text) if category == "Governing Law" else []
                by_category[category].append(
                    {
                        "text": text,
                        "filename": filename,
                        "states": [s for s in dict.fromkeys(states)][:2],
                    }
                )
                stats["collected"] += 1

    entries: list[dict] = []
    for category in sorted(by_category):
        candidates = by_category[category]
        # Even spread across the candidate list rather than the first N, which would
        # all come from a handful of documents.
        if len(candidates) > per_category:
            step = len(candidates) / per_category
            candidates = [candidates[int(i * step)] for i in range(per_category)]

        for index, item in enumerate(candidates):
            tags = [slugify(category)]
            if item["states"]:
                tags.append(f"governing-law-{slugify(item['states'][0])}")
            # Keep the source document identifiable without shipping the filing name as
            # a title; traceability matters more than pretty output here.
            doc_hint = item["filename"].split("_")[0][:40] if item["filename"] else "unknown"
            entries.append(
                {
                    "id": f"us-cuad-{slugify(category)}-{index:03d}",
                    "jurisdiction": "US",
                    "source": f"CUAD v1 — {category} ({doc_hint})",
                    "doc_type": "contract_clause",
                    "title": f"{category} clause from a US commercial agreement",
                    "text": item["text"],
                    "tags": tags,
                    "risk_level": CATEGORY_RISK[category],
                }
            )
            stats[f"kept:{category}"] += 1

    return entries, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="Path to CUAD_v1.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--per-category", type=int, default=8, help="Clauses kept per category.")
    parser.add_argument("--min-chars", type=int, default=180, help="Discard shorter extractions.")
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        print(f"✗ {source} not found.", file=sys.stderr)
        print(
            "  Download CUAD v1 (CC BY 4.0):\n"
            "    https://huggingface.co/datasets/theatticusproject/cuad\n"
            "  or:  huggingface-cli download theatticusproject/cuad CUAD_v1/CUAD_v1.json",
            file=sys.stderr,
        )
        return 1

    entries, stats = build(source, args.per_category, args.min_chars)

    kept = {k.split(":", 1)[1]: v for k, v in stats.items() if k.startswith("kept:")}
    chars = sum(len(e["text"]) for e in entries)
    print(f"  source        {source}")
    print(f"  contracts     {len(json.loads(source.read_text())['data'])}")
    print(f"  collected     {stats['collected']} answer span(s)")
    print(f"    duplicates  {stats['duplicate']} rejected as repeated text")
    print(f"    too short   {stats['too_short']} rejected below {args.min_chars} chars")
    print(f"    unanswered  {stats['unanswered']} categories with no clause present")
    print(f"  kept          {len(entries)} clause(s) across {len(kept)} categories")
    print(f"  volume        {chars:,} chars (~{chars // 4:,} tokens)")
    if args.stats_only:
        print("\n  per category")
        for name in sorted(kept):
            print(f"    {name[:40]:42} {kept[name]}")
        return 0

    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  wrote         {destination}")
    print("\n  Next:  python -m sowsprint.rag.ingest --recreate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
