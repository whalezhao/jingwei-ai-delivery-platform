"""代码统计：采集落库、聚合与报表渲染、升级收件人优先级。"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from vega.gitlab import GitlabClient
from vega.jobs import _group_tech_leads, _owner_targets, _tech_leads, collect_day, daily_commit_stats
from vega.models import CommitFile, DailyStat, FileStat, GroupTechLead, Person, SystemOwner, SystemPM
from vega.reports import aggregate, build_code_report


async def test_tech_leads_project_then_group(db, seeded):
    """技术负责人项目级优先；项目没配才落到组级。"""
    proj, group = seeded["project"], seeded["group"]
    assert [p.name for p in await _tech_leads(db, proj, group)] == ["张三"]

    lead = Person(name="组技术负责人", gitlab_username=f"lead-{group.id}", octo_uid="uid-lead")
    db.add(lead)
    await db.flush()
    db.add(GroupTechLead(group_id=group.id, person_id=lead.id))
    await db.commit()
    assert [p.name for p in await _group_tech_leads(db, group)] == ["组技术负责人"]
    assert [p.name for p in await _tech_leads(db, proj, group)] == ["张三"]  # 项目级在场，组级不参与

    for row in (await db.scalars(select(SystemPM).where(SystemPM.system_id == proj.id))).all():
        await db.delete(row)
    await db.commit()
    assert [p.name for p in await _tech_leads(db, proj, group)] == ["组技术负责人"]


async def test_owner_targets_owner_then_tech_lead(db, seeded):
    """卡 MR 一级升级：项目负责人优先，没配直接落技术负责人。"""
    proj, group = seeded["project"], seeded["group"]
    assert [p.name for p in await _owner_targets(db, proj, group)] == ["张三"]  # 没负责人 → 技术负责人

    owner = Person(name="项目负责人乙", gitlab_username=f"owner-{group.id}", octo_uid="uid-owner")
    db.add(owner)
    await db.flush()
    db.add(SystemOwner(system_id=proj.id, person_id=owner.id))
    await db.commit()
    assert [p.name for p in await _owner_targets(db, proj, group)] == ["项目负责人乙"]


async def test_aggregate_and_report(db, seeded):
    """排行/热点/风险都从库里算；LLM 失败只少一段研判，不挡正文。"""
    proj, group, person = seeded["project"], seeded["group"], seeded["person"]
    day = date(2026, 10, 20)
    db.add(
        DailyStat(
            group_id=group.id, stat_date=day, system_id=proj.id, person_id=person.id,
            gitlab_author="zhangsan@test.cn", commits=6, additions=100, deletions=20,
        )
    )
    db.add(
        DailyStat(
            group_id=group.id, stat_date=day, system_id=proj.id, person_id=None,
            gitlab_author="ghost@x.cn", commits=1, additions=5, deletions=1,
        )
    )
    for back, msg in enumerate(["修复空指针", "补充日志", "调整超时"]):  # 同一文件连改三天 = 反复修改
        db.add(
            FileStat(
                group_id=group.id, stat_date=day - timedelta(days=back), system_id=proj.id,
                path="src/core/x.py", commits=4, additions=100, deletions=13,
            )
        )
        db.add(
            CommitFile(
                group_id=group.id, stat_date=day - timedelta(days=back), system_id=proj.id,
                path="src/core/x.py", sha=f"sha{back}", author="zhangsan@test.cn",
                message=msg, additions=100, deletions=13,
            )
        )
    db.add(
        CommitFile(
            group_id=group.id, stat_date=day, system_id=proj.id, path="src/core/x.py",
            sha="sha-merge", author="zhangsan@test.cn",
            message="Merge branch 'release/1.9.3' into 'master'", additions=0, deletions=0,
        )
    )
    await db.commit()

    agg = await aggregate(db, group_id=group.id, system_id=None, start=day, end=day)
    assert agg["totals"] == {"commits": 7, "additions": 105, "deletions": 21, "people": 1, "systems": 1}
    assert agg["people"][0]["name"] == "张三" and agg["people"][0]["commits"] == 6
    assert agg["people"][1]["matched"] is False and agg["people"][1]["name"] == "ghost@x.cn"
    assert agg["hot_files"][0]["path"] == "src/core/x.py"
    assert agg["unmatched"] == ["ghost@x.cn"]

    text = await build_code_report(db, group, day - timedelta(days=2), day, "weekly")
    assert "代码周报" in text and "张三 6 次" in text
    assert "热点文件" in text and "src/core/x.py" in text
    assert "风险提示" in text
    # 反复修改：3 天里改 12 次，带日期和最近几次在改什么
    assert "src/core/x.py 在 3 天里被改 12 次" in text and f"最近 {day}" in text
    assert "近几次：调整超时 / 补充日志 / 修复空指针" in text  # 按时间正序：最早的在前
    assert "Merge branch" not in text  # 合并提交不进风险
    # 单点项目：7 次里 6 次是同一人
    assert "team/repo 的 7 次提交里 6 次来自 张三（86%），单点风险" in text
    # 未匹配作者和「零提交」都不再当风险
    assert "ghost@x.cn" not in text and "零提交" not in text
    assert "研判" not in text  # conftest 让 LLM 直接失败


async def test_daily_commit_stats_hot_files_and_rerun(db, seeded, monkeypatch):
    """同日重跑不炸也不重复计数；热点文件按 diff 行数累计。"""
    proj, group, person = seeded["project"], seeded["group"], seeded["person"]
    day = date(2026, 10, 21)

    async def fake_commits(self, client_, pid, since=None, until=None):
        # 邮箱域名对不上、只有前缀=GitLab 账号：也要匹配到人（工号@企业域 的常见情况）
        return [
            {
                "id": "sha1",
                "author_email": f"{person.gitlab_username}@other.cn",
                "message": "修复任务重复打开\n\n详细说明不该入库",
                "stats": {"additions": 10, "deletions": 2},
            }
        ]

    async def fake_diff(self, client_, pid, sha):
        return [{"new_path": "src/a.py", "diff": "+x\n+y\n-old\n"}]

    monkeypatch.setattr(GitlabClient, "project_commits", fake_commits)
    monkeypatch.setattr(GitlabClient, "commit_diff", fake_diff)

    sent: dict = {}

    async def fake_notify(db_, group_, content, *, reason, mention=None):
        sent.update(content=content, reason=reason, mention=mention or [])

    monkeypatch.setattr("vega.jobs.notify", fake_notify)

    await daily_commit_stats(db, group, day)
    await daily_commit_stats(db, group, day)  # 重跑同一天

    stats = list(
        (
            await db.scalars(
                select(DailyStat).where(DailyStat.group_id == group.id, DailyStat.stat_date == day)
            )
        ).all()
    )
    files = list(
        (
            await db.scalars(
                select(FileStat).where(FileStat.group_id == group.id, FileStat.stat_date == day)
            )
        ).all()
    )
    assert len(stats) == 1 and stats[0].commits == 1
    assert stats[0].person_id == person.id
    assert len(files) == 1
    assert (files[0].path, files[0].commits, files[0].additions, files[0].deletions) == ("src/a.py", 1, 2, 1)
    commit_files = list(
        (
            await db.scalars(
                select(CommitFile).where(CommitFile.group_id == group.id, CommitFile.stat_date == day)
            )
        ).all()
    )
    assert len(commit_files) == 1  # 重跑不重复
    assert commit_files[0].sha == "sha1" and commit_files[0].path == "src/a.py"
    assert commit_files[0].message == "修复任务重复打开"  # 只存首行
    assert sent["reason"] == "code_report_daily"
    assert sent["mention"] == []  # 组没配技术负责人：照发，只是不 @


async def test_author_name_fallback(db, seeded, monkeypatch):
    """邮箱对不上任何档案时用提交人姓名兜底：外包同事拿自己的 git 邮箱提交很常见。"""
    group = seeded["group"]
    day = date(2026, 10, 24)
    wang = Person(name=f"开发者{group.id}", gitlab_username=f"dev{group.id}")
    db.add(wang)
    await db.commit()

    async def fake_commits(self, client_, pid, since=None, until=None):
        return [
            {"id": "sha9", "author_email": "dev@example.com", "author_name": wang.name, "stats": {}}
        ]

    async def fake_diff(self, client_, pid, sha):
        return []

    monkeypatch.setattr(GitlabClient, "project_commits", fake_commits)
    monkeypatch.setattr(GitlabClient, "commit_diff", fake_diff)
    await collect_day(db, group, day)
    row = await db.scalar(
        select(DailyStat).where(DailyStat.group_id == group.id, DailyStat.stat_date == day)
    )
    assert row.person_id == wang.id


async def test_person_view(db, seeded):
    """人员视角：只统计这个人的提交，文件统计没有人员维度所以不出热点文件。"""
    proj, group, person = seeded["project"], seeded["group"], seeded["person"]
    day = date(2026, 10, 23)
    other = Person(name="李四", gitlab_username=f"lisi-agg-{group.id}", gitlab_email=f"l4-{group.id}@t.cn")
    db.add(other)
    await db.flush()
    for pid, n in ((person.id, 6), (other.id, 2)):
        db.add(
            DailyStat(
                group_id=group.id, stat_date=day, system_id=proj.id, person_id=pid,
                gitlab_author=f"a{pid}@t.cn", commits=n, additions=10 * n, deletions=n,
            )
        )
    db.add(
        FileStat(
            group_id=group.id, stat_date=day, system_id=proj.id, path="src/x.py",
            commits=3, additions=30, deletions=3,
        )
    )
    await db.commit()

    agg = await aggregate(db, group_id=group.id, system_id=None, person_id=person.id, start=day, end=day)
    assert agg["totals"]["commits"] == 6 and agg["totals"]["people"] == 1
    assert [p["name"] for p in agg["people"]] == ["张三"]
    assert agg["hot_files"] == []


async def test_collect_endpoint_backfills(admin_client, seeded, monkeypatch):
    """补数据接口：按天循环采集，不推报表。"""
    from vega.routers import code_stats as router_mod

    days: list[date] = []

    async def fake_collect(db_, group_, day, system_id=None):
        days.append(day)
        return {"rows": 2, "files": 1, "projects": 1}

    monkeypatch.setattr(router_mod, "collect_day", fake_collect)
    r = await admin_client.post(
        f"/api/code-stats/collect?group_id={seeded['group'].id}&start=2026-10-20&end=2026-10-22"
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"days": 3, "rows": 6, "files": 3}
    assert days == [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]

    r = await admin_client.post(
        f"/api/code-stats/collect?group_id={seeded['group'].id}&start=2026-01-01&end=2026-03-01"
    )
    assert r.status_code == 400


async def test_code_stats_endpoint(admin_client, db, seeded):
    proj, group, person = seeded["project"], seeded["group"], seeded["person"]
    day = date(2026, 10, 22)
    db.add(
        DailyStat(
            group_id=group.id, stat_date=day, system_id=proj.id, person_id=person.id,
            gitlab_author="zhangsan@test.cn", commits=5, additions=50, deletions=5,
        )
    )
    await db.commit()

    r = await admin_client.get(f"/api/code-stats?group_id={group.id}&start={day}&end={day}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["commits"] == 5
    assert body["people"][0]["name"] == "张三"
    assert body["start"] == str(day) and body["end"] == str(day)


async def test_group_tech_leads_roundtrip(admin_client, seeded):
    """研发组技术负责人：可多选保存，空数组 = 清空。"""
    gid, pid = seeded["group"].id, seeded["person"].id
    r = await admin_client.put(f"/api/groups/{gid}", json={"tech_lead_person_ids": [pid]})
    assert r.status_code == 200, r.text
    assert r.json()["tech_lead_person_ids"] == [pid]

    r = await admin_client.get("/api/groups")
    assert next(g for g in r.json() if g["id"] == gid)["tech_lead_person_ids"] == [pid]

    r = await admin_client.put(f"/api/groups/{gid}", json={"tech_lead_person_ids": []})
    assert r.json()["tech_lead_person_ids"] == []
