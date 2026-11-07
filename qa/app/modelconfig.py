"""系统级模型配置的判定与执行门禁。

「系统配置了模型」的口径只有这一处定义，UI 展示和执行放行都读它 ——
否则会出现「页面显示已配置、点执行却被拒绝」这种自相矛盾的状态。
"""

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import System, SystemModelConfig

REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("model_name", "模型名"),
    ("base_url", "接入地址"),
    ("api_key", "API Key"),
)
"""一个项目要能真正跑起来，必须齐的字段。

没有全局兜底之后，「只填了模型名」这种半套配置是跑不起来的，所以判定必须要求齐全，
不能沿用「覆盖了任意字段就算配过」的宽松口径 —— 那会让页面显示「已配置」但点执行就报错。

模型族（model_family）不在必填里：实测 midscene 不填也能正常跑（三项自检全过），
它只影响解析细节，属于可选优化。"""


def configured_condition() -> ColumnElement[bool]:
    """一个系统算「已配置模型」的 SQL 条件。"""
    return and_(
        *[getattr(SystemModelConfig, field).is_not(None) for field, _ in REQUIRED_FIELDS]
    )


def configured_system_ids_stmt():
    return select(SystemModelConfig.system_id).where(configured_condition())


def missing_fields(row: SystemModelConfig | None) -> list[str]:
    """还缺哪些必填项，用于在界面上说清楚「差什么」而不是笼统说「没配置」。"""
    return [
        label
        for field, label in REQUIRED_FIELDS
        if row is None or not getattr(row, field, None)
    ]


def row_configured(row: SystemModelConfig | None) -> bool:
    """单行的 Python 侧判定，语义必须和上面的 configured_condition 一致。"""
    return not missing_fields(row)


async def assert_systems_configured(
    db: AsyncSession, system_ids: set[int] | list[int]
) -> None:
    """执行前的门禁：这些系统必须都配了模型，否则拒绝。

    为什么不允许「没配就用全局」：全局配置只是填空默认值，一个系统用哪个模型
    应该是个明确的决定 —— 静默继承会让「这个系统的用例跑在哪个模型上」变成没人知道的事。
    """
    ids = set(system_ids)
    if not ids:
        return
    configured = set((await db.execute(configured_system_ids_stmt())).scalars())
    missing = ids - configured
    if not missing:
        return

    rows = (
        await db.execute(select(System).where(System.id.in_(missing)))
    ).scalars()
    configs = {
        row.system_id: row
        for row in (
            await db.execute(
                select(SystemModelConfig).where(SystemModelConfig.system_id.in_(missing))
            )
        ).scalars()
    }
    # 说清楚是「没配」还是「缺哪几项」—— 后者用户自己看不出来
    details = []
    for system in rows:
        gaps = missing_fields(configs.get(system.id))
        details.append(f"{system.name}（缺：{'、'.join(gaps)}）")

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"以下系统还没配置好大模型：{'；'.join(details)}。"
        "请到「系统详情 → 大模型配置」补齐后再执行。",
    )
