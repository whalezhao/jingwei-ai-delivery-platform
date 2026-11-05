import { Fragment, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Empty, Select, Tabs, Tag, message } from "antd";
import dayjs from "dayjs";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { useGroup } from "../group";
import i18n, { type MsgKey } from "../i18n";
import Page from "../Page";
import CodeStats from "./CodeStats";

/**
 * 工作台：需求 → 任务 → MR → CI → 合入 的实时流程图。
 * 布局：每个需求一行，状态节点连成轨道；任务卡不单独挂一行，而是并进
 * 它当前所处的阶段节点（同一状态只出现一张卡），随阶段移动；
 * 连线/圆点随任务状态动起来（进行中虚线流动、执行中呼吸、失败红闪），5s 轮询刷新。
 * 轨道按项目配置裁剪：项目没配 audit_prefixes 就没有代码评审/CI，
 * 没配 auto_approve_prefixes 就没有自动合并，收尾改为「octo 通知项目负责人」。
 * 只显示实时工作的需求：所有任务到终态（合并 / 失败 / MR 关闭）即退场；
 * 全部合入的需求状态会被置为「已上线」，去需求池里看。 */

interface Mr {
  verdict: string;
  gate: string;
  pipeline_url: string | null;
  flow_state: string;
  target_branch: string;
  summary: string | null;
  issues: number;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}
interface Task {
  id: number;
  code: string;
  title: string | null;
  scope: string | null;
  status: string;
  branch: string | null;
  runner_name: string | null;
  mr_iid: number | null;
  mr_url: string | null;
  error: string | null;
  outcome: string | null;
  created_at: string | null;
  updated_at: string | null;
  agents: { sub: number; events: number } | null;
  mr: Mr | null;
  /** 详情含需求文档/方案正文，非认领人后端 403 —— 入口按此禁用 */
  can_open: boolean;
}
interface Req {
  id: number;
  code: string;
  title: string;
  req_type: "requirement" | "bug";
  status: string;
  can_open: boolean;
  claimant_name: string | null;
  created_at: string | null;
  system_id: number;
  system_name: string;
  group_id: number;
  doc_final: boolean;
  plan_final: boolean;
  doc: { version: number; filename: string; time: string | null } | null;
  plan: { version: number; time: string | null } | null;
  review_on: boolean;
  merge_on: boolean;
  owners: string[];
  doc_agent: { id: string; name: string } | null;
  plan_agent: { id: string; name: string } | null;
  tasks: Task[];
}

/** 卡片时间统一显示到分钟；后端给的是带时区的 ISO，dayjs 转浏览器本地时区 */
const fmtT = (s?: string | null) => (s ? dayjs(s).format("MM-DD HH:mm") : null);
const fullT = (s?: string | null) => (s ? dayjs(s).format("YYYY-MM-DD HH:mm:ss") : "—");
const stamped = (key: MsgKey, s?: string | null) => (s ? `${i18n.t(key)} ${fmtT(s)}` : null);

const REQ_STATUS: Record<string, [string, MsgKey]> = {
  pool: ["default", "workbench.reqStatus.pool"],
  claimed: ["processing", "workbench.reqStatus.claimed"],
  confirmed: ["success", "workbench.reqStatus.confirmed"],
  archived: ["default", "workbench.reqStatus.archived"],
  online: ["cyan", "workbench.reqStatus.online"],
};
const TASK_STATUS: Record<string, MsgKey> = {
  pending: "workbench.taskStatus.pending",
  queued: "workbench.taskStatus.queued",
  running: "workbench.taskStatus.running",
  pushed: "workbench.taskStatus.pushed",
  mr_created: "workbench.taskStatus.mr_created",
  reviewing: "workbench.taskStatus.reviewing",
  cicd: "workbench.taskStatus.cicd",
  done: "workbench.taskStatus.done",
  failed: "workbench.taskStatus.failed",
};

