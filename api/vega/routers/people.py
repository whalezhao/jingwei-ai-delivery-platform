from __future__ import annotations

import random

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, hash_password, require_admin
from ..config import get_settings
from ..crypto import decrypt, encrypt
from ..db import get_db
from ..gitlab import GitlabClient
from ..models import GroupMember, Person, PersonGitlabAccount
from ..schemas import GitlabAccountIn, GitlabAccountOut, PersonIn, PersonOut, PersonUpd
from . import person_to_out

router = APIRouter(prefix="/api/people", tags=["people"])

# 内置头像 key，必须与 web/public/avatars/ 下的文件一一对应（不允许上传，故白名单校验）
AVATARS = {f"a{i}" for i in range(1, 13)}


def _apply(body: PersonIn | PersonUpd, person: Person) -> None:
    s = get_settings().secret_key
    data = body.model_dump(exclude_unset=True)
    for k in ("name", "username", "gitlab_username", "gitlab_email", "is_admin", "llm_base_url", "llm_model", "octo_uid"):
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
    if data.get("roles") is not None:  # 未传 = 不动；传空数组 = 清空角色
        person.roles = list(dict.fromkeys(data["roles"]))  # 去重保序

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
    # 兼容旧导入：没填登录名时优先沿用 gitlab_username
    person.username = person.username or person.gitlab_username or None
    if person.username and await db.scalar(select(Person).where(Person.username == person.username)):
        raise HTTPException(400, "登录名已存在")
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
        # 角色决定认领权限（产品/开发），自己改等于给自己提权，也只能管理员动。
        # 改密码必须走 /api/auth/password（校验旧密码），这里不带旧密码就能改会有绕过。
        # 判「真的改了」而不是「提交了」：前端回传整份表单时 disabled 的登录名和
        # is_admin 也在里面，按提交与否拒绝会让普通用户连姓名都改不了。
        data = body.model_dump(exclude_unset=True)
        for k in ("is_admin", "octo_uid", "gitlab_username", "username"):
            if k in data and data[k] is not None and data[k] != getattr(person, k):
                raise HTTPException(403, "无权修改该字段")
        if data.get("roles") is not None and list(data["roles"]) != list(person.roles or []):
            raise HTTPException(403, "无权修改角色")
        if body.password:
            raise HTTPException(403, "修改密码请走修改密码接口（需校验旧密码）")
    _apply(body, person)
    if "username" in body.model_fields_set:
        if not person.username:
            raise HTTPException(400, "登录名不能为空")
        dup = await db.scalar(
            select(Person).where(Person.username == person.username, Person.id != person.id)
        )
        if dup:
            raise HTTPException(400, "登录名已存在")
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
    # 账号没有级联：先解除组内绑定再删，否则外键挡成 500
    acc_ids = (await db.scalars(select(PersonGitlabAccount.id).where(PersonGitlabAccount.person_id == person_id))).all()
    if acc_ids:
        for gm in (
            await db.scalars(select(GroupMember).where(GroupMember.gitlab_account_id.in_(acc_ids)))
        ).all():
            gm.gitlab_account_id = None
        await db.execute(delete(PersonGitlabAccount).where(PersonGitlabAccount.person_id == person_id))
    await db.delete(person)
    await db.commit()
    return {"ok": True}


# ---------- 个人 GitLab 账号（一个人可多个；每个研发组选一个用） ----------


def _account_out(a: PersonGitlabAccount) -> dict:
    return {
        "id": a.id,
        "base_url": a.base_url,
        "gitlab_user_id": a.gitlab_user_id,
        "username": a.username,
        "name": a.name,
        "email": a.email,
        "has_token": bool(a.token_enc),
    }


async def _own_person(person_id: int, me: Person) -> Person:
    if not me.is_admin and me.id != person_id:
        raise HTTPException(403, "只能管理自己的 GitLab 账号")
    return me


@router.get("/{person_id}/gitlab-accounts", response_model=list[GitlabAccountOut])
async def list_gitlab_accounts(
    person_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> list[GitlabAccountOut]:
    await _own_person(person_id, me)
    rows = (
        await db.scalars(
            select(PersonGitlabAccount)
            .where(PersonGitlabAccount.person_id == person_id)
            .order_by(PersonGitlabAccount.id)
        )
    ).all()
    return [_account_out(a) for a in rows]


@router.post("/{person_id}/gitlab-accounts", response_model=GitlabAccountOut)
async def add_gitlab_account(
    person_id: int,
    body: GitlabAccountIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> GitlabAccountOut:
    await _own_person(person_id, me)
    base_url = body.base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "GitLab 地址必须是 http(s)")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as c:
            info = await GitlabClient(base_url, body.token).whoami(c)
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"token 校验失败（HTTP {e.response.status_code}）") from e
    except httpx.HTTPError as e:
        raise HTTPException(400, f"连不上 GitLab：{e}") from e
    uid = int(info["id"])
    holder = await db.scalar(
        select(PersonGitlabAccount).where(
            PersonGitlabAccount.base_url == base_url,
            PersonGitlabAccount.gitlab_user_id == uid,
        )
    )
    if holder and holder.person_id != person_id:
        raise HTTPException(400, "该 GitLab 账号已属于其他人")
    acc = holder or PersonGitlabAccount(person_id=person_id, base_url=base_url, gitlab_user_id=uid)
    acc.username = str(info.get("username") or "")
    acc.name = str(info.get("name") or "")
    acc.email = str(info.get("email") or "")
    acc.token_enc = encrypt(get_settings().secret_key, body.token)
    if not holder:
        db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return _account_out(acc)


@router.delete("/{person_id}/gitlab-accounts/{account_id}")
async def delete_gitlab_account(
    person_id: int,
    account_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    await _own_person(person_id, me)
    acc = await db.get(PersonGitlabAccount, account_id)
    if not acc or acc.person_id != person_id:
        raise HTTPException(404, "账号不存在")
    # 仍被某个研发组绑定时先解绑成员，否则外键挡住删除
    bound = await db.scalar(select(GroupMember).where(GroupMember.gitlab_account_id == acc.id).limit(1))
    if bound:
        bound.gitlab_account_id = None
    await db.delete(acc)
    await db.commit()
    return {"ok": True}
