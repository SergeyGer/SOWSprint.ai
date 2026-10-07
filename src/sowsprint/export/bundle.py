"""Deliverable bundle assembly.

``render_deliverables`` is the single place that decides what a finished run produces.
Every artefact is written under ``data/artifacts/<run_id>/`` and described by an
:class:`~sowsprint.models.ArtifactRef` so the UI can offer it for download without
knowing anything about file formats.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import ARTIFACT_DIR
from ..models import (
    ArtifactRef,
    CritiqueReport,
    RequirementScope,
    SOWDocument,
    TechnicalBlueprint,
)
from ..observability.logging import get_logger
from .jira_csv import render_jira_csv
from .sow_markdown import render_backlog_markdown, render_critique_markdown, render_sow_markdown
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
) -> list[dict[str, Any]]:
    """Write every artefact for a completed run and return their references.

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
        try:
            path = target / "statement_of_work.md"
            header = (
                f"<!-- run_id: {run_id} · session: {session_id} · "
                f"generated: {datetime.utcnow().isoformat()}Z -->\n\n"
            )
            path.write_text(header + render_sow_markdown(parsed_sow), encoding="utf-8")
            refs.append(_ref("sow_markdown", path, "Statement of Work (Markdown)"))
        except Exception as exc:
            log.error("export.markdown_failed", error=str(exc))

        try:
            path = target / "statement_of_work.pdf"
            render_sow_pdf(parsed_sow, path)
            refs.append(_ref("sow_pdf", path, "Statement of Work (PDF)"))
        except Exception as exc:
            log.error("export.pdf_failed", error=str(exc))

    # ---------------------------------------------------------------- backlog
    if parsed_blueprint is not None:
        try:
            path = target / "backlog.md"
            path.write_text(render_backlog_markdown(parsed_blueprint), encoding="utf-8")
            refs.append(_ref("backlog_markdown", path, "Delivery backlog (Markdown)"))
        except Exception as exc:
            log.error("export.backlog_failed", error=str(exc))

        try:
            path = target / "jira_backlog.csv"
            path.write_text(render_jira_csv(parsed_blueprint, project_key=code), encoding="utf-8")
            refs.append(_ref("jira_csv", path, "Jira-importable backlog (CSV)"))
        except Exception as exc:
            log.error("export.csv_failed", error=str(exc))

        try:
            path = target / "backlog.json"
            path.write_text(
                json.dumps(parsed_blueprint.model_dump(mode="json"), indent=2, default=str),
                encoding="utf-8",
            )
            refs.append(_ref("backlog_json", path, "Backlog (JSON)"))
        except Exception as exc:
            log.error("export.json_failed", error=str(exc))

    # ---------------------------------------------------------------- audit report
    if parsed_critique is not None:
        try:
            path = target / "quality_audit.md"
            path.write_text(render_critique_markdown(parsed_critique), encoding="utf-8")
            refs.append(_ref("audit_markdown", path, "Critic quality audit"))
        except Exception as exc:
            log.error("export.audit_failed", error=str(exc))

    # ---------------------------------------------------------------- run report
    try:
        path = target / "run_report.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "scope": scope,
                    "blueprint_summary": _blueprint_summary(parsed_blueprint),
                    "sow_summary": _sow_summary(parsed_sow),
                    "critique": critique,
                    "integrations": integrations or [],
                    "cost": cost or {},
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        refs.append(_ref("report_json", path, "Run report (JSON)"))
    except Exception as exc:
        log.error("export.report_failed", error=str(exc))

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
