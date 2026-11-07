import json
import uuid

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import ai_case
from ..ai_fix import propose_case_fix
from ..case_versions import diff_text, rollback_case, snapshot_case
from ..db import SessionLocal
from ..deps import CurrentPerson, DbSession, require_case_access, require_feature_access
from ..modelconfig import missing_fields
from ..models import (
    CaseResult,
    SystemModelConfig,
    Run,
    RunBatch,
    TestCase,
    TestCaseVersion,
)
from ..quota import ensure_quota, record_usage
from ..schemas import (
    AiFixIn,
    AiFixOut,
    CaseDiffOut,
    CaseDraftIn,
    CaseDraftOut,
    CaseVersionOut,
    TestCaseCreate,
    TestCaseOut,
    TestCaseUpdate,
    YamlValidateIn,
    YamlValidateOut,
)
from ..yamlcheck import validate as validate_yaml

router = APIRouter(tags=["cases"])


async def _draft_ready(feature_id, user, db):
    """两条生成路径（一次性 / 流式）共用的前置校验。"""
    feature = await require_feature_access(db, feature_id, user)
    config = await db.get(SystemModelConfig, feature.system_id)
    gaps = missing_fields(config)
    if gaps:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"该系统还没配好大模型（缺：{'、'.join(gaps)}），无法生成用例。"
            "请先到「系统详情 → 大模型配置」补齐。",
        )
    try:
        await ensure_quota(db, feature.system_id, config)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return feature, config


@router.post("/api/features/{feature_id}/ai/case-draft", response_model=CaseDraftOut)
async def draft_case_with_ai(
    feature_id: uuid.UUID, body: CaseDraftIn, user: CurrentPerson, db: DbSession
) -> CaseDraftOut:
    """通过对话生成用例 YAML。

    用的是**系统上配好的那个模型** —— 和跑用例是同一份配置，所以「这个系统用哪个模型」
    仍然只有一个答案。系统没配好时同样拦住，并说清缺什么。
    """
    feature, config = await _draft_ready(feature_id, user, db)

    try:
        result = await ai_case.draft(
            db,
            config,
            history=[m.model_dump() for m in body.messages],
            current_yaml=body.current_yaml,
            feature_name=feature.name,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _describe_http_error(exc)) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"连不上模型服务（{config.base_url}）：{exc.__class__.__name__}。请检查接入地址与网络。",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await record_usage(db, feature.system_id, "case_draft", result.usage)
    await db.commit()

    return CaseDraftOut(
        reply=result.reply,
        yaml=result.yaml_text,
        yaml_ok=result.yaml_ok,
        yaml_errors=result.yaml_errors,
        usage=result.usage,
    )


@router.post("/api/features/{feature_id}/ai/case-draft/stream")
async def draft_case_with_ai_stream(
    feature_id: uuid.UUID, body: CaseDraftIn, user: CurrentPerson, db: DbSession
):
    """流式版：边生成边显示，等模型的时间从「干等 30 秒」变成「看着它写」。

    事件：delta（增量文本）/ done（结构化结果，形状同 CaseDraftOut）/ error。
    """
    feature, config = await _draft_ready(feature_id, user, db)

    async def events():
        async for item in ai_case.draft_stream(
            config,
            history=[m.model_dump() for m in body.messages],
            current_yaml=body.current_yaml,
            feature_name=feature.name,
        ):
            if item["event"] == "done":
                tokens = int((item["data"].get("usage") or {}).get("total_tokens") or 0)
                if tokens:
                    # 生成器在响应生命周期里跑，依赖注入的 db 会话不保证还活着，
                    # 用一条独立会话记流水
                    async with SessionLocal() as usage_db:
                        await record_usage(
                            usage_db,
                            feature.system_id,
                            "case_draft",
                            {"total_tokens": tokens},
                        )
                        await usage_db.commit()
            yield (
                f"event: {item['event']}\n"
                f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 之类的反代会缓冲 SSE，显式关掉
            "X-Accel-Buffering": "no",
        },
    )


def _describe_http_error(exc: httpx.HTTPStatusError) -> str:
    """把模型服务的 HTTP 错误翻译成用户能照做的话。

    和执行链路上的失败归因是同一个道理：额度用尽、密钥不对、模型名写错，
    这些都不是「我问得不对」，要明说而不是抛一个裸的 500。
    """
    code = exc.response.status_code
    body = exc.response.text[:300]
    if code == 429 or "usage limit" in body or "quota" in body:
        return "模型调用额度已用尽（上游返回 429）。请更换模型或等额度恢复。"
    if code == 401:
        return "模型拒绝了请求：API Key 无效或已过期。"
    if code == 404:
        return "模型服务返回 404：模型名或接入地址可能不对。"
    return f"模型服务返回 {code}：{body}"


