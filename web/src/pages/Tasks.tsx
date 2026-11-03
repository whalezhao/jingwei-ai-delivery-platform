import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Input, Select, Tag, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { api } from "../api";
import i18n, { type MsgKey } from "../i18n";
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
  /** 详情页含需求文档/方案正文，非认领人后端 403 —— 入口直接灰掉 */
  can_open: boolean;
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

export const STATUS: Record<string, [string, MsgKey]> = {
  pending: ["default", "tasks.status.pending"],
  queued: ["processing", "tasks.status.queued"],
  running: ["processing", "tasks.status.running"],
  pushed: ["processing", "tasks.status.pushed"],
  mr_created: ["success", "tasks.status.mr_created"],
  reviewing: ["success", "tasks.status.reviewing"],
  cicd: ["processing", "tasks.status.cicd"],
  done: ["green", "tasks.status.done"],
  failed: ["error", "tasks.status.failed"],
};

/** 任务详情 / 需求详情都含需求文档与方案正文：can_open=false 时不给入口，点也不跳 */
function openOrTell(r: Task, to: string, label: string, className?: string) {
  return r.can_open ? (
    <Link to={to} className={className}>
      {label}
    </Link>
  ) : (
    <span
      className={className}
      style={{ color: "var(--muted)", cursor: "help" }}
      onClick={() => message.info(i18n.t("errors.denied"))}
    >
      {label}
    </span>
  );
}

export default function Tasks() {
  const { t } = useTranslation();
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
    const timer = setInterval(load, 5000); // 状态自动刷新
    return () => clearInterval(timer);
  }, [groupId, projectId, status, keyword]);

  useEffect(() => {
    setProjectId(undefined); // 切组后旧项目筛选可能不属于该组
    api<any[]>(`/api/projects${groupId ? `?group_id=${groupId}` : ""}`).then(setProjects);
  }, [groupId]);

  return (
    <Page title={t("nav.tasks")} desc={t("tasks.desc")}>
      <div className="page-card">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
          <Select
            style={{ width: 260 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("tasks.filterProject")}
            value={projectId}
            onChange={setProjectId}
            options={projects.map((p) => ({ value: p.id, label: `${p.path}（${p.name}）` }))}
          />
          <Select
            style={{ width: 140 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("tasks.filterStatus")}
            value={status}
            onChange={setStatus}
            options={Object.entries(STATUS).map(([k, v]) => ({ value: k, label: t(v[1]) }))}
          />
          <Input.Search
            style={{ width: 260 }}
            allowClear
            placeholder={t("tasks.searchPlaceholder")}
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
              title: t("tasks.colCode"),
              dataIndex: "code",
              width: 250,
              render: (v, r) => openOrTell(r, `/tasks/${r.id}`, v, "mono"),
            },
            {
              title: t("tasks.colTitle"),
              dataIndex: "title",
              ellipsis: true,
              render: (v, r) => (
                <span title={r.scope || v || undefined}>{v || "—"}</span>
              ),
            },
            {
              title: t("tasks.colRequirement"),
              dataIndex: "requirement_id",
              render: (v, r) => openOrTell(r, `/reqs/${v}`, t("tasks.viewRequirement")),
              width: 100,
            },
            {
              title: t("tasks.colRunner"),
              dataIndex: "runner_name",
              width: 110,
              render: (v) => v || "—",
            },
            {
              title: t("common.status"),
              dataIndex: "status",
              width: 110,
              render: (v) => {
                const pair = STATUS[v];
                return <Tag color={pair?.[0] ?? "default"}>{pair ? t(pair[1]) : v}</Tag>;
              },
            },
            {
              title: t("tasks.colBranch"),
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
              title: t("tasks.colError"),
              dataIndex: "error",
              ellipsis: true,
              render: (v) =>
                v ? <Typography.Text type="danger" style={{ fontSize: 12 }}>{v}</Typography.Text> : "—",
            },
            {
              title: t("tasks.colCreated"),
              dataIndex: "created_at",
              render: (v) => (v ? <span className="mono">{v.slice(0, 19)}</span> : "—"),
              width: 170,
            },
            {
              title: t("common.actions"),
              render: (_, r) => openOrTell(r, `/tasks/${r.id}`, t("tasks.detail")),
              width: 80,
            },
          ]}
        />
      </div>
    </Page>
  );
}
