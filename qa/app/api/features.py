"""系统下的功能（测试资产的第一层）。"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..deps import (
    CurrentPerson,
    DbSession,
    require_feature_access,
    require_system_access,
    writable_system_ids,
)
from ..models import Feature, Requirement, System, TestCase
from ..schemas import FeatureCreate, FeatureOut, FeatureUpdate

router = APIRouter(tags=["features"])


async def _check_requirement(db, system_id: int, requirement_id: int | None) -> None:
    """功能只能关联本系统的需求，否则测试入口会指到别人的系统上。"""
    if requirement_id is None:
        return
    requirement = await db.get(Requirement, requirement_id)
    if requirement is None or requirement.system_id != system_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "需求不存在或不属于该系统")


@router.get("/api/features")
async def list_all_features(
    user: CurrentPerson,
    db: DbSession,
    q: str | None = None,
) -> list[dict]:
    """跨系统功能检索，供「新建测试任务」跨系统选择功能。

    无写权限的系统仍然返回（can_manage=false），让用户看得到资产但选不进任务。
    """
    stmt = select(Feature, System.name).join(System, System.id == Feature.system_id)
    if q:
        stmt = stmt.where(Feature.name.ilike(f"%{q}%"))
    rows = (
        await db.execute(
            stmt.order_by(System.name, Feature.stage, Feature.name).limit(1000)
        )
    ).all()
    allowed = await writable_system_ids(db, user)
    return [
        {
            **FeatureOut.model_validate(feature).model_dump(mode="json"),
            "system_name": system_name,
            "can_manage": allowed is None or feature.system_id in allowed,
        }
        for feature, system_name in rows
    ]


@router.get("/api/systems/{system_id}/features", response_model=list[FeatureOut])
async def list_features(
    system_id: int,
    user: CurrentPerson,
    db: DbSession,
    stage: str | None = None,
) -> list[Feature]:
    await require_system_access(db, system_id, user)
    stmt = select(Feature).where(Feature.system_id == system_id)
    if stage:
        stmt = stmt.where(Feature.stage == stage)
    stmt = stmt.order_by(Feature.stage, Feature.name)
    return list((await db.execute(stmt)).scalars())


@router.post(
    "/api/systems/{system_id}/features",
    response_model=FeatureOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_feature(
    system_id: int, body: FeatureCreate, user: CurrentPerson, db: DbSession
) -> Feature:
    await require_system_access(db, system_id, user, "member")
    await _check_requirement(db, system_id, body.requirement_id)
    exists = (
        await db.execute(
            select(Feature.id).where(
                Feature.system_id == system_id, Feature.name == body.name
            )
        )
    ).first()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该系统下已有同名功能")
    feature = Feature(system_id=system_id, created_by=user.id, **body.model_dump())
    db.add(feature)
    await db.commit()
    await db.refresh(feature)
    return feature


@router.get("/api/features/{feature_id}", response_model=FeatureOut)
async def get_feature(feature_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> Feature:
    return await require_feature_access(db, feature_id, user)


@router.patch("/api/features/{feature_id}", response_model=FeatureOut)
async def update_feature(
    feature_id: uuid.UUID, body: FeatureUpdate, user: CurrentPerson, db: DbSession
) -> Feature:
    feature = await require_feature_access(db, feature_id, user, "member")
    data = body.model_dump(exclude_unset=True)
    if "requirement_id" in data:
        await _check_requirement(db, feature.system_id, data["requirement_id"])
    for field, value in data.items():
        setattr(feature, field, value)
    await db.commit()
    await db.refresh(feature)
    return feature


@router.delete("/api/features/{feature_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feature(feature_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> None:
    feature = await require_feature_access(db, feature_id, user, "admin")
    await db.delete(feature)
    await db.commit()


@router.get("/api/systems/{system_id}/features/case-counts")
async def feature_case_counts(
    system_id: int, user: CurrentPerson, db: DbSession
) -> dict[str, int]:
    """功能列表页要显示每个功能下挂了多少用例，一次查完避免 N+1。"""
    await require_system_access(db, system_id, user)
    stmt = (
        select(TestCase.feature_id, func.count(TestCase.id))
        .join(Feature, Feature.id == TestCase.feature_id)
        .where(Feature.system_id == system_id, TestCase.is_active.is_(True))
        .group_by(TestCase.feature_id)
    )
    return {str(fid): count for fid, count in (await db.execute(stmt)).all()}