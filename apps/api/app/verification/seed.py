"""Load the baseline requirement registry without resetting statuses that tests have already earned."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AcceptanceCriterion, Requirement, TestCase


def seed_requirements(db: Session) -> int:
    directory = get_settings().resolved_requirements_dir
    requirements = json.loads((directory / "requirements.json").read_text(encoding="utf-8"))
    criteria = json.loads((directory / "acceptance-criteria.json").read_text(encoding="utf-8"))
    created = 0
    for item in requirements:
        existing = db.scalar(select(Requirement).where(Requirement.requirement_key == item["id"]))
        if existing is None:
            existing = Requirement(
                requirement_key=item["id"],
                title=item["title"],
                description=item.get("description", ""),
                category=item.get("category", "general"),
                priority=item.get("priority", "mandatory"),
                scope=item.get("scope", "platform"),
                status="not_started",
                dependencies=item.get("dependencies", []),
                related_modules=item.get("related_modules", []),
                implementation_refs=item.get("implementation_refs", []),
                blockers=[],
            )
            db.add(existing)
            db.flush()
            created += 1
        else:
            existing.title = item["title"]
            existing.description = item.get("description", existing.description)
            existing.implementation_refs = item.get("implementation_refs", existing.implementation_refs)
            existing.related_modules = item.get("related_modules", existing.related_modules)
    for item in criteria:
        requirement = db.scalar(select(Requirement).where(Requirement.requirement_key == item["requirement_id"]))
        if requirement is None:
            continue
        existing = db.scalar(select(AcceptanceCriterion).where(AcceptanceCriterion.criterion_key == item["id"]))
        if existing is None:
            db.add(
                AcceptanceCriterion(
                    requirement_id=requirement.id,
                    criterion_key=item["id"],
                    description=item["description"],
                    verification_method=item.get("verification_method", "automated_test"),
                    mandatory=item.get("mandatory", True),
                    requires_evidence=item.get("requires_evidence", True),
                    status="not_tested",
                )
            )
            db.add(
                TestCase(
                    requirement_id=requirement.id,
                    name=item["id"],
                    preconditions=item.get("preconditions", "A running test environment."),
                    steps=item.get("steps", []),
                    expected_result=item["description"],
                )
            )
    db.commit()
    return created