/** 连线与圆点的动效档位：bad 红闪 / ok 绿实线 / live 蓝虚线流动 / idle 灰静态 */
function flowCls(task: Task): string {
  if (
    task.status === "failed" ||
    task.mr?.verdict === "failed" ||
    ["ci:failed", "ci:canceled"].includes(task.mr?.gate || "") ||
    task.mr?.flow_state === "conflict_notified"
  )
    return "bad";
  if (task.mr?.flow_state === "merged" || task.status === "done") return "ok";
  if (["queued", "running", "pushed", "reviewing", "cicd", "mr_created"].includes(task.status)) return "live";
  return "idle";
}

function ciChip(task: Task) {
  const m = task.mr;
  if (!m) return null;
  if (!m.gate) return { cls: "ok", text: i18n.t("workbench.ciOk") };
  if (["ci:failed", "ci:canceled"].includes(m.gate))
    return { cls: "bad", text: i18n.t("workbench.ciFailed", { status: m.gate.slice(3) }) };
  return { cls: "wait", text: i18n.t("workbench.ciWait", { status: m.gate.slice(3) }) };
}
function mergeChip(task: Task) {
  const m = task.mr;
  if (!m) return null;
  if (m.flow_state === "merged")
    return { cls: "ok", text: i18n.t("workbench.mergedBranch", { branch: m.target_branch }) };
  if (m.flow_state === "conflict_notified") return { cls: "bad", text: i18n.t("workbench.conflict") };
  if (m.flow_state === "hold_not_mergeable") return { cls: "wait", text: i18n.t("workbench.holdMergeable") };
  return null;
}
/** 任务卡种类标签：流水线上走到哪一段（终态三分类 + 中间阶段） */
function taskPhase(task: Task, r: Req): [string, MsgKey] {
  if (task.outcome === "merged") return ["success", "workbench.phase.merged"];
  if (task.outcome === "closed") return ["warning", "workbench.phase.closed"];
  if (task.outcome === "failed" || task.status === "failed") return ["error", "workbench.phase.failed"];
  if (task.mr_iid || task.status === "pushed") {
    if (!r.review_on) return ["processing", task.mr_iid ? "workbench.phase.pendingMerge" : "workbench.phase.submitting"];
    const m = task.mr;
    if (!m || m.verdict === "pending" || m.verdict === "modify") return ["processing", "workbench.phase.review"];
    if (m.verdict === "failed") return ["error", "workbench.phase.reviewFailed"];
    if (m.gate) return ["processing", "workbench.phase.ci"];
    return ["processing", r.merge_on ? "workbench.phase.autoMerging" : "workbench.phase.pendingMerge"];
  }
  if (task.status === "pending") return ["default", "workbench.phase.grabbing"];
  return ["processing", "workbench.phase.developing"];
}

type NodeKey = "doc" | "plan" | "dev" | "review" | "ci" | "merge" | "notify";
type St = "ok" | "live" | "idle" | "bad";

const NODE_LABEL: Record<NodeKey, MsgKey> = {
  doc: "workbench.node.doc",
  plan: "workbench.node.plan",
  dev: "workbench.node.dev",
  review: "workbench.node.review",
  ci: "workbench.node.ci",
  merge: "workbench.node.merge",
  notify: "workbench.node.notify",
};

/** 项目配置决定轨道长度：评审/CI 看 audit_prefixes，自动合并看 auto_approve_prefixes；
 *  缺哪段就在 MR 建好后交给负责人（octo 通知），不再画空节点。 */
function nodeKeys(r: Req): NodeKey[] {
  // Bug 没有需求文档阶段，轨道从方案开始
  const keys: NodeKey[] = r.req_type === "bug" ? ["plan", "dev"] : ["doc", "plan", "dev"];
  if (r.review_on) keys.push("review", "ci");
  if (r.review_on && r.merge_on) keys.push("merge");
  else keys.push("notify");
  return keys;
}

function taskBad(task: Task): boolean {
  return (
    task.status === "failed" ||
    task.outcome === "failed" ||
    task.mr?.verdict === "failed" ||
    ["ci:failed", "ci:canceled"].includes(task.mr?.gate || "") ||
    task.mr?.flow_state === "conflict_notified"
  );
}

