import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import i18n, { type MsgKey } from "../i18n";
import { useGroup } from "../group";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import SearchSelect from "../ui/SearchSelect";
import { toast } from "../ui/toast";
import { Input } from "../components/motion/input";

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

export const STATUS: Record<string, [BadgeTone, MsgKey]> = {
  pending: ["neutral", "tasks.status.pending"],
  queued: ["info", "tasks.status.queued"],
  running: ["info", "tasks.status.running"],
  pushed: ["info", "tasks.status.pushed"],
  mr_created: ["success", "tasks.status.mr_created"],
  reviewing: ["success", "tasks.status.reviewing"],
  cicd: ["info", "tasks.status.cicd"],
  done: ["success", "tasks.status.done"],
  failed: ["danger", "tasks.status.failed"],
};

/** 任务详情 / 需求详情都含需求文档与方案正文：can_open=false 时不给入口，点也不跳 */
function openOrTell(r: Task, to: string, label: string, className?: string) {
  return r.can_open ? (
    <Link to={to} className={className}>
      {label}
    </Link>
  ) : (
    <button
      type="button"
      className={className}
      onClick={() => toast.info(i18n.t("errors.denied"))}
    >
      {label}
    </button>
  );
}

export default function Tasks() {
  const { t } = useTranslation();
  const { groupId, projectId, groups, systems } = useGroup();
  const [rows, setRows] = useState<Task[]>([]);
  const [systemFilter, setSystemFilter] = useState<number | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [draft, setDraft] = useState("");

  const load = () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("project_id", String(projectId));
    if (systemFilter) p.set("system_id", String(systemFilter));
    if (status) p.set("status", status);
    if (keyword.trim()) p.set("q", keyword.trim());
    return api<Task[]>(`/api/dev-tasks?${p}`).then(setRows);
  };
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000); // 状态自动刷新
    return () => clearInterval(timer);
  }, [groupId, projectId, systemFilter, status, keyword]);
  // 全局条件变了，表格内的系统筛选可能已不在范围内
  useEffect(() => {
    setSystemFilter(undefined);
  }, [groupId, projectId]);

  const scopedSystems = systems.filter(
    (s) =>
      (!groupId || s.group_id === groupId) &&
      (!projectId || groups.find((g) => g.id === s.group_id)?.project_id === projectId)
  );

  const columns: DataTableColumn<Task>[] = [
    {
      title: t("tasks.colCode"),
      dataIndex: "code",
      width: 250,
      render: (v, r) => openOrTell(r, `/tasks/${r.id}`, v, "font-mono text-[12px] text-primary"),
    },
    {
      title: t("tasks.colTitle"),
      dataIndex: "title",
      render: (v, r) => <span className="truncate">{r.scope || v || "—"}</span>,
    },
    {
      title: t("tasks.colRequirement"),
      dataIndex: "requirement_id",
      width: 100,
      render: (v, r) => openOrTell(r, `/reqs/${v}`, t("tasks.viewRequirement"), "text-primary"),
    },
    {
      title: t("tasks.colRunner"),
      dataIndex: "runner_name",
      width: 110,
      render: (v) => <span className="truncate">{v || "—"}</span>,
    },
    {
      title: t("common.status"),
      dataIndex: "status",
      width: 110,
      render: (v) => {
        const pair = STATUS[v];
        return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : v}</Badge>;
      },
    },
    {
      title: t("tasks.colBranch"),
      dataIndex: "branch",
      render: (v) => (v ? <span className="truncate font-mono text-[12px]">{v}</span> : "—"),
    },
    {
      title: "MR",
      dataIndex: "mr_iid",
      width: 80,
      render: (v, r) =>
        v ? (
          r.mr_url ? (
            <a href={r.mr_url} target="_blank" rel="noreferrer" className="font-mono text-[12px] text-primary">
              !{v}
            </a>
          ) : (
            <span className="font-mono text-[12px]">!{v}</span>
          )
        ) : (
          "—"
        ),
    },
    {
      title: t("tasks.colError"),
      dataIndex: "error",
      render: (v) =>
        v ? <span className="truncate text-[12px] text-destructive">{v}</span> : "—",
    },
    {
      title: t("tasks.colCreated"),
      dataIndex: "created_at",
      width: 170,
      render: (v) =>
        v ? <span className="font-mono text-[12px]">{v.slice(0, 19)}</span> : "—",
    },
    {
      title: t("common.actions"),
      width: 80,
      render: (_, r) => openOrTell(r, `/tasks/${r.id}`, t("tasks.detail"), "text-primary"),
    },
  ];

  return (
    <Page title={t("nav.tasks")} desc={t("tasks.desc")}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchSelect
          value={systemFilter}
          onChange={(v) => setSystemFilter(v == null ? undefined : Number(v))}
          placeholder={t("tasks.filterSystem")}
          clearable
          options={scopedSystems.map((s) => ({ value: s.id, label: `${s.path}（${s.code_prefix}）` }))}
          className="w-[260px]"
        />
        <SearchSelect
          value={status}
          onChange={(v) => setStatus(v == null ? undefined : String(v))}
          placeholder={t("tasks.filterStatus")}
          clearable
          options={Object.entries(STATUS).map(([k, v]) => ({ value: k, label: t(v[1]) }))}
          className="w-[140px]"
        />
        <Input
          value={draft}
          onChange={setDraft}
          onKeyDown={(e) => {
            if (e.key === "Enter") setKeyword(draft.trim());
          }}
          placeholder={t("tasks.searchPlaceholder")}
          leftIcon={<Search />}
          classNames={{ field: "rounded-lg" }}
          className="w-[260px]"
        />
      </div>
      <DataTable<Task> rowKey="id" dataSource={rows} columns={columns} emptyText={t("common.noData")} />
    </Page>
  );
}