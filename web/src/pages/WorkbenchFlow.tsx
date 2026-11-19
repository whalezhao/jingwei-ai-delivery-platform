import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Graph as G6Graph, GraphData } from "@antv/g6";
import { useTranslation } from "react-i18next";
import {
  Bell,
  Code2,
  FileCheck2,
  FileText,
  FlaskConical,
  GitMerge,
  GitPullRequest,
  Lightbulb,
  ListChecks,
  Rocket,
  type LucideIcon,
} from "lucide-react";
import dayjs from "dayjs";
import i18n, { type MsgKey } from "../i18n";
import EgmMark from "../components/EgmMark";
import Badge, { type BadgeTone } from "../ui/Badge";
import { toast } from "../ui/toast";

/**
 * 工作台流程图：G6 5 画布 + React 节点。
 * 每个需求一行：需求卡 → 阶段卡/任务卡（任务并进它当前所处的阶段），
 * 阶段按节点状态给连线上色、流动。位置由本文件手算（按行、贴底对齐），
 * 高度先离屏量一次真实 DOM，避免卡片被裁；5s 轮询只换数据，视图不跳。
 */

export interface Mr {
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
export interface Task {
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
  can_open: boolean;
  // 0 = 首轮；>0 = 返工后重新派发的任务
  rework_round?: number;
}
export interface Req {
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
  plan_egm: boolean;
  review_on: boolean;
  merge_on: boolean;
  owners: string[];
  doc_agent: { id: string; name: string } | null;
  plan_agent: { id: string; name: string } | null;
  // 部署/测试泳道：系统配了环境映射才画 deploy
  deploy_on: boolean;
  deploy: { environment: string; sha: string; at: string | null } | null;
  test?: { status: string; pass_rate: number | null } | null;
  // 方案返工回路：非空表示正在由方案专家修订 / 等复审
  rework?: { round: number; state: string } | null;
  tasks: Task[];
}

const fmtT = (s?: string | null) => (s ? dayjs(s).format("MM-DD HH:mm") : null);
const fullT = (s?: string | null) => (s ? dayjs(s).format("YYYY-MM-DD HH:mm:ss") : "—");
const stamped = (key: MsgKey, s?: string | null) => (s ? `${i18n.t(key)} ${fmtT(s)}` : null);

const REQ_STATUS: Record<string, [BadgeTone, MsgKey]> = {
  pool: ["neutral", "workbench.reqStatus.pool"],
  claimed: ["info", "workbench.reqStatus.claimed"],
  confirmed: ["success", "workbench.reqStatus.confirmed"],
  archived: ["neutral", "workbench.reqStatus.archived"],
  online: ["violet", "workbench.reqStatus.online"],
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
export function flowCls(task: Task): string {
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
function taskPhase(task: Task, r: Req): [BadgeTone, MsgKey] {
  if (task.outcome === "merged") return ["success", "workbench.phase.merged"];
  if (task.outcome === "closed") return ["warning", "workbench.phase.closed"];
  if (task.outcome === "failed" || task.status === "failed") return ["danger", "workbench.phase.failed"];
  if (task.mr_iid || task.status === "pushed") {
    if (!r.review_on) return ["info", task.mr_iid ? "workbench.phase.pendingMerge" : "workbench.phase.submitting"];
    const m = task.mr;
    if (!m || m.verdict === "pending" || m.verdict === "modify") return ["info", "workbench.phase.review"];
    if (m.verdict === "failed") return ["danger", "workbench.phase.reviewFailed"];
    if (m.gate) return ["info", "workbench.phase.ci"];
    return ["info", r.merge_on ? "workbench.phase.autoMerging" : "workbench.phase.pendingMerge"];
  }
  if (task.status === "pending") return ["neutral", "workbench.phase.grabbing"];
  return ["info", "workbench.phase.developing"];
}

type NodeKey =
  | "doc"
  | "plan"
  | "dev"
  | "review"
  | "ci"
  | "merge"
  | "deploy"
  | "test"
  | "notify";
type St = "ok" | "live" | "idle" | "bad";

/** 测试批次状态 → 泳道节点状态 */
function testState(s?: string | null): St {
  if (s === "completed") return "ok";
  if (s === "completed_with_failures" || s === "failed" || s === "cancelled") return "bad";
  if (s === "queued" || s === "running") return "live";
  return "idle";
}

const NODE_LABEL: Record<NodeKey, MsgKey> = {
  doc: "workbench.node.doc",
  plan: "workbench.node.plan",
  dev: "workbench.node.dev",
  review: "workbench.node.review",
  ci: "workbench.node.ci",
  merge: "workbench.node.merge",
  deploy: "workbench.node.deploy",
  test: "workbench.node.test",
  notify: "workbench.node.notify",
};

/** 项目配置决定轨道长度：评审/CI 看 audit_prefixes，自动合并看 auto_approve_prefixes；
 *  缺哪段就在 MR 建好后交给负责人（octo 通知），不再画空节点。 */
function nodeKeys(r: Req): NodeKey[] {
  // Bug 没有需求文档阶段，轨道从方案开始
  const keys: NodeKey[] = r.req_type === "bug" ? ["plan", "dev"] : ["doc", "plan", "dev"];
  if (r.review_on) keys.push("review", "ci");
  if (r.review_on && r.merge_on) keys.push("merge");
  // 部署泳道只给配了环境映射的系统画；测试只在需求上线后才有意义
  if (r.deploy_on) keys.push("deploy");
  if (r.status === "online") keys.push("test");
  keys.push("notify");
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
  // 部署/测试是需求级状态，不看单个任务：部署过 = ok，测试批次状态各自映射
  if (r.deploy_on) state.deploy = r.deploy ? "ok" : "idle";
  if (keys.includes("test")) state.test = testState(r.test?.status);
  // 方案返工期间把「方案」节点点亮：CRG 判不可通过 → 专家修订 → 复审 → 重新派发
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

type FlowCtx = {
  onOpenReq: (r: Req) => void;
  onOpenTask: (task: Task) => void;
};
type FlowStage = {
  kind: "stage";
  req: Req;
  key: NodeKey;
  label: MsgKey;
  state: St;
  cards: Task[];
  expert: string | null;
  info: ReturnType<typeof nodeInfo>;
};
type FlowDatum =
  | { kind: "req"; req: Req }
  | { kind: "rework"; req: Req }
  | FlowStage;

/** 任务卡：并进流程里它当前所处的阶段节点，带该阶段的必要信息 */
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
          onClick={task.can_open ? undefined : () => toast.info(denied)}
        >
          {task.title || task.code}
        </span>
        <span
          className="wb-task-time"
          title={t("workbench.createdUpdated", { created: fullT(task.created_at), updated: fullT(task.updated_at) })}
        >
          🕒 {fmtT(task.updated_at) || "—"}
        </span>
        <Badge tone={phase[0]} className="wb-phase">{t(phase[1])}</Badge>
      </div>
      <div className="wb-chips">
        <a
          className={`wb-chip mono${task.can_open ? "" : " muted"}`}
          title={task.can_open ? t("workbench.viewTask", { code: task.code }) : denied}
          onClick={task.can_open ? onOpen : () => toast.info(denied)}
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
            onClick={task.can_open ? onOpen : () => toast.info(denied)}
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

function ReqCard({ req, ctx }: { req: Req; ctx: FlowCtx }) {
  const { t } = useTranslation();
  return (
    <div
      className={`wb-card wb-req-card s-${req.status}${req.can_open ? "" : " muted-card"}`}
      title={req.can_open ? undefined : t("errors.denied")}
      onClick={() => (req.can_open ? ctx.onOpenReq(req) : toast.info(t("errors.denied")))}
    >
      <div className="wb-req-top">
        <Badge tone={REQ_STATUS[req.status]?.[0] ?? "neutral"}>
          {REQ_STATUS[req.status] ? t(REQ_STATUS[req.status][1]) : req.status}
        </Badge>
        <span className={`wb-req-type${req.req_type === "bug" ? " bug" : ""}`}>
          {t(req.req_type === "bug" ? "requirements.typeBug" : "requirements.typeRequirement")}
        </span>
        <span className="mono wb-req-code">{req.code}</span>
      </div>
      <div className="wb-req-title" title={req.title}>
        {req.title}
      </div>
      <div className="wb-req-meta">
        {req.system_name} · {req.claimant_name || t("workbench.unclaimed")} ·{" "}
        {t("workbench.taskCount", { n: req.tasks.length })}
        {req.created_at ? ` · ${t("workbench.created", { time: fmtT(req.created_at) ?? "" })}` : ""}
      </div>
    </div>
  );
}

/** 节点内容：贴底排，专家卡在上、任务/阶段卡在下 */
function NodeBody({ datum, ctx }: { datum: FlowDatum; ctx: FlowCtx }) {
  const { t } = useTranslation();
  if (datum.kind === "req") return <ReqCard req={datum.req} ctx={ctx} />;
  if (datum.kind === "rework") {
    const round = datum.req.rework?.round ?? 1;
    return (
      <div className="wb-rework" title={t("workbench.rework")}>
        <b>{t("workbench.reworkCard", { n: round })}</b>
        <small>
          {datum.req.rework?.state === "reviewing"
            ? t("workbench.reworkReviewing")
            : t("workbench.reworkPlanning", { n: round })}
        </small>
      </div>
    );
  }
  const { req, state, cards, expert, info, label } = datum as FlowStage;
  return (
    <div className={`wb-node ${state}`}>
      {expert && (
        <>
          <div className="wb-expert">
            <span>{t(EXPERT_ROLE[datum.key])}</span>
            <b title={expert}>{expert}</b>
          </div>
          <i className={`wb-vlink ${state}`} />
        </>
      )}
      {cards.length ? (
        <div className="wb-cards">
          {cards.map((task) => (
            <TaskCard key={task.id} task={task} r={req} onOpen={() => ctx.onOpenTask(task)} />
          ))}
        </div>
      ) : (
        <div className={`wb-step ${state}`}>
          <b>
            {t(label)}
            {datum.key === "plan" && req.plan_egm && (
              <span className="wb-egm" title={t("workbench.egmPlan")}>
                <EgmMark size={11} />
              </span>
            )}
          </b>
          <small title={info.main}>{info.main}</small>
          {info.sub && <small title={info.sub}>{info.sub}</small>}
          {info.at && <time>{info.at}</time>}
        </div>
      )}
    </div>
  );
}

const REQ_W = 232;
/** 还没量到容器宽度时用的阶段列宽 */
const CARD_W = 252;
const GAP = 34;
const ROW_GAP = 26;
/** 两轮之间留出走线空间，返工回返箭头折在这条空隙里 */
const REWORK_GAP = 64;
/** 表格边框与首/末列卡片之间的留白 */
const PAD = 16;
/** 连线接在卡片底部往上 26px 处（与旧流程板一致） */
const PORT_UP = 26;

type Draft = { id: string; datum: FlowDatum; w: number };

function agentLabel(agentNames: Record<string, string>, id?: string | null, name?: string | null) {
  return id ? agentNames[id] || (name && name !== id ? name : id) : null;
}

/** 每行 = 需求卡 + 已到达的阶段；未开始的尾段不画，中间空节点保留保证轨道不断 */
function buildRows(
  reqs: Req[],
  projects: any[],
  agentNames: Record<string, string>,
  stageW: number,
): Draft[][] {
  return reqs.flatMap((r) => {
    const keys = nodeKeys(r);
    const round = r.rework?.round ?? 0;
    // 返工是一次新的执行：上一轮的卡片不复用，两轮画成两行
    const pass0 = round ? r.tasks.filter((t) => (t.rework_round ?? 0) === 0) : r.tasks;
    const passN = round ? r.tasks.filter((t) => (t.rework_round ?? 0) === round) : [];
    const project = projects.find((p) => p.id === r.system_id);
    const expertFor = (key: NodeKey) => {
      if (key === "doc") return agentLabel(agentNames, r.doc_agent?.id, r.doc_agent?.name);
      if (key === "plan") return agentLabel(agentNames, r.plan_agent?.id, r.plan_agent?.name);
      if (key === "dev") return agentLabel(agentNames, project?.am_agent_id, null);
      return null;
    };

    const stageRow = (tasks: Task[], tag: string, reworkPlan: boolean): Draft[] => {
      const cardsByNode: Record<string, Task[]> = {};
      tasks.forEach((task) => {
        const k = taskStage(task, r);
        (cardsByNode[k] ||= []).push(task);
      });
      const lastCard = Math.max(-1, ...tasks.map((task) => keys.indexOf(taskStage(task, r))));
      const out: Draft[] = [];
      // 每轮只看本轮的任务：卡片数、状态文案都按这一行自己的任务算
      const view = tasks === r.tasks ? r : { ...r, tasks };
      reqNodes(r).forEach((n, i) => {
        // 返工件本身可能在还没派发新任务时就该显示，不跟随后面的空阶段一起裁掉
        const keepReworkPlan = reworkPlan && n.key === "plan";
        if (n.state === "idle" && i > lastCard && !keepReworkPlan) return;
        if (n.key === "plan" && reworkPlan) {
          out.push({ id: `req-${r.id}-rework`, w: stageW, datum: { kind: "rework", req: r } });
          return;
        }
        out.push({
          id: `req-${r.id}-${tag}${n.key}`,
          w: stageW,
          datum: {
            kind: "stage",
            req: r,
            key: n.key,
            label: n.label,
            state: n.state,
            cards: cardsByNode[n.key] || [],
            expert: expertFor(n.key),
            info: nodeInfo(view, n.key, n.state),
          },
        });
      });
      return out;
    };

    const rows: Draft[][] = [
      [
        { id: `req-${r.id}`, w: REQ_W, datum: { kind: "req", req: r } },
        ...stageRow(pass0, "", false),
      ],
    ];
    if (round && r.rework) {
      // 第二轮：返工方案 → 新的开发/评审/CI/合并/通知卡片
      rows.push(stageRow(passN, `p${round}-`, true));
    }
    return rows;
  });
}

/** 阶段泳道按固定顺序排列，全部始终显示；列宽等分容器宽度 */
const LANE_ORDER: NodeKey[] = [
  "doc",
  "plan",
  "dev",
  "review",
  "ci",
  "merge",
  "deploy",
  "test",
  "notify",
];
const LANE_HEAD_H = 30;
/** 阶段图标：与侧栏菜单同一套 lucide 线性图标 */
const LANE_ICON: Record<NodeKey, LucideIcon> = {
  doc: FileCheck2,
  plan: Lightbulb,
  dev: Code2,
  review: GitPullRequest,
  ci: FlaskConical,
  merge: GitMerge,
  deploy: Rocket,
  test: ListChecks,
  notify: Bell,
};
/** 阶段列最窄宽度：再窄就没法读，改为整体缩放 */
const MIN_STAGE_W = 104;

/** 阶段列等分容器宽度（留出需求列、列间距与两侧留白） */
function stageWidth(availW: number): number {
  if (!availW) return CARD_W;
  const n = LANE_ORDER.length;
  return Math.max(MIN_STAGE_W, (availW - PAD * 2 - REQ_W - GAP * n) / n);
}

/** 按测出的高度排版：列按阶段固定，行内贴底对齐，行高取该行最高的节点 */
function layout(
  rows: Draft[][],
  heights: Record<string, number>,
  stageW: number,
): { data: GraphData; width: number; height: number } {
  const nodes: any[] = [];
  const edges: any[] = [];

  const keys = LANE_ORDER;
  const colX: Record<string, number> = {};
  let x = PAD + REQ_W + GAP;
  keys.forEach((k) => {
    colX[k] = x;
    x += stageW + GAP;
  });
  const width = Math.max(x - GAP + PAD, 1);

  let top = LANE_HEAD_H + 8;
  rows.forEach((row, ri) => {
    const rework = row.find((d) => (d.datum as any).kind === "rework");
    const hs = row.map((d) => heights[d.id] || 80);
    const reworkH = rework ? heights[rework.id] || 60 : 0;
    const rowH = Math.max(...hs);

    row.forEach((d, i) => {
      if ((d.datum as any).kind === "rework") return; // 单独定位，不按泳道排
      const h = hs[i];
      const w = i === 0 ? REQ_W : stageW;
      const nx = i === 0 ? PAD : colX[(d.datum as FlowStage).key];
      nodes.push({
        id: d.id,
        data: d.datum,
        style: { x: nx + w / 2, y: top + rowH - h / 2, size: [w, h] },
      });
      if (i > 0) {
        const state = (d.datum as FlowStage).state;
        edges.push({
          // 状态进 id：状态一变就重建边，蚂蚁线动画才会重启
          id: `e-${d.id}-${state}`,
          source: row[i - 1].id,
          target: d.id,
          data: { state },
          style: { sourcePort: "out", targetPort: "in" },
        });
      }
    });

    if (rework) {
      const reworkX = colX.plan + stageW / 2;
      const reworkY = top + rowH - reworkH / 2;
      nodes.push({
        id: rework.id,
        data: rework.datum,
        style: { x: reworkX, y: reworkY, size: [stageW, reworkH] },
      });
      const reqId = (rework.datum as { req: Req }).req.id;
      const reviewId = `req-${reqId}-review`;
      // 上一轮的代码评审 → 本轮的返工方案（打回）；本轮内部的连线由下面的相邻节点循环生成
      const from = nodes.find((n) => n.id === reviewId);
      if (from) {
        // 走两行之间的空隙折返，不压任何卡片：评审卡底 → 行间 → 返工卡顶
        const srcX = from.style.x;
        const srcBottom = from.style.y + from.style.size[1] / 2;
        const bandY = srcBottom + (top - srcBottom) / 2;
        edges.push({
          id: `e-rework-in-${reqId}`,
          source: reviewId,
          target: rework.id,
          data: { state: "rework", via: [[srcX, bandY], [reworkX, bandY]] },
          style: { sourcePort: "down", targetPort: "up" },
        });
      }
    }
    // 下一行是本需求的返工段时，多留一条带子给回返箭头
    const nextIsRework = (rows[ri + 1] || []).some((d) => (d.datum as any).kind === "rework");
    top += rowH + (nextIsRework ? REWORK_GAP : ROW_GAP);
  });

  const height = Math.max(top, 1);
  // 泳道：表头下沿一条渐隐细线 + 贯穿到底的列分隔线，压在连线和卡片下面（zIndex 低于边的 -1）
  nodes.push({
    id: "lane-header-rule",
    data: { kind: "laneRule" },
    style: { x: width / 2, y: LANE_HEAD_H - 0.5, size: [width, 1], zIndex: -2 },
  });
  const line = (id: string, x: number, y: number, size: [number, number]) =>
    nodes.push({ id, data: { kind: "lane", variant: "sep" }, style: { x, y, size, zIndex: -2 } });
  // 外框收边，让泳道读成一张表
  line("lane-frame-l", 0.5, height / 2, [1, height]);
  line("lane-frame-r", width - 0.5, height / 2, [1, height]);
  line("lane-frame-b", width / 2, height - 0.5, [width, 1]);
  keys.forEach((k) => {
    nodes.push({
      id: `lane-sep-${k}`,
      data: { kind: "lane", variant: "sep" },
      // zIndex 写在数据里：G6 只在新增元素时读 datum.style.zIndex 决定绘制层级
      style: { x: colX[k] - GAP / 2, y: height / 2, size: [1, height], zIndex: -2 },
    });
    nodes.push({
      id: `lane-head-${k}`,
      data: { kind: "laneHead", text: i18n.t(NODE_LABEL[k]), icon: LANE_ICON[k] },
      style: { x: colX[k] + stageW / 2, y: LANE_HEAD_H / 2, size: [stageW, LANE_HEAD_H] },
    });
  });
  nodes.push({
    id: "lane-head-req",
    data: { kind: "laneHead", text: i18n.t("workbench.node.req"), icon: FileText },
    style: { x: PAD + REQ_W / 2, y: LANE_HEAD_H / 2, size: [REQ_W, LANE_HEAD_H] },
  });

  return {
    data: { nodes, edges },
    width,
    height,
  };
}

const EDGE_COLOR: Record<string, string> = {
  ok: "#1e8a5b",
  live: "#2b4acb",
  bad: "#c23934",
  idle: "#bfc3ca",
};

let registered = false;

export default function WorkbenchFlow({
  reqs,
  projects,
  agentNames,
  onOpenReq,
  onOpenTask,
}: {
  reqs: Req[];
  projects: any[];
  agentNames: Record<string, string>;
  onOpenReq: (r: Req) => void;
  onOpenTask: (task: Task) => void;
}) {
  const { t } = useTranslation();
  const hostRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<G6Graph | null>(null);
  const builtRef = useRef<ReturnType<typeof layout> | null>(null);
  /** 上次适配时的画布尺寸与内容宽度；一致就不动相机，保住用户的缩放/平移 */
  const viewRef = useRef({ w: 0, h: 0, cw: 0 });
  const ctxRef = useRef<FlowCtx>({ onOpenReq, onOpenTask });
  ctxRef.current = { onOpenReq, onOpenTask };
  const [zoom, setZoom] = useState(1);
  const [hint, setHint] = useState(false);
  const zoomApi = useRef<{ by: (ratio: number) => void; reset: () => void }>({ by: () => {}, reset: () => {} });
  /** 容器宽度：泳道按它铺满，跟着浏览器宽度重排 */
  const [availW, setAvailW] = useState(0);

  const stageW = stageWidth(availW);
  const rows = useMemo(
    () => buildRows(reqs, projects, agentNames, stageW),
    [reqs, projects, agentNames, stageW, i18n.language],
  );
  const [heights, setHeights] = useState<Record<string, number>>({});
  const [viewH, setViewH] = useState(0);

  // 先量真实 DOM 高度再排版，避免按估算尺寸把卡片裁掉
  useLayoutEffect(() => {
    const host = measureRef.current;
    if (!host) return;
    const next: Record<string, number> = {};
    host.querySelectorAll<HTMLElement>("[data-wb-id]").forEach((el) => {
      next[el.dataset.wbId!] = Math.ceil(el.getBoundingClientRect().height);
    });
    setHeights((prev) => {
      const ids = Object.keys(next);
      if (ids.length === Object.keys(prev).length && ids.every((id) => prev[id] === next[id])) return prev;
      return next;
    });
  });

  const built = useMemo(() => layout(rows, heights, stageW), [rows, heights, stageW]);
  builtRef.current = built;

  /**
   * 把最新布局应用到画布：宽度变化时按「刚好铺满视口」重算缩放与高度，
   * 否则只换数据——5s 轮询不能把用户手动缩放/平移的视角顶回去。
   */
  const apply = useCallback((graph: G6Graph) => {
    const b = builtRef.current!;
    const w = hostRef.current?.clientWidth || b.width;
    // 2px 死区：滚动条出现/消失导致的抖动不触发重排
    setAvailW((prev) => (Math.abs(prev - w) < 2 ? prev : w));
    const scale = Math.min(1, w / b.width); // 只缩不放：装得下就保持 1:1
    const h = Math.max(1, Math.ceil(b.height * scale));
    const v = viewRef.current;
    if (v.w !== w || v.h !== h || v.cw !== b.width) {
      graph.resize(w, h);
      // resize 会把相机恢复到旧尺寸下的位置。g6 的 translateTo 以画布中心为基准，
      // 所以按 scale 补偿后画布原点才真正落在左上角
      graph.zoomTo(scale, false, [0, 0]);
      graph.translateTo([(w / 2) * (scale - 1), (h / 2) * (scale - 1)]);
      viewRef.current = { w, h, cw: b.width };
      setViewH(h);
      setZoom(graph.getZoom());
    }
    graph.setData(b.data);
    graph.render();
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let wheel: ((e: WheelEvent) => void) | undefined;
    let ro: ResizeObserver | undefined;
    let detachPan: (() => void) | undefined;
    let hintTimer: number | undefined;
    (async () => {
      const [{ Graph, ExtensionCategory, register, Line }, { ReactNode }] = await Promise.all([
        import("@antv/g6"),
        import("@antv/g6-extension-react"),
      ]);
      if (disposed) return;

      if (!registered) {
        registered = true;
        register(ExtensionCategory.NODE, "workbench-node", ReactNode);

        class FlowEdge extends Line {
          onCreate() {
            // 元素属性里只有 style，状态靠 style.wbState 带进来
            const state = (this as any).attributes?.wbState;
            if (state !== "live" && state !== "bad") return;
            if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
            // lineDashOffset 变小，虚线才沿路径向前走（变大就退回去了）；-16 = 两个周期，循环无跳变
            (this as any).shapeMap?.key?.animate?.(
              [{ lineDashOffset: 0 }, { lineDashOffset: -16 }],
              { duration: 700, iterations: Infinity },
            );
          }
        }
        register(ExtensionCategory.EDGE, "workbench-edge", FlowEdge);
      }

      const graph = new Graph({
        container: host,
        width: built.width,
        height: built.height,
        data: { nodes: [], edges: [] },
        zoomRange: [0.2, 2],
        node: {
          // 泳道底用内置 rect，其余走 React 节点；G6 的类型以这里为准，datum.type 会被忽略
          type: (d: any) => (d.data?.kind === "lane" ? "rect" : "workbench-node"),
          style: (d: any) => {
            const [w, h] = (d.style?.size as [number, number] | undefined) ?? [100, 80];
            if (d.data?.kind === "laneHead") {
              const Ic = d.data.icon as LucideIcon | undefined;
              return {
                size: [w, h],
                dx: -w / 2,
                dy: -h / 2,
                component: (
                  <div className="wb-lane-head">
                    {Ic && <Ic className="wb-lane-icon" strokeWidth={1.8} aria-hidden />}
                    <span className="wb-lane-text">{d.data.text}</span>
                  </div>
                ),
              };
            }
            if (d.data?.kind === "laneRule") {
              return {
                size: [w, h],
                dx: -w / 2,
                dy: -h / 2,
                component: <div className="wb-lane-rule" />,
              };
            }
            if (d.data?.kind === "lane") {
              return {
                size: [w, h],
                radius: 0,
                fill: "rgba(20,23,28,0.15)",
                stroke: "transparent",
                lineWidth: 0,
              };
            }
            return {
              size: [w, h],
              dx: -w / 2,
              dy: -h / 2,
              component: <NodeBody datum={d.data as FlowDatum} ctx={ctxRef.current} />,
              ports: [
                { key: "in", placement: [0, 1 - PORT_UP / h] },
                { key: "out", placement: [1, 1 - PORT_UP / h] },
                // 返工回返箭头专用：从卡片底边下、顶边上，避开 bbox 取交点的偏移
                { key: "down", placement: [0.5, 1] },
                { key: "up", placement: [0.5, 0] },
              ],
            };
          },
        },
        edge: {
          // 返工回路用折线走两行之间的空隙：直连会把中间的开发 / 评审卡片划穿
          type: (d: any) => (d.data?.state === "rework" ? "polyline" : "workbench-edge"),
          style: (d: any) => {
            const state = (d.data?.state as string) || "idle";
            if (state === "rework") {
              return {
                stroke: "#d97706",
                lineWidth: 1.8,
                lineDash: [6, 4],
                endArrow: true,
                endArrowType: "triangle",
                endArrowSize: 9,
                controlPoints: d.data?.via,
                radius: 10,
              };
            }
            return {
              stroke: EDGE_COLOR[state] || EDGE_COLOR.idle,
              lineWidth: 1.5,
              // 只有进行中/失败才走虚线，静态链路保持实线，箭头方向一眼可读
              lineDash: state === "live" || state === "bad" ? [4, 4] : undefined,
              endArrow: true,
              endArrowType: "triangle",
              endArrowSize: 8,
              wbState: state,
            };
          },
        },
        animation: false,
      });

      graphRef.current = graph;
      await graph.render();
      if (disposed) return;
      apply(graph);

      // 普通滚轮留给页面滚动；Ctrl/⌘ + 滚轮（含触控板双指捏合）才缩放
      const showHint = () => {
        setHint(true);
        window.clearTimeout(hintTimer);
        hintTimer = window.setTimeout(() => setHint(false), 1800);
      };
      const syncZoom = () => setZoom(graph.getZoom());
      zoomApi.current = {
        by: (ratio) => {
          graph.zoomBy(ratio);
          syncZoom();
        },
        reset: () => {
          viewRef.current = { w: 0, h: 0, cw: 0 };
          apply(graph);
        },
      };
      wheel = (e: WheelEvent) => {
        if (!e.ctrlKey && !e.metaKey) {
          showHint();
          return;
        }
        e.preventDefault();
        const rect = host.getBoundingClientRect();
        graph.zoomBy(e.deltaY > 0 ? 0.9 : 1.1, false, [e.clientX - rect.left, e.clientY - rect.top]);
        syncZoom();
      };
      host.addEventListener("wheel", wheel, { passive: false });

      // 卡片是真实 DOM 盖在画布上，G6 的 drag-canvas 只能拖空白处；
      // 这里在容器上接管指针，整块都能拖（超过 6px 才算拖，点卡片仍是点击）
      let pan: { x: number; y: number } | null = null;
      let moved = false;
      const onDown = (e: PointerEvent) => {
        if (e.button !== 0) return;
        pan = { x: e.clientX, y: e.clientY };
        moved = false;
      };
      const onMove = (e: PointerEvent) => {
        if (!pan) return;
        const dx = e.clientX - pan.x;
        const dy = e.clientY - pan.y;
        if (!moved && Math.abs(dx) + Math.abs(dy) < 6) return;
        if (!moved) {
          moved = true;
          host.style.userSelect = "none";
        }
        pan = { x: e.clientX, y: e.clientY };
        graph.translateBy([dx, dy]);
      };
      const onUp = () => {
        pan = null;
        if (moved) host.style.userSelect = "";
      };
      // 拖完松手时浏览器还会补一个 click，别让它顺手点开卡片
      const onClickCapture = (e: MouseEvent) => {
        if (moved) {
          moved = false;
          e.stopPropagation();
          e.preventDefault();
        }
      };
      // 双击空白处复位到适配视图
      const onDblClick = (e: MouseEvent) => {
        if (e.target instanceof HTMLCanvasElement) zoomApi.current.reset();
      };
      host.addEventListener("pointerdown", onDown);
      host.addEventListener("pointermove", onMove);
      host.addEventListener("pointerup", onUp);
      host.addEventListener("pointercancel", onUp);
      host.addEventListener("click", onClickCapture, true);
      host.addEventListener("dblclick", onDblClick);
      detachPan = () => {
        host.removeEventListener("pointerdown", onDown);
        host.removeEventListener("pointermove", onMove);
        host.removeEventListener("pointerup", onUp);
        host.removeEventListener("pointercancel", onUp);
        host.removeEventListener("click", onClickCapture, true);
        host.removeEventListener("dblclick", onDblClick);
      };

      // 视口宽度变化（窗口/侧栏折叠）时重新适配
      ro = new ResizeObserver(() => {
        if (graphRef.current === graph) apply(graph);
      });
      ro.observe(host);
    })();

    return () => {
      disposed = true;
      ro?.disconnect();
      detachPan?.();
      window.clearTimeout(hintTimer);
      zoomApi.current = { by: () => {}, reset: () => {} };
      if (wheel) host.removeEventListener("wheel", wheel);
      graphRef.current?.destroy();
      graphRef.current = null;
      viewRef.current = { w: 0, h: 0, cw: 0 };
    };
  }, [apply]);

  // 轮询、语言切换、筛选：只换数据；宽度或内容尺寸变了才重新适配
  useEffect(() => {
    const graph = graphRef.current;
    if (graph) apply(graph);
  }, [built, apply]);

  return (
    <>
      <div ref={hostRef} style={{ width: "100%", height: viewH || built.height, overflow: "hidden" }} />
      <div className="wb-zoom">
        <button type="button" onClick={() => zoomApi.current.by(1 / 1.2)} title={t("workbench.zoomOut")} aria-label={t("workbench.zoomOut")}>
          −
        </button>
        <button type="button" onClick={() => zoomApi.current.reset()} title={t("workbench.zoomReset")} aria-label={t("workbench.zoomReset")}>
          {Math.round(zoom * 100)}%
        </button>
        <button type="button" onClick={() => zoomApi.current.by(1.2)} title={t("workbench.zoomIn")} aria-label={t("workbench.zoomIn")}>
          +
        </button>
      </div>
      {hint && <div className="wb-hint" role="status">{t("workbench.zoomHint")}</div>}
      <div
        ref={measureRef}
        aria-hidden
        style={{ position: "absolute", left: -99999, top: 0, visibility: "hidden", pointerEvents: "none" }}
      >
        {rows.map((row) =>
          row.map((d) => (
            <div
              key={d.id}
              data-wb-id={d.id}
              className={d.datum.kind === "stage" ? `wb-node ${d.datum.state}` : undefined}
              style={{ width: d.w }}
            >
              <NodeBody datum={d.datum} ctx={ctxRef.current} />
            </div>
          )),
        )}
      </div>
    </>
  );
}
