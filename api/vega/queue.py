"""任务队列：入队 + runner 长轮询认领（sticky 路由 + PG/SQLite 双方言）。"""

from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .crypto import decrypt, encrypt
from .db import engine
from .models import RunnerTask, utcnow

# PG 用 SKIP LOCKED + ->>；SQLite 用 json_extract（dev 单写锁足够）
# 认领优先级：MR 初审有时效（MR 开着等结论）插队，方案会话次之，
# EGM 评审再次之，开发任务与项目级 EGM 任务（bootstrap/drift）垫底
# :rid 赋值给 integer 列、:rid_txt 参与 JSON 文本比较——同一个参数两处用会让 PG 只能推断出一种类型
_CLAIM_PG = """
UPDATE runner_task SET status='claimed', runner_id=:rid, claimed_at=:now
WHERE id = (
  SELECT id FROM runner_task
  WHERE status='queued'
    AND (payload->>'sticky_runner_id' IS NULL
         OR payload->>'sticky_runner_id' = :rid_txt)
  ORDER BY CASE type WHEN 'mr_review' THEN 0 WHEN 'plan_turn' THEN 1 WHEN 'egm_review' THEN 2 ELSE 3 END, id LIMIT 1
  FOR UPDATE SKIP LOCKED
) RETURNING id, type, payload, secrets
"""

_CLAIM_SQLITE = """
UPDATE runner_task SET status='claimed', runner_id=:rid, claimed_at=:now
WHERE id = (
  SELECT id FROM runner_task
  WHERE status='queued'
    AND (json_extract(payload,'$.sticky_runner_id') IS NULL
         OR json_extract(payload,'$.sticky_runner_id') = :rid)
  ORDER BY CASE type WHEN 'mr_review' THEN 0 WHEN 'plan_turn' THEN 1 WHEN 'egm_review' THEN 2 ELSE 3 END, id LIMIT 1
) RETURNING id, type, payload, secrets
"""

# 入队后唤醒长轮询：200ms 轮询代替 asyncio.Condition（跨事件循环安全，延迟可忽略）
_POLL_S = 0.2


async def enqueue(db: AsyncSession, secrets: dict | None = None, **fields) -> RunnerTask:
    """secrets（git_token / claude_env 等）加密单存，payload 保持可明文落库。"""
    if secrets:
        fields["secrets"] = encrypt(get_settings().secret_key, json.dumps(secrets, ensure_ascii=False))
    task = RunnerTask(**fields)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def claim_next(
    runner_id: int,
    wait_s: float = 25.0,
    alive: Callable[[], Awaitable[bool]] | None = None,
) -> dict | None:
    """长轮询认领一条任务；wait_s 内无任务返回 None。

    alive 返回 False（客户端连接已断开）时立即放弃：
    否则 runner 重启后，遗留在服务端的轮询协程会把任务抢给死连接，任务永久卡在 claimed。
    """
    deadline = asyncio.get_event_loop().time() + wait_s
    is_pg = engine().dialect.name == "postgresql"
    stmt = text(_CLAIM_PG if is_pg else _CLAIM_SQLITE)
    while True:
        if alive is not None and not await alive():
            return None
        async with engine().begin() as conn:
            row = (
                await conn.execute(stmt, {"rid": runner_id, "rid_txt": str(runner_id), "now": utcnow()})
            ).first()
        if row:
            payload = row[2]
            if isinstance(payload, str):  # SQLite JSON 列原生返回字符串
                payload = json.loads(payload)
            sec = row[3]
            if sec:  # 秘密只在认领响应里解密并入，不回写 DB payload
                payload.update(json.loads(decrypt(get_settings().secret_key, sec)))
            return {"id": row[0], "type": row[1], "payload": payload}
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(_POLL_S)


def encode_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def stale_threshold(minutes: int = 60) -> datetime.datetime:
    return utcnow() - datetime.timedelta(minutes=minutes)


_REQUEUE_DEAD = """
UPDATE runner_task SET status='queued', runner_id=NULL, claimed_at=NULL
WHERE status IN ('claimed', 'running')
  AND runner_id IN (
    SELECT id FROM runner_node
    WHERE last_heartbeat IS NULL OR last_heartbeat < :cutoff
  )
"""


async def requeue_dead_nodes(dead_minutes: int = 3) -> int:
    """认领节点心跳已停的任务放回队列（runner 崩溃/被杀的兜底）。

    只看节点心跳不看任务时长：活 runner 的长任务可能合法跑几十分钟。
    sticky_runner_id 保留——节点只是重启的话回来还能续会话。
    """
    async with engine().begin() as conn:
        r = await conn.execute(text(_REQUEUE_DEAD), {"cutoff": stale_threshold(dead_minutes)})
        return r.rowcount
