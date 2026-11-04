"""代码统计聚合与日报/周报/月报渲染（数据来自 DailyStat / FileStat 每日落库）。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm import resolve_llm
from .llm_client import ask_llm
from .models import CommitFile, DailyStat, DevGroup, FileStat, Person, System

log = logging.getLogger(__name__)

TOP_N = 10
SINGLE_POINT = 0.8  # 单个系统里一人提交占比超过它 = 单点风险
SINGLE_POINT_MIN = 3  # 样本太少不判定
REVISIT_COMMITS = 3  # 单文件区间内被改几次 = 反复修改
REVISIT_DAYS = 3  # 或横跨几个不同日期
CHURN_LINES = 3000  # 单人单日增删行数阈值

KIND_LABEL = {"daily": "日报", "weekly": "周报", "monthly": "月报"}


def report_range(kind: str, today: date | None = None) -> tuple[date, date]:
    """报表统计区间：日报=昨天，周报=近 7 天，月报=上一个自然月。"""
    today = today or date.today()
    if kind == "weekly":
        end = today - timedelta(days=1)
        return end - timedelta(days=6), end
    if kind == "monthly":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    return today - timedelta(days=1), today - timedelta(days=1)


async def aggregate(
    db: AsyncSession,
    *,
    group_id: int | None,
    system_id: int | None,
    start: date,
    end: date,
    person_id: int | None = None,
) -> dict:
    """区间聚合：人员榜、项目分布、热点文件、按日趋势。日报与统计页共用。

    person_id 给定时只统计这个人：文件统计没有人员维度，人员视角下不取热点文件。
    """
    dq = select(DailyStat).where(DailyStat.stat_date >= start, DailyStat.stat_date <= end)
    if group_id:
        dq = dq.where(DailyStat.group_id == group_id)
    if system_id:
        dq = dq.where(DailyStat.system_id == system_id)
    if person_id:
        dq = dq.where(DailyStat.person_id == person_id)
    rows = list((await db.scalars(dq)).all())

    files: list[FileStat] = []
    if not person_id:
        fq = select(FileStat).where(FileStat.stat_date >= start, FileStat.stat_date <= end)
        if group_id:
            fq = fq.where(FileStat.group_id == group_id)
        if system_id:
            fq = fq.where(FileStat.system_id == system_id)
        files = list((await db.scalars(fq)).all())

    people = list((await db.scalars(select(Person))).all())
    by_id = {p.id: p.name for p in people}
    systems = list((await db.scalars(select(System))).all())
    path_of = {p.id: p.path for p in systems}

    ppl: dict = defaultdict(lambda: {"commits": 0, "additions": 0, "deletions": 0})
    prj: dict = defaultdict(lambda: {"commits": 0, "additions": 0, "deletions": 0})
    prj_person: dict = defaultdict(int)  # (项目, 人) → 提交数：查「一个系统是不是一直一个人提交」
    day: dict = defaultdict(lambda: {"commits": 0, "additions": 0, "deletions": 0})
    hot: dict = defaultdict(lambda: {"commits": 0, "additions": 0, "deletions": 0, "_days": set()})
    churn: dict = defaultdict(int)  # (人, 日) → 增删行
    unmatched: set[str] = set()
    for r in rows:
        key = r.person_id if r.person_id else f"?{r.gitlab_author}"
        if not r.person_id:
            unmatched.add(r.gitlab_author)
        for bucket in (ppl[key], prj[r.system_id], day[r.stat_date]):
            bucket["commits"] += r.commits
            bucket["additions"] += r.additions
            bucket["deletions"] += r.deletions
        prj_person[(r.system_id, key)] += r.commits
        churn[(key, r.stat_date)] += r.additions + r.deletions
    for f in files:
        slot = hot[(f.system_id, f.path)]
        slot["commits"] += f.commits
        slot["additions"] += f.additions
        slot["deletions"] += f.deletions
        slot["_days"].add(f.stat_date)

    def _sorted(d: dict) -> list[tuple]:
        return sorted(
            d.items(), key=lambda kv: (-kv[1]["commits"], -(kv[1]["additions"] + kv[1]["deletions"]))
        )

    people_out = []
    for k, v in _sorted(ppl):
        name = by_id.get(k) if isinstance(k, int) else k[1:]
        people_out.append({"person_id": k if isinstance(k, int) else None, "name": name, "matched": isinstance(k, int), **v})
    systems_out = [
        {"system_id": pid, "path": path_of.get(pid, str(pid)), **v} for pid, v in _sorted(prj)
    ]
    system_people = [
        {
            "system_id": pid,
            "path": path_of.get(pid, str(pid)),
            "person_id": k if isinstance(k, int) else None,
            "name": by_id.get(k, str(k)) if isinstance(k, int) else k[1:],
            "commits": n,
        }
        for (pid, k), n in prj_person.items()
    ]
    hot_out = [
        {
            "system_id": pid,
            "system": path_of.get(pid, str(pid)),
            "path": path,
            **{k: v for k, v in slot.items() if k != "_days"},
            "days": len(slot["_days"]),
            "last": str(max(slot["_days"])),
        }
        for (pid, path), slot in sorted(
            hot.items(), key=lambda kv: (-kv[1]["commits"], -(kv[1]["additions"] + kv[1]["deletions"]))
        )
    ]
    daily_out = [{"date": str(d), **v} for d, v in sorted(day.items())]

    return {
        "totals": {
            "commits": sum(v["commits"] for v in ppl.values()),
            "additions": sum(v["additions"] for v in ppl.values()),
            "deletions": sum(v["deletions"] for v in ppl.values()),
            "people": sum(1 for k in ppl if isinstance(k, int)),
            "systems": len(prj),
        },
        "people": people_out,
        "systems": systems_out,
        "system_people": system_people,
        "hot_files": hot_out,
        "daily": daily_out,
        "unmatched": sorted(unmatched),
        "spikes": [
            {"name": by_id.get(k, str(k)) if isinstance(k, int) else k[1:], "date": str(d), "lines": n}
            for (k, d), n in sorted(churn.items(), key=lambda kv: -kv[1])
            if n >= CHURN_LINES
        ][:5],
    }


def _top_revisit(agg: dict, n: int = 3) -> list[dict]:
    """反复修改的候选：同一文件改得多，或连着几天都在动它。"""
    hits = [
        f
        for f in agg["hot_files"]
        if f["commits"] >= REVISIT_COMMITS or f["days"] >= REVISIT_DAYS
    ]
    return sorted(hits, key=lambda x: (-x["commits"], -x["days"]))[:n]


def _risks(agg: dict, msgs: dict | None = None) -> list[str]:
    """规则风险：只看「改得可疑」的模式（反复改同一处、一个系统单点、单日大进大出）。

    「没提交」不是风险，不列。msgs 给了提交信息就把「在改什么」写在同一条里。
    """
    out: list[str] = []

    # 反复修改：同一文件被改了很多次，或连着几天都在动它
    for f in _top_revisit(agg):
        span = f"在 {f['days']} 天里" if f["days"] > 1 else "单日"
        line = (
            f"{f['path']} {span}被改 {f['commits']} 次（+{f['additions']}/-{f['deletions']}），"
            f"最近 {f['last']}"
        )
        notes = (msgs or {}).get((f["system_id"], f["path"]))
        if notes:
            line += "；近几次：" + " / ".join(notes)
        else:
            line += "，建议看下为什么反复动这里"
        out.append(line)

    # 单点项目：一个系统的提交几乎全出自同一个人
    by_prj: dict[int, list[dict]] = defaultdict(list)
    for row in agg["system_people"]:
        by_prj[row["system_id"]].append(row)
    solo = []
    for rows in by_prj.values():
        n = sum(r["commits"] for r in rows)
        top = max(rows, key=lambda r: r["commits"])
        if n >= SINGLE_POINT_MIN and top["commits"] / n >= SINGLE_POINT:
            solo.append((top["commits"] / n, n, top, rows[0]["path"]))
    for ratio, n, top, path in sorted(solo, key=lambda x: -x[0])[:3]:
        out.append(f"{path} 的 {n} 次提交里 {top['commits']} 次来自 {top['name']}（{ratio:.0%}），单点风险")

    if agg["spikes"]:
        s = agg["spikes"][0]
        out.append(f"{s['name']} 在 {s['date']} 单日改动 {s['lines']} 行，建议抽查")
    return out[:6]


async def build_code_report(
    db: AsyncSession, group: DevGroup, start: date, end: date, kind: str
) -> str:
    """渲染一条可直接发群的代码报表（总量 → 排行 → 热点 → 风险 → LLM 研判）。"""
    label = KIND_LABEL.get(kind, kind)
    span = str(start) if start == end else f"{start} ~ {end}"
    agg = await aggregate(db, group_id=group.id, system_id=None, start=start, end=end)
    total = agg["totals"]
    head = f"📊 {group.name} 代码{label}（{span}）"
    if not total["commits"]:
        return f"{head}\n区间内无提交记录"

    lines = [
        head,
        f"提交 {total['commits']} · +{total['additions']}/-{total['deletions']} · "
        f"{total['people']} 人 / {total['systems']} 个系统",
        "",
        "🏆 提交排行",
    ]
    matched = [p for p in agg["people"] if p["matched"]][:TOP_N]
    lines += [
        f"{i + 1}. {p['name']} {p['commits']} 次（+{p['additions']}/-{p['deletions']}）"
        for i, p in enumerate(matched)
    ] or ["（暂无匹配到人员的提交）"]

    lines += ["", "🔥 热点文件"]
    lines += [
        f"{i + 1}. {f['path']} · {f['system']}（{f['commits']} 次，+{f['additions']}/-{f['deletions']}）"
        for i, f in enumerate(agg["hot_files"][:TOP_N])
    ] or ["（暂无文件级数据）"]

    # 反复改的文件，把最近几次提交信息带上，回答「到底在改什么」
    msgs: dict[tuple[int, str], list[str]] = {}
    for f in _top_revisit(agg):
        rows = (
            await db.scalars(
                select(CommitFile.message)
                .where(
                    CommitFile.system_id == f["system_id"],
                    CommitFile.path == f["path"],
                    CommitFile.stat_date >= start,
                    CommitFile.stat_date <= end,
                    ~CommitFile.message.like("Merge %"),  # 合并提交看不出改了什么
                )
                .order_by(CommitFile.stat_date.desc(), CommitFile.id.desc())
                .limit(3)
            )
        ).all()
        got = [m if len(m) <= 36 else m[:36].rstrip() + "…" for m in reversed(rows) if m]
        if got:
            msgs[(f["system_id"], f["path"])] = got
    risks = _risks(agg, msgs)
    if risks:
        lines += ["", "⚠️ 风险提示"] + [f"· {r}" for r in risks]

    summary = await _llm_summary(group, head, total, matched, agg["hot_files"][:5], risks)
    if summary:
        lines += ["", "🤖 研判", summary]
    return "\n".join(lines)


async def _llm_summary(
    group: DevGroup, head: str, total: dict, people: list[dict], hot: list[dict], risks: list[str]
) -> str:
    """给了 LLM 就写一段研判；没配或失败就整段省略，不阻塞推送。"""
    facts = [
        head,
        f"提交 {total['commits']}，+{total['additions']}/-{total['deletions']}，{total['people']} 人，{total['systems']} 个系统",
        "排行：" + "；".join(f"{p['name']} {p['commits']} 次" for p in people[:5]),
        "热点：" + "；".join(f"{f['path']} {f['commits']} 次" for f in hot),
        "风险：" + "；".join(risks) if risks else "风险：无",
    ]
    try:
        target = resolve_llm(None, group, person_first=False)
        return await ask_llm(
            "\n".join(facts) + "\n\n请基于以上数据写 2-3 句研发风险研判，指出最需要关注的项目/人/文件，不要重复罗列数字。",
            system="你是研发效能分析助手，回答精炼，中文。",
            target=target,
            group=group,
            max_tokens=400,
        )
    except Exception as e:  # noqa: BLE001 研判是锦上添花，网络/上游任何失败都不能拖垮日报
        log.warning("code report llm failed for %s: %s", group.name, e)
        return ""