@router.post("/api/cases/validate", response_model=YamlValidateOut)
async def validate_body(body: YamlValidateIn, user: CurrentPerson) -> YamlValidateOut:
    """编辑器里实时校验用 —— 不落库、不调 AI，纯本地解析。"""
    errors, case_count = validate_yaml(body.yaml_content)
    return YamlValidateOut(ok=not errors, errors=errors, case_count=case_count)


@router.get("/api/features/{feature_id}/cases", response_model=list[TestCaseOut])
async def list_cases(
    feature_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> list[TestCaseOut]:
    await require_feature_access(db, feature_id, user)
    stmt = (
        select(TestCase)
        .options(selectinload(TestCase.feature))  # effective_stage 需要
        .where(TestCase.feature_id == feature_id)
        .order_by(TestCase.priority.nullslast(), TestCase.name)
    )
    cases = list((await db.execute(stmt)).scalars())
    if not cases:
        return []
    versions = dict(
        (
            await db.execute(
                select(TestCaseVersion.case_id, func.max(TestCaseVersion.version_no))
                .where(TestCaseVersion.case_id.in_([c.id for c in cases]))
                .group_by(TestCaseVersion.case_id)
            )
        ).all()
    )
    out = []
    for case in cases:
        item = TestCaseOut.model_validate(case)
        item.version_no = versions.get(case.id)
        out.append(item)
    return out


@router.post(
    "/api/features/{feature_id}/cases",
    response_model=TestCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_case(
    feature_id: uuid.UUID, body: TestCaseCreate, user: CurrentPerson, db: DbSession
) -> TestCaseOut:
    feature = await require_feature_access(db, feature_id, user, "member")
    errors, _ = validate_yaml(body.yaml_content)
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))
    exists = (
        await db.execute(
            select(TestCase.id).where(
                TestCase.feature_id == feature_id, TestCase.name == body.name
            )
        )
    ).first()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该功能下已有同名用例")
    data = body.model_dump(exclude={"change_source", "change_note"})
    case = TestCase(feature_id=feature_id, created_by=user.id, **data)
    db.add(case)
    await db.flush()
    version = await snapshot_case(
        db,
        case,
        source="create",
        note=body.change_note,
        user_id=user.id,
    )
    await db.commit()
    case.feature = feature
    out = TestCaseOut.model_validate(case)
    out.version_no = version.version_no
    return out


