from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, hash_password, login as do_login, verify_password
from ..db import get_db
from ..models import Person
from ..schemas import LoginIn, PasswordChangeIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)) -> TokenOut:
    person, token = await do_login(db, body.username, body.password)
    return TokenOut(token=token, person_id=person.id, is_admin=person.is_admin, roles=person.roles or [])


@router.post("/password")
async def change_password(
    body: PasswordChangeIn, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    if not me.password_hash or not verify_password(body.old, me.password_hash):
        raise HTTPException(400, "旧密码不正确")
    me.password_hash = hash_password(body.new)
    await db.commit()
    return {"ok": True}
