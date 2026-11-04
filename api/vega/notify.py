"""通知层：octo 群消息 + @ + notify_log 留痕。octo 未配置时只留痕。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import octo
from .config import get_settings
from .crypto import decrypt
from .models import DevGroup, NotifyLog, Person, Project, SystemOwner


async def project_owners(db: AsyncSession, system_id: int) -> list[Person]:
    """系统负责人：MR/评审相关通知除任务创建人外一并 @ 的人。"""
    return list(
        (
            await db.scalars(
                select(Person)
                .join(SystemOwner, SystemOwner.person_id == Person.id)
                .where(SystemOwner.system_id == system_id)
                .order_by(SystemOwner.id)  # 顺序稳定：MR 只能挂第一个有 GitLab 账号的人
            )
        ).all()
    )


def merge_mentions(*persons: Person | None) -> list[Person]:
    """按人员 id 去重，避免同一个人被 @ 两次。"""
    seen: set[int] = set()
    out: list[Person] = []
    for p in persons:
        if p and p.id not in seen:
            seen.add(p.id)
            out.append(p)
    return out


async def _uids_for(persons: list[Person]) -> list[str]:
    return [p.octo_uid for p in persons if p and p.octo_uid]


async def notify(
    db: AsyncSession,
    group: DevGroup,
    content: str,
    *,
    reason: str,
    mention: list[Person] | None = None,
) -> bool:
    chat = group.octo_group_no
    uids = await _uids_for(mention or [])
    token = decrypt(get_settings().secret_key, group.octo_bot_token_enc)  # 组 bot 优先，None 落全局
    ok = octo.send_text(chat, content, uids, token=token) if chat else False
    db.add(
        NotifyLog(
            group_id=group.id,
            octo_chat=chat,
            mentioned_uids=uids,
            content=content,
            reason=reason,
            ok=ok,
        )
    )
    await db.commit()
    return ok


async def notify_project(
    db: AsyncSession,
    project: Project,
    content: str,
    *,
    reason: str,
    mention: list[Person] | None = None,
    source_group: DevGroup,
) -> bool:
    """项目级通知：发到项目 octo 群（成员是各研发组负责人）。

    @ 的对象必须是群成员才有效，所以调用方传研发组负责人，不要传总负责人。
    """
    chat = project.octo_group_no
    uids = await _uids_for(mention or [])
    # 项目群跨研发组，任何一组的专用 bot 都未必在群里；统一用全局 bot
    ok = octo.send_text(chat, content, uids) if chat else False
    db.add(
        NotifyLog(
            group_id=source_group.id,
            project_id=project.id,
            octo_chat=chat,
            mentioned_uids=uids,
            content=content,
            reason=reason,
            ok=ok,
        )
    )
    await db.commit()
    return ok
