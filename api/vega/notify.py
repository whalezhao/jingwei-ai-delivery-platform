"""通知层：octo 群消息 + @ + notify_log 留痕。octo 未配置时只留痕。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import octo
from .config import get_settings
from .crypto import decrypt
from .models import DevGroup, NotifyLog, Person, ProjectOwner


async def project_owners(db: AsyncSession, project_id: int) -> list[Person]:
    """项目负责人：MR/评审相关通知除任务创建人外一并 @ 的人。"""
    return list(
        (
            await db.scalars(
                select(Person)
                .join(ProjectOwner, ProjectOwner.person_id == Person.id)
                .where(ProjectOwner.project_id == project_id)
                .order_by(ProjectOwner.id)  # 顺序稳定：MR 只能挂第一个有 GitLab 账号的人
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
