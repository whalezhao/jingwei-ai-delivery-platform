import { Fragment, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Empty, Select, Tabs, Tag } from "antd";
import dayjs from "dayjs";
import { api } from "../api";
import { useGroup } from "../group";
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
}
interface Req {
  id: number;
  code: string;
  title: string;
  status: string;
  claimant_name: string | null;
  created_at: string | null;
  project_id: number;
  project_name: string;
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
const stamped = (label: string, s?: string | null) => (s ? `${label} ${fmtT(s)}` : null);

const REQ_STATUS: Record<string, [string, string]> = {
  pool: ["default", "池中"],
  claimed: ["processing", "方案讨论中"],
  confirmed: ["success", "已定稿"],
  archived: ["default", "已归档"],
  online: ["cyan", "已上线"],
};
const TASK_STATUS: Record<string, string> = {
  pending: "待派发",
  queued: "排队中",
  running: "执行中",
  pushed: "已推送",
  mr_created: "MR 待审",
  reviewing: "审核中",
  cicd: "CI/CD",
  done: "完成",
  failed: "失败",
};

/** 连线与圆点的动效档位：bad 红闪 / ok 绿实线 / live 蓝虚线流动 / idle 灰静态 */
function flowCls(t: Task): string {
  if (
    t.status === "failed" ||
    t.mr?.verdict === "failed" ||
    ["ci:failed", "ci:canceled"].includes(t.mr?.gate || "") ||
    t.mr?.flow_state === "conflict_notified"
  )
    return "bad";
  if (t.mr?.flow_state === "merged" || t.status === "done") return "ok";
  if (["queued", "running", "pushed", "reviewing", "cicd", "mr_created"].includes(t.status)) return "live";
  return "idle";
}

function ciChip(t: Task) {
  const m = t.mr;
  if (!m) return null;
  if (!m.gate) return { cls: "ok", text: "CI ✓" };
  if (["ci:failed", "ci:canceled"].includes(m.gate)) return { cls: "bad", text: `CI ✗ ${m.gate.slice(3)}` };
  return { cls: "wait", text: `CI ⏳ ${m.gate.slice(3)}` };
}
function mergeChip(t: Task) {
  const m = t.mr;
  if (!m) return null;
  if (m.flow_state === "merged") return { cls: "ok", text: `已合入 ${m.target_branch}` };
  if (m.flow_state === "conflict_notified") return { cls: "bad", text: "合并冲突" };
  if (m.flow_state === "hold_not_mergeable") return { cls: "wait", text: "等待可合并" };
  return null;
}
/** 任务卡种类标签：流水线上走到哪一段（终态三分类 + 中间阶段） */
function taskPhase(t: Task, r: Req): [string, string] {
  if (t.outcome === "merged") return ["success", "已合入"];
  if (t.outcome === "closed") return ["warning", "MR 关闭"];
  if (t.outcome === "failed" || t.status === "failed") return ["error", "失败"];
  if (t.mr_iid || t.status === "pushed") {
    if (!r.review_on) return ["processing", t.mr_iid ? "待人工合入" : "MR 提交中"];
    const m = t.mr;
    if (!m || m.verdict === "pending" || m.verdict === "modify") return ["processing", "代码评审"];
    if (m.verdict === "failed") return ["error", "评审未过"];
    if (m.gate) return ["processing", "CI"];
    return ["processing", r.merge_on ? "自动合并中" : "待人工合入"];
  }
  if (t.status === "pending") return ["default", "待抢单"];
  return ["processing", "开发中"];
}

type NodeKey = "doc" | "plan" | "dev" | "review" | "ci" | "merge" | "notify";
type St = "ok" | "live" | "idle" | "bad";

const NODE_LABEL: Record<NodeKey, string> = {
  doc: "📄 需求定稿",
  plan: "💬 方案",
  dev: "⚙️ Agent 开发",
  review: "🔍 代码评审",
  ci: "🧪 CI",
  merge: "🔀 自动合并",
  notify: "📣 通知项目负责人",
};

/** 项目配置决定轨道长度：评审/CI 看 audit_prefixes，自动合并看 auto_approve_prefixes；
 *  缺哪段就在 MR 建好后交给负责人（octo 通知），不再画空节点。 */
function nodeKeys(r: Req): NodeKey[] {
  const keys: NodeKey[] = ["doc", "plan", "dev"];
  if (r.review_on) keys.push("review", "ci");
  if (r.review_on && r.merge_on) keys.push("merge");
  else keys.push("notify");
  return keys;
}

function taskBad(t: Task): boolean {
  return (
    t.status === "failed" ||
    t.outcome === "failed" ||
    t.mr?.verdict === "failed" ||
    ["ci:failed", "ci:canceled"].includes(t.mr?.gate || "") ||
    t.mr?.flow_state === "conflict_notified"
  );
}

/** 任务当前所处阶段；终态任务停在收尾（合入/通知）或它失败的那一段 */
function taskStage(t: Task, r: Req): NodeKey {
  const last: NodeKey = r.review_on && r.merge_on ? "merge" : "notify";
  if (t.outcome === "merged" || t.outcome === "closed") return last;
  if (t.outcome === "failed" || t.status === "failed") {
    if (t.mr?.gate?.startsWith("ci:")) return "ci";
    if (!t.mr_iid) return "dev";
    return r.review_on ? "review" : "notify";
  }
  if (!t.mr_iid && t.status !== "pushed") return "dev";
  if (!r.review_on) return "notify"; // 项目没配 AI 评审：MR 建好就交给负责人
  const m = t.mr;
  if (!m || ["pending", "modify", "failed"].includes(m.verdict)) return "review";
  if (m.gate) return "ci";
  return r.merge_on ? "merge" : "notify";
}

/**
 * 需求轨道以「最落后的未终态任务」为当前节点：之前的都已完成、之后都未开始，
 * 避免 MR 已建但评审未过时多个节点同时亮。
 */
function reqNodes(r: Req): { key: NodeKey; label: string; state: St }[] {
  const keys = nodeKeys(r);
  const state: Record<string, St> = { doc: "ok", plan: r.plan_final ? "ok" : "live" };
  const open = r.tasks.filter((t) => !t.outcome && t.status !== "failed");
  if (r.plan_final && open.length) {
    const at = (t: Task) => keys.indexOf(taskStage(t, r));
    const cur = Math.max(keys.indexOf("dev"), Math.min(...open.map(at)));
    const bad = open.some((t) => at(t) === cur && taskBad(t));
    keys.forEach((k, i) => {
      state[k] = i < cur ? "ok" : i === cur ? (bad ? "bad" : "live") : "idle";
    });
  }
  return keys.map((k) => ({ key: k, label: NODE_LABEL[k], state: state[k] || "idle" }));
}

const VERDICT: Record<string, string> = { approve: "✅ 可批准", modify: "⚠️ 需修改", failed: "❌ 未通过" };
const STEP_TEXT: Record<string, string> = { ok: "已完成", live: "进行中", bad: "异常", idle: "待开始" };
/** 专家卡片的角色说明 */
const EXPERT_ROLE: Record<string, string> = { doc: "需求专家", plan: "方案专家", dev: "开发专家" };

/** 阶段卡上的核心信息：没挂任务卡时，这一格也要看出「做了什么、什么时候」 */
function nodeInfo(r: Req, key: NodeKey, state: St): { main: string; sub?: string | null; at?: string | null } {
  const t = r.tasks[0];
  const m = t?.mr || null;
  if (key === "doc")
    return { main: r.doc ? `v${r.doc.version} · ${r.doc.filename}` : STEP_TEXT[state], at: stamped("定稿", r.doc?.time) };
  if (key === "plan")
    return state === "ok"
      ? { main: r.plan ? `已定稿 · v${r.plan.version}` : "已定稿", at: stamped("定稿", r.plan?.time) }
      : { main: "讨论中" };
  if (key === "dev")
    return {
      main: r.tasks.length ? `${r.tasks.length} 个任务` : STEP_TEXT[state],
      sub: t?.runner_name ? `⚡ ${t.runner_name}` : null,
      at: stamped("开跑", t?.created_at),
    };
  if (key === "review") {
    if (!m) return { main: STEP_TEXT[state] };
    return {
      main: `${VERDICT[m.verdict] || "⏳ 初审中"}${m.issues ? ` · ${m.issues} 个问题` : ""}`,
      at: stamped("送审", m.created_at),
    };
  }
  if (key === "ci")
    return { main: (t && ciChip(t)?.text) || STEP_TEXT[state], at: stamped("更新", m?.updated_at) };
  if (key === "merge")
    return { main: (t && mergeChip(t)?.text) || STEP_TEXT[state], at: stamped("更新", m?.updated_at) };
  return {
    main: r.owners.length ? `octo @ ${r.owners.join("、")}` : "未配置负责人",
    at: stamped("建 MR", m?.created_at),
  };
}

/** 任务卡：并进流程里它当前所处的阶段节点，不再单独挂在下方；带该阶段的必要信息 */
function TaskCard({ t, r, onOpen }: { t: Task; r: Req; onOpen: () => void }) {
  const cls = flowCls(t);
  const ci = ciChip(t);
  const mg = mergeChip(t);
  const phase = taskPhase(t, r);
  const notified = taskStage(t, r) === "notify";
  return (
    <div className={`wb-card wb-task ${cls}`}>
      <div className="wb-task-head">
        <i className={`wb-dot ${cls}`} />
        <span className="wb-task-title" title={t.scope || t.title || ""}>
          {t.title || t.code}
        </span>
        <span
          className="wb-task-time"
          title={`创建 ${fullT(t.created_at)} · 更新 ${fullT(t.updated_at)}`}
        >
          🕒 {fmtT(t.updated_at) || "—"}
        </span>
        <Tag className="wb-phase" color={phase[0]}>{phase[1]}</Tag>
      </div>
      <div className="wb-chips">
        <a className="wb-chip mono" title={`查看任务 ${t.code}`} onClick={onOpen}>
          {t.code}
        </a>
        <span className="wb-chip">
          {t.runner_name ? `⚡ ${t.runner_name}` : t.status === "queued" ? "⏳ 待抢单" : "·"}
        </span>
        {t.agents && t.agents.sub > 0 && (
          <a
            className={`wb-chip${cls === "live" ? " live" : ""}`}
            onClick={onOpen}
            title={`${t.agents.events} 条过程事件 · 点击看各 agent 泳道`}
          >
            🤖 主 + {t.agents.sub} 子{cls === "live" ? " 协作中" : ""}
          </a>
        )}
        {t.mr_iid ? (
          <span className="wb-chip">
            MR{" "}
            {t.mr_url ? (
              <a href={t.mr_url} target="_blank" rel="noreferrer" className="mono">
                !{t.mr_iid}
              </a>
            ) : (
              <span className="mono">!{t.mr_iid}</span>
            )}{" "}
            {VERDICT[t.mr?.verdict || ""] || "⏳"}
          </span>
        ) : (
          <span className="wb-chip">{TASK_STATUS[t.status] || t.status}</span>
        )}
        {t.mr && t.mr.issues > 0 && <span className="wb-chip wait">{t.mr.issues} 个问题</span>}
        {ci && (t.mr?.pipeline_url && ci.cls !== "ok" ? (
          <a className={`wb-chip ${ci.cls}`} href={t.mr.pipeline_url} target="_blank" rel="noreferrer">
            {ci.text} ↗
          </a>
        ) : (
          <span className={`wb-chip ${ci.cls}`}>{ci.text}</span>
        ))}
        {mg && <span className={`wb-chip ${mg.cls}`}>{mg.text}</span>}
      </div>
      {t.mr?.summary && (
        <div className="wb-note" title={t.mr.summary}>
          {t.mr.summary}
        </div>
      )}
      {notified && (
        <div className="wb-note">
          📣 {t.mr_iid ? "已通知" : "待通知"}项目负责人
          {r.owners.length ? `：${r.owners.join("、")}` : "（未配置）"}
        </div>
      )}
      {t.error && (
        <div className="wb-error" title={t.error}>
          ⚠ {t.error}
        </div>
      )}
    </div>
  );
}

export default function Workbench() {
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
    if (projectId) p.set("project_id", String(projectId));
    const d = await api<{ requirements: Req[] }>(`/api/workbench?${p}`);
    setReqs(d.requirements);
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [groupId, projectId]);
  useEffect(() => {
    api<any[]>("/api/projects").then(setProjects);
  }, []);
  useEffect(() => {
    setProjectId(undefined); // 切组后旧项目筛选可能不属于该组
  }, [groupId]);