/** 任务当前所处阶段；终态任务停在收尾（合入/通知）或它失败的那一段 */
function taskStage(task: Task, r: Req): NodeKey {
  const last: NodeKey = r.review_on && r.merge_on ? "merge" : "notify";
  if (task.outcome === "merged" || task.outcome === "closed") return last;
  if (task.outcome === "failed" || task.status === "failed") {
    if (task.mr?.gate?.startsWith("ci:")) return "ci";
    if (!task.mr_iid) return "dev";
    return r.review_on ? "review" : "notify";
  }
  if (!task.mr_iid && task.status !== "pushed") return "dev";
  if (!r.review_on) return "notify"; // 项目没配 AI 评审：MR 建好就交给负责人
  const m = task.mr;
  if (!m || ["pending", "modify", "failed"].includes(m.verdict)) return "review";
  if (m.gate) return "ci";
  return r.merge_on ? "merge" : "notify";
}

/**
 * 需求轨道以「最落后的未终态任务」为当前节点：之前的都已完成、之后都未开始，
 * 避免 MR 已建但评审未过时多个节点同时亮。
 */
function reqNodes(r: Req): { key: NodeKey; label: MsgKey; state: St }[] {
  const keys = nodeKeys(r);
  const state: Record<string, St> = { plan: r.plan_final ? "ok" : "live" };
  if (r.req_type !== "bug") state.doc = "ok";
  const open = r.tasks.filter((task) => !task.outcome && task.status !== "failed");
  if (r.plan_final && open.length) {
    const at = (task: Task) => keys.indexOf(taskStage(task, r));
    const cur = Math.max(keys.indexOf("dev"), Math.min(...open.map(at)));
    const bad = open.some((task) => at(task) === cur && taskBad(task));
    keys.forEach((k, i) => {
      state[k] = i < cur ? "ok" : i === cur ? (bad ? "bad" : "live") : "idle";
    });
  }
  return keys.map((k) => ({ key: k, label: NODE_LABEL[k], state: state[k] || "idle" }));
}

const VERDICT: Record<string, MsgKey> = {
  approve: "workbench.verdict.approve",
  modify: "workbench.verdict.modify",
  failed: "workbench.verdict.failed",
};
const STEP_TEXT: Record<string, MsgKey> = {
  ok: "workbench.step.ok",
  live: "workbench.step.live",
  bad: "workbench.step.bad",
  idle: "workbench.step.idle",
};
/** 专家卡片的角色说明 */
const EXPERT_ROLE: Record<string, MsgKey> = {
  doc: "workbench.expert.doc",
  plan: "workbench.expert.plan",
  dev: "workbench.expert.dev",
};

/** 阶段卡上的核心信息：没挂任务卡时，这一格也要看出「做了什么、什么时候」 */
function nodeInfo(r: Req, key: NodeKey, state: St): { main: string; sub?: string | null; at?: string | null } {
  const first = r.tasks[0];
  const m = first?.mr || null;
  const step = i18n.t(STEP_TEXT[state]);
  if (key === "doc")
    return {
      main: r.doc ? i18n.t("workbench.docVersion", { version: r.doc.version, file: r.doc.filename }) : step,
      at: stamped("workbench.stampedDoc", r.doc?.time),
    };
  if (key === "plan")
    return state === "ok"
      ? {
          main: r.plan
            ? i18n.t("workbench.finalizedV", { version: r.plan.version })
            : i18n.t("workbench.finalized"),
          at: stamped("workbench.stampedDoc", r.plan?.time),
        }
      : { main: i18n.t("workbench.discussing") };
  if (key === "dev")
    return {
      main: r.tasks.length ? i18n.t("workbench.tasksN", { n: r.tasks.length }) : step,
      sub: first?.runner_name ? `⚡ ${first.runner_name}` : null,
      at: stamped("workbench.stampedStart", first?.created_at),
    };
  if (key === "review") {
    if (!m) return { main: step };
    return {
      main: `${VERDICT[m.verdict] ? i18n.t(VERDICT[m.verdict]) : i18n.t("workbench.verdict.pending")}${
        m.issues ? i18n.t("workbench.issues", { n: m.issues }) : ""
      }`,
      at: stamped("workbench.stampedReview", m.created_at),
    };
  }
  if (key === "ci")
    return {
      main: (first && ciChip(first)?.text) || step,
      at: stamped("workbench.stampedUpdate", m?.updated_at),
    };
  if (key === "merge")
    return {
      main: (first && mergeChip(first)?.text) || step,
      at: stamped("workbench.stampedUpdate", m?.updated_at),
    };
  return {
    main: r.owners.length
      ? i18n.t("workbench.octoOwners", { owners: r.owners.join(i18n.t("projects.listSep")) })
      : i18n.t("workbench.noOwners"),
    at: stamped("workbench.stampedMr", m?.created_at),
  };
}

