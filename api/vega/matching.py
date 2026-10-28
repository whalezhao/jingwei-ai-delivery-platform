"""octo↔GitLab 成员三段式对配（DESIGN §4.2）。

① 规则精确匹配 ② AI 对配（组 LLM，置信度≥0.8 生效）③ 人工调整（manual 锁定）。
人工锁定（manual_lock）在任何自动对配中永不被覆盖。
"""

from __future__ import annotations

import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from .llm import resolve_llm
from .llm_client import LLMError, ask_llm
from .models import DevGroup, GroupMember, Person

AI_CONFIDENCE_THRESHOLD = 0.8

_AI_SYSTEM = (
    "你是研发团队的身份匹配助手。给你两组名单：A 是团队人员（含 GitLab 用户名/邮箱/姓名），"
    "B 是 IM 群成员（含昵称）。判断 A 中每人和 B 中哪个是同一个人。"
    '只输出 JSON：{"pairs": [{"person_id": int, "octo_uid": "…", "confidence": 0.0~1.0, "reason": "一句话"}]}，'
    "不确定的不要配。"
)


def _norm(s: str) -> str:
    return re.sub(r"[\s_.\-()（）]", "", s or "").lower()


def _email_prefix(email: str | None) -> str:
    return _norm((email or "").split("@")[0])


def rule_match(person: Person, octo_members: dict[str, str]) -> tuple[str, float, str] | None:
    """① 规则层：username/姓名/邮箱前缀 与群昵称的归一化比较。"""
    names = {
        _norm(n)
        for n in (person.gitlab_username, person.name, _email_prefix(person.gitlab_email))
        if n
    }
    for uid, nickname in octo_members.items():
        nn = _norm(nickname)
        if nn and nn in names:
            return uid, 1.0, "规则精确匹配"
    return None


async def ai_match(
    group: DevGroup,
    people: list[Person],
    octo_members: dict[str, str],
) -> dict[int, tuple[str, float, str]]:
    """② AI 层：返回 {person_id: (octo_uid, confidence, reason)}。"""
    if not people or not octo_members:
        return {}
    a = [
        {"person_id": p.id, "name": p.name, "gitlab_username": p.gitlab_username, "email": p.gitlab_email}
        for p in people
    ]
    b = [{"octo_uid": uid, "nickname": nm} for uid, nm in octo_members.items()]
    prompt = f"A（团队人员）：{json.dumps(a, ensure_ascii=False)}\nB（群成员）：{json.dumps(b, ensure_ascii=False)}"
    target = resolve_llm(None, group, person_first=False)
    try:
        raw = await ask_llm(prompt, system=_AI_SYSTEM, target=target, group=group, max_tokens=1024)
        data = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    except (LLMError, ValueError, json.JSONDecodeError):
        return {}
    out: dict[int, tuple[str, float, str]] = {}
    for pair in data.get("pairs", []):
        try:
            pid, uid = int(pair["person_id"]), str(pair["octo_uid"])
            conf = float(pair.get("confidence", 0))
            if uid in octo_members and pid in {p.id for p in people}:
                out[pid] = (uid, conf, str(pair.get("reason", ""))[:500])
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def run_matching(
    db: AsyncSession, group: DevGroup, gm_rows: list[GroupMember], octo_members: dict[str, str]
) -> dict:
    """整轮对配入口。gm_rows 由调用方 selectinload(person) 后传入，octo_members 为实时群成员。"""
    if not octo_members:
        return {"error": "群成员拉取失败或群未配置"}
    if not gm_rows:
        return {"auto_exact": 0, "auto_ai": 0, "pending": 0}

    people = {gm.person_id: gm.person for gm in gm_rows if gm.person}

    # 手工锁定的先排除（其绑定视为已占用）
    locked = {gm.person_id for gm in gm_rows if gm.manual_lock and gm.octo_uid}
    used_uids = {gm.octo_uid for gm in gm_rows if gm.octo_uid and gm.manual_lock}

    stats = {"auto_exact": 0, "auto_ai": 0, "pending": 0, "unmatched": 0, "kept_manual": len(locked)}
    gm_by_pid = {gm.person_id: gm for gm in gm_rows}

    # ① 规则层
    leftovers: list[Person] = []
    for pid, person in people.items():
        gm = gm_by_pid[pid]
        if pid in locked:
            continue
        hit = rule_match(person, {u: n for u, n in octo_members.items() if u not in used_uids})
        if hit:
            uid, conf, reason = hit
            gm.octo_uid, gm.octo_nickname = uid, octo_members[uid]
            gm.match_status, gm.match_confidence, gm.match_reason = "auto_exact", conf, reason
            person.octo_uid = uid
            used_uids.add(uid)
            stats["auto_exact"] += 1
        else:
            leftovers.append(person)

    # ② AI 层
    if leftovers:
        ai = await ai_match(group, leftovers, {u: n for u, n in octo_members.items() if u not in used_uids})
        for person in leftovers:
            gm = gm_by_pid[person.id]
            hit = ai.get(person.id)
            if hit and hit[1] >= AI_CONFIDENCE_THRESHOLD:
                uid, conf, reason = hit
                gm.octo_uid, gm.octo_nickname = uid, octo_members[uid]
                gm.match_status, gm.match_confidence, gm.match_reason = "auto_ai", conf, reason
                person.octo_uid = uid
                used_uids.add(uid)
                stats["auto_ai"] += 1
            elif hit:
                gm.octo_uid, gm.octo_nickname = hit[0], octo_members[hit[0]]
                gm.match_status, gm.match_confidence, gm.match_reason = "pending", hit[1], hit[2]
                stats["pending"] += 1
            else:
                gm.octo_uid, gm.octo_nickname, gm.match_status = None, None, "unmatched"
                stats["unmatched"] += 1

    await db.commit()
    return stats