  // 专家卡片显示 agent-memory 里的专家名：按项目团队拉一次；拉不到就回落到 id
  useEffect(() => {
    const teams = [...new Set(projects.map((p) => p.am_team_id).filter(Boolean))] as string[];
    teams.forEach((t) => {
      api<any[]>(`/api/am/agents?team_id=${encodeURIComponent(t)}`)
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
      const project = projects.find((p) => p.id === r.project_id);
      return agentLabel(project?.am_agent_id, null);
    }
    return null;
  };

  const projOptions = projects
    .filter((p) => !groupId || p.group_id === groupId)
    .map((p) => ({ value: p.id, label: `${p.path}（${p.name}）` }));

  const liveTasks = active.reduce((n, r) => n + r.tasks.filter((t) => flowCls(t) === "live").length, 0);
  const reviewMrs = active.reduce(
    (n, r) => n + r.tasks.filter((t) => t.mr_iid && !t.outcome && ["mr_created", "reviewing", "cicd"].includes(t.status)).length,
    0
  );
  const stats: [string, number, string][] = [
    ["在读需求", active.length, ""],
    ["任务进行中", liveTasks, "live"],
    ["MR 待审/CI", reviewMrs, "wait"],
  ];

  return (
    <Page title="工作台" desc="需求 → 定稿 → 任务 → MR → CI → 合入 · 实时流程图，5 秒刷新 · 代码统计每日落库">
      <div className="wb-bar">
        <Select
          style={{ width: 280 }}
          allowClear
          placeholder="项目"
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
            label: "需求流程",
            children: (
              <>
        <div className="wb-bar">
        <div className="wb-legend">
          <span><i className="wb-dot live" /> 进行中</span>
          <span><i className="wb-dot ok" /> 已合入</span>
          <span><i className="wb-dot bad" /> 失败/冲突</span>
          <span><i className="wb-dot idle" /> 待处理</span>
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
          <i /> 实时
        </span>
        {active.length === 0 ? (
          <Empty
            description="暂无进行中的需求（或该筛选下没有需求）"
            style={{ padding: "48px 0" }}
          />
        ) : (
          <div className="wb-board">
            {active.map((r) => {
              const nodes = reqNodes(r);
              const keys = nodeKeys(r);
              const cardsByNode: Record<string, Task[]> = {};
              r.tasks.forEach((t) => {
                const k = taskStage(t, r);
                (cardsByNode[k] ||= []).push(t);
              });
              const lastCard = Math.max(-1, ...r.tasks.map((t) => keys.indexOf(taskStage(t, r))));
              return (
                <div className="wb-line" key={r.id}>
                  <div className="wb-chain">
                    {/* 需求信息直接做成流程起点的卡片，不额外加标题行 */}
                    <div className="wb-card wb-req-card" onClick={() => nav(`/reqs/${r.id}`)}>
                      <div className="wb-req-top">
                        <Tag color={(REQ_STATUS[r.status] || ["default", r.status])[0]}>
                          {(REQ_STATUS[r.status] || ["default", r.status])[1]}
                        </Tag>
                        <span className="mono wb-req-code">{r.code}</span>
                      </div>
                      <div className="wb-req-title" title={r.title}>
                        {r.title}
                      </div>
                      <div className="wb-req-meta">
                        {r.project_name} · {r.claimant_name || "未认领"} · {r.tasks.length} 任务
                        {r.created_at ? ` · 创建 ${fmtT(r.created_at)}` : ""}
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
                                  <span>{EXPERT_ROLE[n.key]}</span>
                                  <b title={expert}>{expert}</b>
                                </div>
                                <i className={`wb-vlink ${n.state}`} />
                              </>
                            )}
                            {cards.length ? (
                              <div className="wb-cards">
                                {cards.map((t) => (
                                  <TaskCard key={t.id} t={t} r={r} onOpen={() => nav(`/tasks/${t.id}`)} />
                                ))}
                              </div>
                            ) : (
                              <div className={`wb-step ${n.state}`}>
                                <b>{n.label}</b>
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
            label: "代码统计",
            children: <CodeStats groupId={groupId ?? undefined} projectId={projectId} />,
          },
        ]}
      />
    </Page>
  );
}
