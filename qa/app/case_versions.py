"""用例版本快照、diff 与回滚。"""

import difflib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TestCase, TestCaseVersion

DIFF_LIMIT = 20_000


def diff_text(old: str, new: str, from_label: str, to_label: str) -> str:
    lines = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
        )
    )
    text = "\n".join(lines)
    if len(text) > DIFF_LIMIT:
        return text[:DIFF_LIMIT] + "\n...（差异过长，已截断）"
    return text


async def next_version_no(db: AsyncSession, case_id: uuid.UUID) -> int:
    current = (
        await db.execute(
            select(func.max(TestCaseVersion.version_no)).where(
                TestCaseVersion.case_id == case_id
            )
        )
    ).scalar()
    return int(current or 0) + 1


async def snapshot_case(
    db: AsyncSession,
    case: TestCase,
    *,
    source: str,
    note: str | None = None,
    user_id: uuid.UUID | None = None,
) -> TestCaseVersion:
    version = TestCaseVersion(
        case_id=case.id,
        version_no=await next_version_no(db, case.id),
        name=case.name,
        description=case.description,
        yaml_content=case.yaml_content,
        priority=case.priority,
        defect_ids=case.defect_ids,
        stage_override=case.stage_override,
        is_active=case.is_active,
        change_source=source,
        change_note=note,
        created_by=user_id,
    )
    db.add(version)
    await db.flush()
    return version


async def rollback_case(
    db: AsyncSession,
    case: TestCase,
    version: TestCaseVersion,
    *,
    user_id: uuid.UUID | None = None,
) -> TestCaseVersion:
    conflict = (
        await db.execute(
            select(TestCase.id).where(
                TestCase.feature_id == case.feature_id,
                TestCase.name == version.name,
                TestCase.id != case.id,
            )
        )
    ).first()
    if conflict is not None:
        raise ValueError("该功能下已有同名用例，无法回滚到这个版本")

    case.name = version.name
    case.description = version.description
    case.yaml_content = version.yaml_content
    case.priority = version.priority
    case.defect_ids = version.defect_ids
    case.stage_override = version.stage_override
    case.is_active = version.is_active
    return await snapshot_case(
        db,
        case,
        source="rollback",
        note=f"回滚到 v{version.version_no}",
        user_id=user_id,
    )