@router.get("/api/cases/{case_id}", response_model=TestCaseOut)
async def get_case(case_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> TestCaseOut:
    case = await require_case_access(db, case_id, user)
    out = TestCaseOut.model_validate(case)
    out.version_no = (
        await db.execute(
            select(func.max(TestCaseVersion.version_no)).where(
                TestCaseVersion.case_id == case.id
            )
        )
    ).scalar()
    return out


@router.patch("/api/cases/{case_id}", response_model=TestCaseOut)
async def update_case(
    case_id: uuid.UUID, body: TestCaseUpdate, user: CurrentPerson, db: DbSession
) -> TestCaseOut:
    existing = await require_case_access(db, case_id, user, "member")
    feature = existing.feature
    case = (
        await db.execute(
            select(TestCase)
            .options(selectinload(TestCase.feature))
            .where(TestCase.id == case_id)
            .with_for_update()
        )
    ).scalar_one()
    data = body.model_dump(exclude_unset=True, exclude={"change_source", "change_note"})
    if "yaml_content" in data:
        errors, _ = validate_yaml(data["yaml_content"])
        if errors:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))
    if "stage_override" in data and data["stage_override"] not in {
        None,
        "in_iteration",
        "released",
    }:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法分期")
    if "name" in data and data["name"] != case.name:
        duplicate = (
            await db.execute(
                select(TestCase.id).where(
                    TestCase.feature_id == case.feature_id,
                    TestCase.name == data["name"],
                    TestCase.id != case.id,
                )
            )
        ).first()
        if duplicate is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "该功能下已有同名用例")
    source = body.change_source or "manual"
    if source not in {"manual", "ai_fix"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法版本来源")
    for field, value in data.items():
        setattr(case, field, value)
    version = await snapshot_case(
        db, case, source=source, note=body.change_note, user_id=user.id
    )
    await db.commit()
    case.feature = feature
    out = TestCaseOut.model_validate(case)
    out.version_no = version.version_no
    return out


@router.delete("/api/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> None:
    case = await require_case_access(db, case_id, user, "member")
    await db.delete(case)
    await db.commit()


@router.get("/api/cases/{case_id}/versions", response_model=list[CaseVersionOut])
async def list_versions(
    case_id: uuid.UUID,
    user: CurrentPerson,
    db: DbSession,
    limit: int = 50,
    offset: int = 0,
) -> list[TestCaseVersion]:
    case = await require_case_access(db, case_id, user)
    return list(
        (
            await db.execute(
                select(TestCaseVersion)
                .where(TestCaseVersion.case_id == case.id)
                .order_by(TestCaseVersion.version_no.desc())
                .limit(max(1, min(limit, 200)))
                .offset(max(0, offset))
            )
        ).scalars()
    )


@router.get("/api/cases/{case_id}/versions/diff", response_model=CaseDiffOut)
async def version_diff(
    case_id: uuid.UUID,
    user: CurrentPerson,
    db: DbSession,
    from_version: int,
    to_version: int | None = None,
) -> CaseDiffOut:
    case = await require_case_access(db, case_id, user)
    source = (
        await db.execute(
            select(TestCaseVersion).where(
                TestCaseVersion.case_id == case.id,
                TestCaseVersion.version_no == from_version,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "起始版本不存在")
    if to_version is None:
        target_yaml = case.yaml_content
        target_no = (
            await db.execute(
                select(func.max(TestCaseVersion.version_no)).where(
                    TestCaseVersion.case_id == case.id
                )
            )
        ).scalar() or 0
    else:
        target = (
            await db.execute(
                select(TestCaseVersion).where(
                    TestCaseVersion.case_id == case.id,
                    TestCaseVersion.version_no == to_version,
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "目标版本不存在")
        target_yaml = target.yaml_content
        target_no = target.version_no
    return CaseDiffOut(
        from_version=source.version_no,
        to_version=int(target_no),
        diff=diff_text(
            source.yaml_content,
            target_yaml,
            f"v{source.version_no}",
            f"v{target_no}",
        ),
    )


@router.get(
    "/api/cases/{case_id}/versions/{version_id}", response_model=CaseVersionOut
)
async def get_version(
    case_id: uuid.UUID, version_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> TestCaseVersion:
    case = await require_case_access(db, case_id, user)
    version = await db.get(TestCaseVersion, version_id)
    if version is None or version.case_id != case.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在")
    return version


@router.post(
    "/api/cases/{case_id}/versions/{version_id}/rollback",
    response_model=CaseVersionOut,
)
async def rollback_to_version(
    case_id: uuid.UUID, version_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> TestCaseVersion:
    existing = await require_case_access(db, case_id, user, "member")
    case = (
        await db.execute(
            select(TestCase)
            .options(selectinload(TestCase.feature))
            .where(TestCase.id == case_id)
            .with_for_update()
        )
    ).scalar_one()
    version = await db.get(TestCaseVersion, version_id)
    if version is None or version.case_id != case.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在")
    errors, _ = validate_yaml(version.yaml_content)
    if errors:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "该版本 YAML 已不符合当前校验规则：" + "; ".join(errors),
        )
    try:
        new_version = await rollback_case(db, case, version, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    case.feature = existing.feature
    return new_version


@router.post("/api/cases/{case_id}/ai-fix", response_model=AiFixOut)
async def ai_fix(
    case_id: uuid.UUID, body: AiFixIn, user: CurrentPerson, db: DbSession
) -> AiFixOut:
    case = await require_case_access(db, case_id, user, "member")
    try:
        proposal = await propose_case_fix(db, case, run_id=body.run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await db.commit()
    return AiFixOut(
        run_id=proposal.run_id,
        proposed_yaml=proposal.proposed_yaml,
        explanation=proposal.explanation,
        diff=proposal.diff,
        yaml_ok=proposal.yaml_ok,
        yaml_errors=proposal.yaml_errors,
        model_name=proposal.model_name,
    )


@router.get("/api/cases/{case_id}/history")
async def case_history(
    case_id: uuid.UUID, user: CurrentPerson, db: DbSession, limit: int = 30
) -> list[dict]:
    """该用例的历史执行结果。

    带上系统与批次，看的人才知道「这次跑的是哪儿、属于哪次执行」——
    只有一个时间和通过与否，判断不了结果是不是自己关心的那次。
    """
    await require_case_access(db, case_id, user)
    stmt = (
        select(
            CaseResult,
            Run,
            System.name,
            RunBatch.id,
            RunBatch.name,
        )
        .join(Run, Run.id == CaseResult.run_id)
        .join(System, System.id == Run.system_id)
        .outerjoin(RunBatch, RunBatch.id == Run.batch_id)
        .where(
            CaseResult.test_case_id == case_id,
            Run.status.in_(("succeeded", "failed")),
        )
        .order_by(Run.started_at.desc().nullslast())
        .limit(min(limit, 200))
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "run_id": str(run.id),
            "batch_id": str(batch_id) if batch_id else None,
            "batch_name": batch_name,
            "system_name": system_name,
            "status": result.status,
            "attempt_count": result.attempt_count,
            "passed_after_retry": result.passed_after_retry,
            "run_status": run.status,
            "failure_kind": run.failure_kind,
            "model_name": run.model_name,
            "duration_ms": run.duration_ms,
            "report_available": bool(run.report_html_path),
            "started_at": run.started_at.isoformat() if run.started_at else None,
        }
        for result, run, system_name, batch_id, batch_name in rows
    ]
