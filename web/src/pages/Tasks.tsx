import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Input, Select, Tag, Typography } from "antd";
import ResizableTable from "../ResizableTable";
import { api } from "../api";
import { useGroup } from "../group";
import Page from "../Page";
import { PAGE } from "../table";

interface Task {
  id: number;
  requirement_id: number;
  code: string;
  title: string | null;
  scope: string | null;
  status: string;
  creator_id: number;
  branch: string | null;
  mr_iid: number | null;
  mr_url: string | null;
  runner_name: string | null;
  error: string | null;
  created_at: string | null;
}

/** 过程事件（任务详情页 AI 工作进展渲染用；parent 为空 = 主 Agent，否则为子 Agent 泳道） */
export interface TaskEvent {
  seq: number;
  kind: string;
  text?: string;
  name?: string;
  input?: string;
  content?: string;
  is_error?: boolean;
  parent?: string;
  id?: string;
}

export const STATUS: Record<string, [string, string]> = {
  pending: ["default", "待派发"],
  queued: ["processing", "排队中"],
  running: ["processing", "执行中"],
  pushed: ["processing", "已推送"],
  mr_created: ["success", "待审核"],
  reviewing: ["success", "审核中"],
  cicd: ["processing", "CICD"],
  done: ["green", "完成"],
  failed: ["error", "失败"],
};

export default function Tasks() {
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Task[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<number | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");

  const load = () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("project_id", String(projectId));
    if (status) p.set("status", status);
    if (keyword.trim()) p.set("q", keyword.trim());
    return api<Task[]>(`/api/dev-tasks?${p}`).then(setRows);
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 5000); // 状态自动刷新
    return () => clearInterval(t);
  }, [groupId, projectId, status, keyword]);

  useEffect(() => {
    setProjectId(undefined); // 切组后旧项目筛选可能不属于该组
    api<any[]>(`/api/projects${groupId ? `?group_id=${groupId}` : ""}`).then(setProjects);
  }, [groupId]);

  return (
    <Page title="开发任务" desc="任务池 · 所有 runner 抢单执行 · 状态 5 秒自动刷新">
      <div className="page-card">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
          <Select
            style={{ width: 260 }}
            allowClear
            placeholder="按项目筛选"
            value={projectId}
            onChange={setProjectId}
            options={projects.map((p) => ({ value: p.id, label: `${p.path}（${p.name}）` }))}
          />
          <Select
            style={{ width: 140 }}
            allowClear
            placeholder="按状态筛选"
            value={status}
            onChange={setStatus}
            options={Object.entries(STATUS).map(([k, v]) => ({ value: k, label: v[1] }))}
          />
          <Input.Search
            style={{ width: 260 }}
            allowClear
            placeholder="任务号 / 标题"
            onSearch={(v) => setKeyword(v.trim())}
            onChange={(e) => !e.target.value && setKeyword("")}
          />
        </div>
        <ResizableTable<Task>
          rowKey="id"
          dataSource={rows}
          pagination={PAGE}
          columns={[
            {
              title: "任务号",
              dataIndex: "code",
              width: 250,
              render: (v, r) => (
                <Link to={`/tasks/${r.id}`} className="mono">
                  {v}
                </Link>
              ),
            },
            {
              title: "任务标题",
              dataIndex: "title",
              ellipsis: true,
              render: (v, r) => (
                <span title={r.scope || v || undefined}>{v || "—"}</span>
              ),
            },
            {
              title: "需求",
              dataIndex: "requirement_id",
              render: (v) => <Link to={`/reqs/${v}`}>查看需求</Link>,
              width: 100,
            },
            {
              title: "执行节点",
              dataIndex: "runner_name",
              width: 110,
              render: (v) => v || "—",
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 110,
              render: (v) => {
                const t = STATUS[v] || ["default", v];
                return <Tag color={t[0]}>{t[1]}</Tag>;
              },
            },
            {
              title: "分支",
              dataIndex: "branch",
              render: (v) => (v ? <span className="mono">{v}</span> : "—"),
            },
            {
              title: "MR",
              dataIndex: "mr_iid",
              render: (v, r) =>
                v ? (
                  r.mr_url ? (
                    <a href={r.mr_url} target="_blank" rel="noreferrer" className="mono">
                      !{v}
                    </a>
                  ) : (
                    <span className="mono">!{v}</span>
                  )
                ) : (
                  "—"
                ),
              width: 80,
            },
            {
              title: "错误",
              dataIndex: "error",
              ellipsis: true,
              render: (v) =>
                v ? <Typography.Text type="danger" style={{ fontSize: 12 }}>{v}</Typography.Text> : "—",
            },
            {
              title: "创建时间",
              dataIndex: "created_at",
              render: (v) => (v ? <span className="mono">{v.slice(0, 19)}</span> : "—"),
              width: 170,
            },
            {
              title: "操作",
              render: (_, r) => <Link to={`/tasks/${r.id}`}>详情</Link>,
              width: 80,
            },
          ]}
        />
      </div>
    </Page>
  );
}
