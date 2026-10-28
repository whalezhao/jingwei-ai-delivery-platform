from __future__ import annotations

import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, hash_password, require_admin
from ..config import get_settings
from ..crypto import encrypt
from ..db import get_db
from ..models import GroupMember, Person
from ..schemas import PersonIn, PersonOut, PersonUpd
from . import person_to_out

router = APIRouter(prefix="/api/people", tags=["people"])

# 内置头像 key，必须与 web/public/avatars/ 下的文件一一对应（不允许上传，故白名单校验）
AVATARS = {f"a{i}" for i in range(1, 13)}


def _apply(body: PersonIn | PersonUpd, person: Person) -> None:
    s = get_settings().secret_key
    data = body.model_dump(exclude_unset=True)
    for k in ("name", "gitlab_username", "gitlab_email", "is_admin", "llm_base_url", "llm_model", "octo_uid"):
        if k in data and data[k] is not None:
            setattr(person, k, data[k])
    if "avatar" in data:  # 空串/null = 清除
        av = data["avatar"] or None
        if av and av not in AVATARS:
            raise HTTPException(400, "无效头像")
        person.avatar = av
    if data.get("password"):
        person.password_hash = hash_password(data["password"])
    if "llm_api_key" in data:
        person.llm_api_key_enc = encrypt(s, data["llm_api_key"]) if data["llm_api_key"] else None
    if "llm_effort" in data:  # 空串/null = 清除（继承下一级）
        person.llm_effort = data["llm_effort"] or None
    if "am_user_key" in data:
        person.am_user_key_enc = encrypt(s, data["am_user_key"]) if data["am_user_key"] else None


@router.get("", response_model=list[PersonOut])
async def list_people(
    group_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[PersonOut]:
    q = select(Person).order_by(Person.id)
    if group_id:
        # 只看该组成员（(group_id, person_id) 唯一，不会重复）
        q = q.join(GroupMember, GroupMember.person_id == Person.id).where(GroupMember.group_id == group_id)
    rows = (await db.scalars(q)).all()
    return [person_to_out(p) for p in rows]


@router.post("", response_model=PersonOut)
async def create_person(
    body: PersonIn, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> PersonOut:
    person = Person()
    _apply(body, person)
    # 新建时不指定头像就随机给一张（_apply 里显式清空会覆盖模型默认值）
    person.avatar = person.avatar or random.choice(sorted(AVATARS))
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return person_to_out(person)


@router.put("/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: int,
    body: PersonUpd,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> PersonOut:
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "人员不存在")
    if not me.is_admin and me.id != person_id:
        raise HTTPException(403, "只能改自己")
    if not me.is_admin:
        # 自己能改：姓名/邮箱/头像/LLM 配置/am key。登录名是身份匹配与登录依据、
        # octo 绑定决定通知去向、管理员标记是权限，都只能管理员动；
        # 改密码必须走 /api/auth/password（校验旧密码），这里不带旧密码就能改会有绕过。
        if any(getattr(body, k) is not None for k in ("is_admin", "octo_uid", "gitlab_username")):
            raise HTTPException(403, "无权修改该字段")
        if body.password is not None:
            raise HTTPException(403, "修改密码请走修改密码接口（需校验旧密码）")
    _apply(body, person)
    await db.commit()
    return person_to_out(person)


@router.delete("/{person_id}")
async def delete_person(
    person_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    if person_id == admin.id:
        raise HTTPException(400, "不能删除自己")
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "人员不存在")
    await db.delete(person)
    await db.commit()
    return {"ok": True}