/** 任务卡：并进流程里它当前所处的阶段节点，不再单独挂在下方；带该阶段的必要信息 */

function TaskCard({ task, r, onOpen }: { task: Task; r: Req; onOpen: () => void }) {
  const { t } = useTranslation();
  const denied = t("errors.denied");
  const cls = flowCls(task);
  const ci = ciChip(task);
  const mg = mergeChip(task);
  const phase = taskPhase(task, r);
  const notified = taskStage(task, r) === "notify";
  const status = TASK_STATUS[task.status];
  return (
    <div className={`wb-card wb-task ${cls}`}>
      <div className="wb-task-head">
        <i className={`wb-dot ${cls}`} />
        <span
          className={`wb-task-title${task.can_open ? "" : " muted"}`}
          title={task.can_open ? task.scope || task.title || "" : t("workbench.scopeDenied")}
          onClick={task.can_open ? undefined : () => message.info(denied)}
        >
          {task.title || task.code}
        </span>
        <span
          className="wb-task-time"
          title={t("workbench.createdUpdated", { created: fullT(task.created_at), updated: fullT(task.updated_at) })}
        >
          🕒 {fmtT(task.updated_at) || "—"}
        </span>
        <Tag className="wb-phase" color={phase[0]}>{t(phase[1])}</Tag>
      </div>
      <div className="wb-chips">
        <a
          className={`wb-chip mono${task.can_open ? "" : " muted"}`}
          title={task.can_open ? t("workbench.viewTask", { code: task.code }) : denied}
          onClick={task.can_open ? onOpen : () => message.info(denied)}
        >
          {task.code}
        </a>
        <span className="wb-chip">
          {task.runner_name
            ? `⚡ ${task.runner_name}`
            : task.status === "queued"
              ? t("workbench.queued")
              : "·"}
        </span>
        {task.agents && task.agents.sub > 0 && (
          <a
            className={`wb-chip${cls === "live" ? " live" : ""}${task.can_open ? "" : " muted"}`}
            onClick={task.can_open ? onOpen : () => message.info(denied)}
            title={t("workbench.eventsTip", { n: task.agents.events })}
          >
            {cls === "live"
              ? t("workbench.mainSubLive", { n: task.agents.sub })
              : t("workbench.mainSub", { n: task.agents.sub })}
          </a>
        )}
        {task.mr_iid ? (
          <span className="wb-chip">
            MR{" "}
            {task.mr_url ? (
              <a href={task.mr_url} target="_blank" rel="noreferrer" className="mono">
                !{task.mr_iid}
              </a>
            ) : (
              <span className="mono">!{task.mr_iid}</span>
            )}{" "}
            {VERDICT[task.mr?.verdict || ""] ? t(VERDICT[task.mr?.verdict || ""]) : "⏳"}
          </span>
        ) : (
          <span className="wb-chip">{status ? t(status) : task.status}</span>
        )}
        {task.mr && task.mr.issues > 0 && (
          <span className="wb-chip wait">{t("workbench.issuesShort", { n: task.mr.issues })}</span>
        )}
        {ci && (task.mr?.pipeline_url && ci.cls !== "ok" ? (
          <a className={`wb-chip ${ci.cls}`} href={task.mr.pipeline_url} target="_blank" rel="noreferrer">
            {ci.text} ↗
          </a>
        ) : (
          <span className={`wb-chip ${ci.cls}`}>{ci.text}</span>
        ))}
        {mg && <span className={`wb-chip ${mg.cls}`}>{mg.text}</span>}
      </div>
      {task.mr?.summary && (
        <div className="wb-note" title={task.mr.summary}>
          {task.mr.summary}
        </div>
      )}
      {notified && (
        <div className="wb-note">
          📣 {task.mr_iid ? t("workbench.notifiedOwners") : t("workbench.pendingNotifyOwners")}
          {r.owners.length
            ? t("workbench.ownersJoin", { owners: r.owners.join(t("projects.listSep")) })
            : t("workbench.ownersUnset")}
        </div>
      )}
      {task.error && (
        <div className="wb-error" title={task.error}>
          ⚠ {task.error}
        </div>
      )}
    </div>
  );
}

