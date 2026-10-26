"""The published audit measurement, asserted rather than described.

`scripts/eval_audit.py` produces the numbers the README badge shows. These tests exist so the
badge cannot drift from reality between releases: if a change to the audit stops a check from
firing, or makes the clean baseline noisy, CI fails here instead of the figure quietly
becoming untrue.

The four assertions are deliberately separate, because they fail for different reasons:

* a **missed detection** means a check stopped working
* **collateral findings** mean a check fires on the wrong thing
* a **noisy baseline** means the fixture is wrong, and every number below it is meaningless
* the **committed JSON** matching means the badge is still describing this code
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "eval_audit.py"
COMMITTED = REPO_ROOT / "docs" / "eval.json"


def _module():
    """Load `scripts/eval_audit.py` as a module.

    Registered in `sys.modules` *before* it executes: it defines a `@dataclass`, and
    `dataclasses` resolves the class's `__module__` through `sys.modules`. Without the
    registration that lookup returns None and the import fails with an AttributeError from
    inside the standard library, which says nothing about the real cause.
    """
    name = "eval_audit"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def result():
    return _module().run()


class TestAuditMeasurement:
    def test_the_clean_baseline_is_actually_clean(self, result) -> None:
        """If the fixture trips the audit, every other number here is fiction.

        The first version of the fixture used an XML-shaped evidence block. `_evidence_index`
        parses `[n] source ... id=...` lines and silently produced zero entries for anything
        else, which disabled the citation and grounding checks at once — three "missed
        detections" that were three unrun checks.
        """
        assert result["clean_findings"] == 0, result["clean_categories"]
        assert result["clean_passed"] is True

    def test_every_injected_defect_is_found(self, result) -> None:
        missed = [row["injection"] for row in result["by_injection"] if not row["detected"]]
        assert not missed, f"the audit missed: {missed}"
        assert result["recall"] == 1.0

    def test_the_audit_raises_nothing_it_was_not_asked_to(self, result) -> None:
        """A check that fires on everything scores perfect recall and is useless.

        Two variants of most defect kinds, injected into different clauses, so this measures
        that the checks work on more than the example each was written against.
        """
        mismatched = []
        for row in result["by_injection"]:
            other = [c for c in row["finding_categories"] if c != row["expected"]]
            if other:
                mismatched.append((row["injection"], other))
        assert not mismatched, f"collateral findings: {mismatched}"
        assert result["precision"] == 1.0

    def test_the_corpus_covers_more_than_one_example_per_check(self, result) -> None:
        """One injection per kind measures whether a check exists, not whether it works."""
        from collections import Counter

        counts = Counter(row["expected"] for row in result["by_injection"])
        thin = {kind: count for kind, count in counts.items() if count < 2}
        assert not thin, f"defect kinds with a single example: {thin}"

    def test_the_committed_figure_matches_this_code(self, result) -> None:
        """`docs/eval.json` feeds the README badge. It must describe the audit that ships."""
        assert COMMITTED.is_file(), "docs/eval.json is missing — run scripts/eval_audit.py --json"
        committed = json.loads(COMMITTED.read_text())
        for key in ("injections", "recall", "precision", "detected", "clean_findings"):
            assert committed[key] == result[key], f"{key} drifted: {committed[key]} != {result[key]}"
