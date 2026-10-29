"""集成中心：单址 webhook 组识别 / summary 鉴权 / 事件与通知分页查询。"""

from __future__ import annotations

from vega.models import DevGroup, GitlabEvent, NotifyLog


async def test_webhook_old_group_route_gone(client, seeded):
    r = await client.post(f"/webhooks/gitlab/{seeded['group'].id}", json={})
    assert r.status_code == 404


async def test_webhook_two_groups_by_secret(client, db, seeded):
    """单址没有 group_id：两个组各自 secret，事件必须落回正确的组。"""
    g2 = DevGroup(name=f"g2-{seeded['group'].id}", webhook_secret="whs-second-secret")
    db.add(g2)
    await db.commit()
    s1, s2 = seeded["group"].webhook_secret, "whs-second-secret"

    def push(token: str) -> dict:
        return dict(
            json={"project_id": 1, "user_name": "x", "ref": "refs/heads/a", "total_commits_count": 1, "after": "c1"},
            headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": token},
        )

    r1 = await client.post("/webhooks/gitlab", **push(s1))
    r2 = await client.post("/webhooks/gitlab", **push(s2))
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)

    from sqlalchemy import select

    rows = (
        await db.scalars(select(GitlabEvent).order_by(GitlabEvent.id.desc()).limit(2))
    ).all()
    assert {e.group_id for e in rows} == {seeded["group"].id, g2.id}


async def test_summary_admin_only(client, admin_client, db, seeded):
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    r = await client.get("/api/integrations/summary", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403

    r = await admin_client.get("/api/integrations/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["webhook_url"].endswith("/webhooks/gitlab")
    mine = next(g for g in body["groups"] if g["id"] == seeded["group"].id)
    assert mine["webhook_secret"] == seeded["group"].webhook_secret and mine["has_octo_bot_token"] is False


async def test_events_and_notify_logs_paged(client, admin_client, db, seeded):
    db.add_all(
        NotifyLog(group_id=seeded["group"].id, reason="push", content=f"c{i}", ok=True) for i in range(25)
    )
    await db.commit()

    r = await admin_client.get("/api/integrations/notify-logs", params={"page": 2, "page_size": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 25 and len(body["items"]) >= 5

    r = await admin_client.get("/api/integrations/events")
    assert r.status_code == 200 and "items" in r.json()

    r = await admin_client.get("/api/integrations/mr-reviews")
    assert r.status_code == 200 and r.json()["items"] == []


async def test_log_search_filters(admin_client, db, seeded):
    """q 走服务端过滤：通知查正文/原因，MR 查标题/项目路径，事件查类型。"""
    from vega.models import MrReview, Project

    gid = seeded["group"].id
    db.add_all(
        [
            NotifyLog(group_id=gid, reason="push", content="流水线失败 adjudicator", ok=True),
            NotifyLog(group_id=gid, reason="mr_review", content="别的通知", ok=True),
        ]
    )
    proj = Project(
        group_id=gid,
        gitlab_project_id=901,
        path="grp/nightly-target",
        name="target",
        code_prefix="TGT",
        default_branch="master",
        mr_target_branch="nightly",
    )
    db.add(proj)
    await db.flush()
    db.add_all(
        [
            MrReview(
                project_id=proj.id, group_id=gid, mr_iid=1, head_sha="a" * 40,
                title="修复登录", verdict="approve",
            ),
            MrReview(
                project_id=proj.id, group_id=gid, mr_iid=2, head_sha="b" * 40,
                title="调整样式", verdict="approve",
            ),
        ]
    )
    db.add_all(
        [
            GitlabEvent(group_id=gid, event_type="Push Hook", payload={}),
            GitlabEvent(group_id=gid, event_type="Merge Request Hook", payload={}),
        ]
    )
    await db.commit()

    rows = (await admin_client.get("/api/integrations/notify-logs", params={"q": "adjudicator"})).json()
    assert [r["content"] for r in rows["items"]] == ["流水线失败 adjudicator"] and rows["total"] == 1

    rows = (await admin_client.get("/api/integrations/mr-reviews", params={"q": "登录"})).json()
    assert [r["mr_iid"] for r in rows["items"]] == [1]
    rows = (await admin_client.get("/api/integrations/mr-reviews", params={"q": "nightly-target"})).json()
    assert sorted(r["mr_iid"] for r in rows["items"]) == [1, 2]

    rows = (await admin_client.get("/api/integrations/events", params={"q": "merge request"})).json()
    assert [r["event_type"] for r in rows["items"]] == ["Merge Request Hook"]
    # 用户输入里的 % 当字面量，不当通配符
    rows = (await admin_client.get("/api/integrations/notify-logs", params={"q": "%"})).json()
    assert rows["total"] == 0