export default function Workbench() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { groupId } = useGroup();
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<number | undefined>();
  const [reqs, setReqs] = useState<Req[]>([]);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});

  const active = reqs;

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("system_id", String(projectId));
    const d = await api<{ requirements: Req[] }>(`/api/workbench?${p}`);
    setReqs(d.requirements);
  };
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [groupId, projectId]);
  useEffect(() => {
    api<any[]>("/api/systems").then(setProjects);
  }, []);
  useEffect(() => {
    setProjectId(undefined); // 切组后旧项目筛选可能不属于该组
  }, [groupId]);

  // 专家卡片显示 agent-memory 里的专家名：按项目团队拉一次；拉不到就回落到 id
  useEffect(() => {
    const teams = [...new Set(projects.map((p) => p.am_team_id).filter(Boolean))] as string[];
    teams.forEach((teamId) => {
      api<any[]>(`/api/am/agents?team_id=${encodeURIComponent(teamId)}`)
        .then((list) =>
          setAgentNames((prev) => ({ ...prev, ...Object.fromEntries(list.map((a: any) => [a.id, a.name])) }))
        )
        .catch(() => {});
    });
  }, [projects]);

  const agentLabel = (id?: string | null, name?: string | null) =>
    id ? agentNames[id] || (name && name !== id ? name : id) : null;
  /** 节点依赖的专家：需求/方案取对话里选的 agent，开发取项目上配置的专家 */
  const nodeExpert = (r: Req, key: string): string | null => {
    if (key === "doc") return agentLabel(r.doc_agent?.id, r.doc_agent?.name);
    if (key === "plan") return agentLabel(r.plan_agent?.id, r.plan_agent?.name);
    if (key === "dev") {
      const project = projects.find((p) => p.id === r.system_id);
      return agentLabel(project?.am_agent_id, null);
    }
    return null;
  };

  const projOptions = projects
    .filter((p) => !groupId || p.group_id === groupId)
    .map((p) => ({ value: p.id, label: `${p.path}（${p.name}）` }));

  const liveTasks = active.reduce((n, r) => n + r.tasks.filter((task) => flowCls(task) === "live").length, 0);
  const reviewMrs = active.reduce(
    (n, r) =>
      n +
      r.tasks.filter(
        (task) => task.mr_iid && !task.outcome && ["mr_created", "reviewing", "cicd"].includes(task.status),
      ).length,
    0
  );
  const stats: [string, number, string][] = [
    [t("workbench.statActive"), active.length, ""],
    [t("workbench.statRunning"), liveTasks, "live"],
    [t("workbench.statReview"), reviewMrs, "wait"],
  ];

  return (
    <Page title={t("nav.workbench")} desc={t("workbench.desc")}>
      <div className="wb-bar">
        <Select
          style={{ width: 280 }}
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder={t("workbench.projectPlaceholder")}
          value={projectId}
          onChange={setProjectId}
          options={projOptions}
        />
      </div>
      <Tabs
        defaultActiveKey="flow"
        items={[
          {
            key: "flow",
            label: t("workbench.tabFlow"),
            children: (
              <>
        <div className="wb-bar">
        <div className="wb-legend">
          <span><i className="wb-dot live" /> {t("workbench.legendLive")}</span>
          <span><i className="wb-dot ok" /> {t("workbench.legendOk")}</span>
          <span><i className="wb-dot bad" /> {t("workbench.legendBad")}</span>
          <span><i className="wb-dot idle" /> {t("workbench.legendIdle")}</span>
        </div>
      </div>

      <div className="wb-stats">
        {stats.map(([label, n, tone]) => (
          <div className={`wb-stat ${tone}`} key={label}>
            <b>{n}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className="page-card wb-flow">
        <span className="wb-live-chip">
          <i /> {t("workbench.live")}
        </span>
        {active.length === 0 ? (
          <Empty
            description={t("workbench.empty")}
            style={{ padding: "48px 0" }}
          />
        ) : (
          <div className="wb-board">
            {active.map((r) => {
              const nodes = reqNodes(r);
              const keys = nodeKeys(r);
              const cardsByNode: Record<string, Task[]> = {};
              r.tasks.forEach((task) => {
                const k = taskStage(task, r);
                (cardsByNode[k] ||= []).push(task);
              });
              const lastCard = Math.max(-1, ...r.tasks.map((task) => keys.indexOf(taskStage(task, r))));
              return (
                <div className="wb-line" key={r.id}>
                  <div className="wb-chain">
                    {/* 需求信息直接做成流程起点的卡片，不额外加标题行 */}
                    <div
                      className={`wb-card wb-req-card${r.can_open ? "" : " muted-card"}`}
                      title={r.can_open ? undefined : t("errors.denied")}
                      onClick={() => (r.can_open ? nav(`/reqs/${r.id}`) : message.info(t("errors.denied")))}
                    >
                      <div className="wb-req-top">
                        <Tag color={REQ_STATUS[r.status]?.[0] ?? "default"}>
                          {REQ_STATUS[r.status] ? t(REQ_STATUS[r.status][1]) : r.status}
                        </Tag>
                        <span className="mono wb-req-code">{r.code}</span>
                      </div>
                      <div className="wb-req-title" title={r.title}>
                        {r.title}
                      </div>
                      <div className="wb-req-meta">
                        {r.system_name} · {r.claimant_name || t("workbench.unclaimed")} ·{" "}
                        {t("workbench.taskCount", { n: r.tasks.length })}
                        {r.created_at ? ` · ${t("workbench.created", { time: fmtT(r.created_at) ?? "" })}` : ""}
                      </div>
                    </div>
                    {/* 未开始的节点不展示；任务卡并进它当前所在的阶段节点（同一状态只画一张卡）。
                        有任务停在后段时，中间的空节点也画出来，保证轨道不断。 */}
                    {nodes.map((n, i) => {
                      const cards = cardsByNode[n.key] || [];
                      if (n.state === "idle" && i > lastCard) return null;
                      const expert = nodeExpert(r, n.key);
                      const info = nodeInfo(r, n.key, n.state);
                      return (
                        <Fragment key={n.key}>
                          <i className={`wb-link ${n.state}`} />
                          <div className={`wb-node ${n.state}`}>
                            {expert && (
                              <>
                                <div className="wb-expert">
                                  <span>{t(EXPERT_ROLE[n.key])}</span>
                                  <b title={expert}>{expert}</b>
                                </div>
                                <i className={`wb-vlink ${n.state}`} />
                              </>
                            )}
                            {cards.length ? (
                              <div className="wb-cards">
                                {cards.map((task) => (
                                  <TaskCard
                                    key={task.id}
                                    task={task}
                                    r={r}
                                    onOpen={() => nav(`/tasks/${task.id}`)}
                                  />
                                ))}
                              </div>
                            ) : (
                              <div className={`wb-step ${n.state}`}>
                                <b>{t(n.label)}</b>
                                <small title={info.main}>{info.main}</small>
                                {info.sub && <small title={info.sub}>{info.sub}</small>}
                                {info.at && <time>{info.at}</time>}
                              </div>
                            )}
                          </div>
                        </Fragment>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

              </>
            ),
          },
          {
            key: "stats",
            label: t("workbench.tabStats"),
            children: <CodeStats groupId={groupId ?? undefined} projectId={projectId} />,
          },
        ]}
      />
    </Page>
  );
}
