"""Requirement status follows executed tests and evidence. It is not set by the client."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AcceptanceCriterion, Evidence, Requirement, TestExecution
from app.models.entities import utcnow


VERIFIED_STATES = {"verified"}
IMPLEMENTED_STATES = {"implemented", "partially_verified", "verified", "failed", "needs_review"}


def set_status(requirement: Requirement, status: str, reason: str) -> None:
    if requirement.status == status:
        return
    history = list(requirement.change_history or [])
    history.append({"at": utcnow().isoformat(), "from": requirement.status, "to": status, "reason": reason})
    requirement.change_history = history
    requirement.status = status
    requirement.version = (requirement.version or 1) + 1
    requirement.updated_at = utcnow()


def record_execution(
    db: Session,
    *,
    requirement_key: str,
    criterion_key: str,
    result: str,
    actual_result: str,
    environment: str,
    evidence_summary: str = "",
    evidence_details: dict | None = None,
    error_detail: str = "",
    storage_key: str = "",
    organization_id: str | None = None,
    project_id: str | None = None,
) -> TestExecution:
    requirement = db.scalar(select(Requirement).where(Requirement.requirement_key == requirement_key))
    if requirement is None:
        raise ValueError(f"Unknown requirement {requirement_key}")
    criterion = db.scalar(select(AcceptanceCriterion).where(AcceptanceCriterion.criterion_key == criterion_key))
    if criterion is None or criterion.requirement_id != requirement.id:
        raise ValueError(f"Unknown criterion {criterion_key}")
    evidence_ids: list[str] = []
    if evidence_summary or storage_key:
        evidence = Evidence(
            organization_id=organization_id or requirement.organization_id,
            project_id=project_id or requirement.project_id,
            requirement_id=requirement.id,
            kind="test_result",
            storage_key=storage_key,
            summary=evidence_summary or actual_result,
            details=evidence_details or {},
        )
        db.add(evidence)
        db.flush()
        evidence_ids.append(evidence.id)
    execution = TestExecution(
        requirement_id=requirement.id,
        criterion_id=criterion.id,
        expected_result=criterion.description,
        actual_result=actual_result,
        environment=environment,
        result=result,
        error_detail=error_detail,
        evidence_ids=evidence_ids,
        steps=["Recorded by the server after the check ran."],
    )
    db.add(execution)
    db.flush()
    if evidence_ids:
        evidence = db.get(Evidence, evidence_ids[0])
        if evidence is not None:
            evidence.test_execution_id = execution.id
    _refresh_criterion(db, criterion)
    evaluate_requirement(db, requirement)
    return execution


def _refresh_criterion(db: Session, criterion: AcceptanceCriterion) -> None:
    latest = db.scalar(
        select(TestExecution)
        .where(TestExecution.criterion_id == criterion.id)
        .order_by(TestExecution.executed_at.desc())
    )
    if latest is None:
        criterion.status = "not_tested"
        return
    if latest.result == "passed" and criterion.requires_evidence and not latest.evidence_ids:
        criterion.status = "not_tested"
        return
    criterion.status = latest.result


def evaluate_requirement(db: Session, requirement: Requirement) -> str:
    criteria = list(
        db.scalars(select(AcceptanceCriterion).where(AcceptanceCriterion.requirement_id == requirement.id)).all()
    )
    mandatory = [item for item in criteria if item.mandatory] or criteria
    if requirement.blockers:
        set_status(requirement, "blocked", "Blockers are still recorded.")
        return requirement.status
    if not mandatory:
        set_status(requirement, "needs_review", "No acceptance criteria are defined.")
        return requirement.status
    statuses = [item.status for item in mandatory]
    if any(item == "failed" for item in statuses):
        set_status(requirement, "failed", "A mandatory acceptance criterion failed.")
        return requirement.status
    if any(item == "blocked" for item in statuses):
        set_status(requirement, "blocked", "A mandatory acceptance criterion is blocked.")
        return requirement.status
    if all(item == "passed" for item in statuses):
        set_status(requirement, "verified", "All mandatory acceptance criteria passed with evidence.")
        return requirement.status
    if any(item == "passed" for item in statuses):
        set_status(requirement, "partially_verified", "Some mandatory acceptance criteria passed.")
        return requirement.status
    set_status(requirement, "not_started", "No mandatory acceptance criterion has passed.")
    return requirement.status


def coverage(db: Session, organization_id: str | None = None) -> dict:
    query = select(Requirement)
    if organization_id:
        query = query.where(
            (Requirement.organization_id == None) | (Requirement.organization_id == organization_id)  # noqa: E711
        )
    requirements = list(db.scalars(query).all())
    status_counts: dict[str, int] = {}
    for requirement in requirements:
        status_counts[requirement.status] = status_counts.get(requirement.status, 0) + 1
    requirement_ids = [item.id for item in requirements]
    criteria = []
    if requirement_ids:
        criteria = list(
            db.scalars(select(AcceptanceCriterion).where(AcceptanceCriterion.requirement_id.in_(requirement_ids))).all()
        )
    executions = []
    if requirement_ids:
        executions = list(
            db.scalars(select(TestExecution).where(TestExecution.requirement_id.in_(requirement_ids))).all()
        )
    evidence_count = 0
    required_evidence = 0
    for criterion in criteria:
        if not criterion.requires_evidence:
            continue
        required_evidence += 1
        latest = next((item for item in reversed(executions) if item.criterion_id == criterion.id), None)
        if latest and latest.evidence_ids:
            evidence_count += 1
    passed = sum(1 for item in executions if item.result == "passed")
    executed = sum(1 for item in executions if item.result != "skipped")
    tested_criteria = sum(1 for item in criteria if item.status != "not_tested")
    return {
        "total_requirements": len(requirements),
        "requirements_by_status": status_counts,
        "implemented_requirements": sum(1 for item in requirements if item.status in IMPLEMENTED_STATES),
        "verified_requirements": status_counts.get("verified", 0),
        "failed_requirements": status_counts.get("failed", 0),
        "blocked_requirements": status_counts.get("blocked", 0),
        "untested_requirements": sum(
            1
            for item in requirements
            if item.status in {"not_started", "in_progress"}
        ),
        "acceptance_criteria_coverage": {"tested": tested_criteria, "total": len(criteria)},
        "test_pass_rate": {"passed": passed, "executed": executed},
        "evidence_coverage": {"with_evidence": evidence_count, "required": required_evidence},
        "verification_coverage": {"verified": status_counts.get("verified", 0), "total": len(requirements)},
    }
