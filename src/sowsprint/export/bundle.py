"""Deliverable bundle assembly.

``render_deliverables`` is the single place that decides what a finished run produces.
Every artefact is written under ``data/artifacts/<run_id>/`` and described by an
:class:`~sowsprint.models.ArtifactRef` so the UI can offer it for download without
knowing anything about file formats.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import ARTIFACT_DIR
from ..models import (
    ArtifactRef,
    CritiqueReport,
    RequirementScope,
    SOWDiff,
    SOWDocument,
    TechnicalBlueprint,
)
from ..observability.logging import get_logger
from .jira_csv import render_jira_csv
from .sow_markdown import (
    render_backlog_markdown,
    render_critique_markdown,
    render_sow_diff,
    render_sow_markdown,
)
from .sow_pdf import render_sow_pdf

log = get_logger(__name__)


def _ref(kind: str, path: Path, label: str) -> ArtifactRef:
    return ArtifactRef(
        kind=kind,  # type: ignore[arg-type]
        path=str(path),
        label=label,
        size_bytes=path.stat().st_size if path.is_file() else 0,
    )


def project_code(title: str) -> str:
    """Derive a Jira-safe project code from the engagement title.

    Delegates to the offline engine's implementation so the CSV export and the live
    REST provisioning agree on the key they would create.
    """
    from ..llm.offline import project_key_from_title

    return project_key_from_title(title)


#: Every artefact this bundle can produce.
#:
#: Declared once so the UI's selection list and the exporter cannot disagree about what
#: exists. A kind the UI offers but this table omits would silently produce nothing.
ARTIFACT_KINDS: dict[str, str] = {
    "sow_markdown": "Statement of Work (Markdown)",
    "sow_pdf": "Statement of Work (PDF)",
    "backlog_markdown": "Delivery backlog (Markdown)",
    "jira_csv": "Jira-importable backlog (CSV)",
    "backlog_json": "Backlog (JSON)",
    "audit_markdown": "Quality audit (Markdown)",
    "report_json": "Run report (JSON)",
}


def _wanted(kind: str, kinds: set[str] | None) -> bool:
    """Whether this artefact was asked for. ``None`` means everything."""
    return kinds is None or kind in kinds


def _write(
    refs: list[ArtifactRef],
    *,
    kind: str,
    kinds: set[str] | None,
    target: Path,
    filename: str,
    label: str,
    event: str,
    body: Callable[[], Any],
    text: bool = True,
) -> None:
    """Render one artefact, if it was asked for, and record it.

    A helper rather than six near-identical try blocks. The selection check, the failure
    isolation and the logging were repeated for every artefact, which is how the first
    attempt at adding a filter dedented a block out of its parent: six copies of a
    structure is six chances to get the edit slightly wrong.

    ``text=False`` is for writers that take the path themselves, like the PDF renderer.
    ``body`` still returns something, and is called for its side effect.
    """
    if not _wanted(kind, kinds):
        return
    try:
        path = target / filename
        content = body()
        if text:
            path.write_text(str(content), encoding="utf-8")
        refs.append(_ref(kind, path, label))
    except Exception as exc:
        log.error(event, error=str(exc))


def render_deliverables(
    *,
    session_id: str,
    run_id: str,
    scope: dict[str, Any] | None,
    blueprint: dict[str, Any] | None,
    sow: dict[str, Any] | None,
    critique: dict[str, Any] | None,
    integrations: list[dict[str, Any]] | None,
    cost: dict[str, Any] | None,
    diff: SOWDiff | None = None,
    kinds: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Write the requested artefacts for a completed run and return their references.

    ``kinds=None`` writes everything; otherwise only the named kinds. Every artefact is
    rendered from objects already in memory, so deselecting one saves rendering time and
    disk rather than model tokens. That distinction is worth stating plainly, because a
    reader who unticks the PDF expecting a cheaper run would be misled about where the
    money actually goes — the audit is roughly 70% of it.

    Individual failures are isolated: a PDF rendering problem must not prevent the
    backlog CSV from being produced.
    """
    target = ARTIFACT_DIR / run_id
    target.mkdir(parents=True, exist_ok=True)

    refs: list[ArtifactRef] = []
    parsed_scope = _validate(RequirementScope, scope)
    parsed_blueprint = _validate(TechnicalBlueprint, blueprint)
    parsed_sow = _validate(SOWDocument, sow)
    parsed_critique = _validate(CritiqueReport, critique)
    code = project_code(parsed_sow.title if parsed_sow else "SOW")

    # ---------------------------------------------------------------- SOW markdown
    if parsed_sow is not None:
        _write(
            refs, kind="sow_markdown", kinds=kinds, target=target,
            filename="statement_of_work.md",
            label="Statement of Work (Markdown)",
            event="export.markdown_failed",
            body=lambda: (
                f"<!-- run_id: {run_id} · session: {session_id} · "
                f"generated: {datetime.utcnow().isoformat()}Z -->\n\n"
            )
            + render_sow_markdown(parsed_sow),
        )
        _write(
            refs, kind="sow_pdf", kinds=kinds, target=target,
            filename="statement_of_work.pdf",
            label="Statement of Work (PDF)",
            event="export.pdf_failed",
            body=lambda: render_sow_pdf(parsed_sow, target / "statement_of_work.pdf"),
            text=False,
        )

    # ---------------------------------------------------------------- backlog
    if parsed_blueprint is not None:
        _write(
            refs, kind="backlog_markdown", kinds=kinds, target=target,
            filename="backlog.md",
            label="Delivery backlog (Markdown)",
            event="export.backlog_failed",
            body=lambda: render_backlog_markdown(parsed_blueprint),
        )
        _write(
            refs, kind="jira_csv", kinds=kinds, target=target,
            filename="jira_backlog.csv",
            label="Jira-importable backlog (CSV)",
            event="export.csv_failed",
            body=lambda: render_jira_csv(parsed_blueprint, project_key=code),
        )
        _write(
            refs, kind="backlog_json", kinds=kinds, target=target,
            filename="backlog.json",
            label="Backlog (JSON)",
            event="export.json_failed",
            body=lambda: json.dumps(
                parsed_blueprint.model_dump(mode="json"), indent=2, default=str
            ),
        )

    # ---------------------------------------------------------------- audit report
    if parsed_critique is not None:
        _write(
            refs, kind="audit_markdown", kinds=kinds, target=target,
            filename="quality_audit.md",
            label="Quality audit (Markdown)",
            event="export.audit_failed",
            body=lambda: render_critique_markdown(parsed_critique),
        )

    # ---------------------------------------------------------------- revision diff
    if diff is not None:
        _write(
            refs, kind="revision_markdown", kinds=kinds, target=target,
            filename=f"revision_{diff.from_revision}_to_{diff.to_revision}.md",
            label=f"Revision diff {diff.from_revision} → {diff.to_revision}",
            event="export.diff_failed",
            body=lambda: render_sow_diff(diff),
        )

    # ---------------------------------------------------------------- run report
    _write(
        refs, kind="report_json", kinds=kinds, target=target,
        filename="run_report.json",
        label="Run report (JSON)",
        event="export.report_failed",
        body=lambda: json.dumps(
            {
                "run_id": run_id,
                "session_id": session_id,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "scope": scope,
                "blueprint_summary": _blueprint_summary(parsed_blueprint),
                "sow_summary": _sow_summary(parsed_sow),
                "critique": critique,
                "revision": (parsed_sow.revision if parsed_sow else None),
                "diff": diff.model_dump(mode="json") if diff else None,
                "integrations": integrations or [],
                "cost": cost or {},
            },
            indent=2,
            default=str,
        ),
    )

    log.info(
        "export.completed",
        run_id=run_id,
        artifacts=len(refs),
        directory=str(target),
        scope_present=parsed_scope is not None,
    )
    return [ref.model_dump(mode="json") for ref in refs]


def _blueprint_summary(blueprint: TechnicalBlueprint | None) -> dict[str, Any] | None:
    if blueprint is None:
        return None
    return {
        "milestones": len(blueprint.milestones),
        "epics": sum(len(m.epics) for m in blueprint.milestones),
        "stories": len(blueprint.all_stories()),
        "story_points": blueprint.estimated_total_points,
        "duration_weeks": blueprint.estimated_duration_weeks,
    }


def _sow_summary(sow: SOWDocument | None) -> dict[str, Any] | None:
    if sow is None:
        return None
    return {
        "title": sow.title,
        "jurisdiction": sow.jurisdiction.value,
        "clauses": len(sow.clauses),
        "words": sow.word_count(),
        "payment_milestones": len(sow.payment_schedule),
        "evidence_links": len(sow.retrieved_evidence_ids),
    }


def _validate(model: Any, payload: Any) -> Any:
    if not payload:
        return None
    try:
        return model.model_validate(payload)
    except Exception as exc:
        log.warning("export.validation_failed", model=model.__name__, error=str(exc))
        return None
