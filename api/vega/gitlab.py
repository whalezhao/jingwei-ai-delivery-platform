"""GitLab API 客户端（httpx async，面向 13.12 的 API v4）。

只做本系统需要的调用：项目扫描 / webhook 配置 / MR 查询与创建 /
每日提交统计 / pipeline job 日志。token 由调用方解密后传入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import httpx

# 13.12 实测：与 Hermes 脚本一致的并发拐点
_CONCURRENCY = 16
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass
class GitlabClient:
    base_url: str  # https://git.example.com
    token: str

    @property
    def api(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v4"

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token}

    async def whoami(self, client: httpx.AsyncClient) -> dict:
        """校验 token 归属：返回 {"id": 42, "username": ..., "name": ..., "email": ...}。"""
        r = await client.get(f"{self.api}/user", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_project(self, client: httpx.AsyncClient, ref: int | str) -> dict:
        """按 id 或 group/repo 路径取项目；无权限时 404（个人账号派发前的权限预检）。"""
        r = await client.get(f"{self.api}/projects/{ref}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def list_admin_projects(self, client: httpx.AsyncClient) -> list[dict]:
        """有 Maintainer(40)+ 权限的项目。"""
        out: list[dict] = []
        page = 1
        while True:
            r = await client.get(
                f"{self.api}/projects",
                params={"membership": "true", "min_access_level": 40, "per_page": 100, "page": page},
                headers=self._headers(),
            )
            r.raise_for_status()
            batch = r.json()
            out.extend(batch)
            if len(batch) < 100 or page >= 20:
                return out
            page += 1

    async def list_webhooks(self, client: httpx.AsyncClient, project_id: int) -> list[dict]:
        r = await client.get(f"{self.api}/projects/{project_id}/hooks", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def list_project_members(self, client: httpx.AsyncClient, project_id: int) -> list[dict]:
        """项目成员（members/all 含继承的组权限）。成员对配的 GitLab 一方。

        必须翻页：GitLab 默认每页 20 人，大项目里排在 20 名之后的人会整个漏掉。
        """
        out: list[dict] = []
        page = 1
        while True:
            r = await client.get(
                f"{self.api}/projects/{project_id}/members/all",
                params={"per_page": 100, "page": page},
                headers=self._headers(),
            )
            r.raise_for_status()
            batch = r.json()
            out.extend(m for m in batch if m.get("state") == "active")
            if len(batch) < 100 or page >= 20:
                return out
            page += 1

    async def ensure_webhook(
        self,
        client: httpx.AsyncClient,
        project_id: int,
        url: str,
        secret: str,
        events: list[str],
    ) -> bool:
        """幂等：已有同 URL 的 hook 则跳过。返回是否新建。"""
        for hook in await self.list_webhooks(client, project_id):
            if hook.get("url") == url:
                return False
        r = await client.post(
            f"{self.api}/projects/{project_id}/hooks",
            json={
                "url": url,
                "token": secret,
                "push_events": "push" in events,
                "merge_requests_events": "merge_request" in events,
                "pipeline_events": "pipeline" in events,
                "job_events": False,
                "enable_ssl_verification": False,  # 内网自签场景
            },
            headers=self._headers(),
        )
        r.raise_for_status()
        return True

    async def delete_webhook(self, client: httpx.AsyncClient, project_id: int, hook_id: int) -> None:
        r = await client.delete(
            f"{self.api}/projects/{project_id}/hooks/{hook_id}", headers=self._headers()
        )
        r.raise_for_status()

    async def list_merge_requests(self, client: httpx.AsyncClient, project_id: int, **params) -> list[dict]:
        r = await client.get(
            f"{self.api}/projects/{project_id}/merge_requests",
            params={"per_page": 100, **params},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def create_mr(
        self,
        client: httpx.AsyncClient,
        project_id: int,
        *,
        source: str,
        target: str,
        title: str,
        description: str,
        assignee_usernames: list[str],
        labels: str = "ai-gen",
    ) -> dict:
        # 13.12 CE 只能挂一个 assignee（多值实测只保留第一个），
        # 其余负责人在描述里 @ 到，GitLab 同样会通知本人
        assignee_id = None
        for u in assignee_usernames:
            assignee_id = await self._user_id(client, u)
            if assignee_id:
                break
        mention = " ".join(f"@{u}" for u in assignee_usernames)
        r = await client.post(
            f"{self.api}/projects/{project_id}/merge_requests",
            json={
                "source_branch": source,
                "target_branch": target,
                "title": title,
                "description": f"{description}\n\n审核人：{mention}" if mention else description,
                "assignee_id": assignee_id,
                "labels": labels,
                "remove_source_branch": False,
            },
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def _user_id(self, client: httpx.AsyncClient, username: str) -> int | None:
        r = await client.get(f"{self.api}/users", params={"username": username}, headers=self._headers())
        r.raise_for_status()
        users = r.json()
        return users[0]["id"] if users else None

    async def project_commits(
        self, client: httpx.AsyncClient, project_id: int, *, since: datetime, until: datetime
    ) -> list[dict]:
        """with_stats 拿增删行；13.12 该接口单页上限 100、只翻 5 页（日报足够）。"""
        out: list[dict] = []
        for page in range(1, 6):
            r = await client.get(
                f"{self.api}/projects/{project_id}/repository/commits",
                params={
                    "since": since.isoformat(),
                    "until": until.isoformat(),
                    "with_stats": "true",
                    "per_page": 100,
                    "page": page,
                },
                headers=self._headers(),
            )
            r.raise_for_status()
            batch = r.json()
            out.extend(batch)
            if len(batch) < 100:
                return out
        return out

    async def commit_diff(self, client: httpx.AsyncClient, project_id: int, sha: str) -> list[dict]:
        """单个提交改动的文件列表（热点文件统计用）；大提交翻页，5 页封顶。"""
        out: list[dict] = []
        for page in range(1, 6):
            r = await client.get(
                f"{self.api}/projects/{project_id}/repository/commits/{sha}/diff",
                params={"per_page": 100, "page": page},
                headers=self._headers(),
            )
            r.raise_for_status()
            batch = r.json()
            out.extend(batch)
            if len(batch) < 100:
                return out
        return out

    async def pipeline_jobs(self, client: httpx.AsyncClient, project_id: int, pipeline_id: int) -> list[dict]:
        r = await client.get(
            f"{self.api}/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def job_trace(self, client: httpx.AsyncClient, project_id: int, job_id: int) -> str:
        r = await client.get(
            f"{self.api}/projects/{project_id}/jobs/{job_id}/trace", headers=self._headers()
        )
        r.raise_for_status()
        return r.text

    async def mr_detail(self, client: httpx.AsyncClient, project_id: int, mr_iid: int) -> dict:
        r = await client.get(
            f"{self.api}/projects/{project_id}/merge_requests/{mr_iid}", headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    async def mr_changes(self, client: httpx.AsyncClient, project_id: int, mr_iid: int) -> list[dict]:
        """MR 改动文件列表（13.12 changes 接口）。"""
        r = await client.get(
            f"{self.api}/projects/{project_id}/merge_requests/{mr_iid}/changes", headers=self._headers()
        )
        r.raise_for_status()
        return r.json().get("changes") or []

    async def mr_pipelines(self, client: httpx.AsyncClient, project_id: int, mr_iid: int) -> list[dict]:
        """MR 关联 pipeline（13.12），按 sha 前缀匹配取本提交的。"""
        r = await client.get(
            f"{self.api}/projects/{project_id}/merge_requests/{mr_iid}/pipelines", headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    async def add_mr_note(self, client: httpx.AsyncClient, project_id: int, mr_iid: int, body: str) -> dict:
        r = await client.post(
            f"{self.api}/projects/{project_id}/merge_requests/{mr_iid}/notes",
            json={"body": body},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def approve_mr(self, client: httpx.AsyncClient, project_id: int, mr_iid: int) -> bool:
        """409/405（已批/未开 approvals/不能自批）一律容忍为 False，不 raise。"""
        r = await client.post(
            f"{self.api}/projects/{project_id}/merge_requests/{mr_iid}/approve", headers=self._headers()
        )
        return r.status_code in (200, 201)

    async def merge_mr(self, client: httpx.AsyncClient, project_id: int, mr_iid: int, sha: str) -> dict:
        """带 sha 合并：期间有新推送 GitLab 直接 406 拒绝（防竞态）。失败 raise。"""
        r = await client.put(
            f"{self.api}/projects/{project_id}/merge_requests/{mr_iid}/merge",
            json={"sha": sha, "should_remove_source_branch": False},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()


async def gather_concurrently(coro_factories: list, limit: int = _CONCURRENCY) -> list:
    sem = asyncio.Semaphore(limit)

    async def wrapped(f):
        async with sem:
            try:
                return await f()
            except Exception:  # noqa: BLE001 单项目失败不拖垮整轮统计
                return None

    return await asyncio.gather(*(wrapped(f) for f in coro_factories))


def client_ctx():
    return httpx.AsyncClient(timeout=_TIMEOUT, verify=False)  # 内网自签；13.12 环境已知如此
