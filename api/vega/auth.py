"""登录认证：PBKDF2 口令 + HMAC 签名 token（无状态，stdlib 实现）。

bootstrap：首次启动若无 admin，用 settings.admin_username/password 建一个。
登录名 = person.gitlab_username。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import Person

_PBKDF2_ITERS = 60_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERS)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


def sign_token(person_id: int, ttl_s: int = 7 * 24 * 3600) -> str:
    s = get_settings()
    exp = int(time.time()) + ttl_s
    body = f"{person_id}.{exp}"
    sig = hmac.new(s.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def parse_token(token: str) -> int | None:
    s = get_settings()
    try:
        pid_s, exp_s, sig = token.split(".")
        body = f"{pid_s}.{exp_s}"
        expect = hmac.new(s.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect) or int(exp_s) < time.time():
            return None
        return int(pid_s)
    except (ValueError, TypeError):
        return None


async def current_person(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> Person:
    token = authorization.removeprefix("Bearer ").strip()
    pid = parse_token(token)
    if pid is None:
        raise HTTPException(401, "未登录或 token 失效")
    person = await db.get(Person, pid)
    if not person:
        raise HTTPException(401, "用户不存在")
    return person


async def require_admin(person: Person = Depends(current_person)) -> Person:
    if not person.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return person


async def bootstrap_admin(db: AsyncSession) -> None:
    s = get_settings()
    existing = await db.scalar(select(Person).where(Person.is_admin.is_(True)))
    if existing:
        return
    admin = Person(
        name=s.admin_username,
        gitlab_username=s.admin_username,
        password_hash=hash_password(s.admin_password),
        is_admin=True,
    )
    db.add(admin)
    await db.commit()


async def login(db: AsyncSession, username: str, password: str) -> tuple[Person, str]:
    person = await db.scalar(select(Person).where(Person.gitlab_username == username))
    if not person or not verify_password(password, person.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return person, sign_token(person.id)
