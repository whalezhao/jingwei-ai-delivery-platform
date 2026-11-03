import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Page from "../Page";
import { Bubble, FileCard, Prompts, Sender } from "@ant-design/x";
import ThoughtChain from "@ant-design/x/es/thought-chain";
import type { ThoughtChainItemType } from "@ant-design/x/es/thought-chain";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Modal,
  Popconfirm,
  Popover,
  Row,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { ProCard } from "@ant-design/pro-components";
import {
  ApartmentOutlined,
  BulbOutlined,
  CheckCircleFilled,
  CheckOutlined,
  ClearOutlined,
  ClockCircleOutlined,
  CloseCircleFilled,
  CloseOutlined,
  CodeOutlined,
  EditOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FormOutlined,
  GlobalOutlined,
  InboxOutlined,
  LoadingOutlined,
  MessageOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SearchOutlined,
  StopOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons";
import Markdown from "react-markdown";
import { useTranslation } from "react-i18next";
import AmSelect from "../components/AmSelect";
import DocPreview from "../components/DocPreview";
import { rawNode } from "../eventNodes";
import { api, token } from "../api";
import i18n, { type MsgKey } from "../i18n";

interface Msg {
  id: number;
  role: string;
  content: string;
  /** 该轮用户选的团队/专家（仅 user 消息有；刷新后输入框按它恢复） */
  team_id?: string | null;
  agent_id?: string | null;
  /** 该轮 AI 的思考/工具过程（后端随消息回放，刷新后思考链不丢） */
  events?: any[];
}

/** 问答选择框（Claude ask 的前端呈现）：一个问题 + 若干可点选项，悬在输入框上方。 */
interface Ask {
  title: string;
  options: { label: string; desc?: string; run: () => void }[];
}

const STATUS: Record<string, [string, MsgKey]> = {
  pool: ["default", "requirements.status.pool"],
  claimed: ["processing", "requirements.status.claimed"],
  confirmed: ["success", "requirements.status.confirmed"],
  archived: ["default", "requirements.status.archived"],
  online: ["cyan", "requirements.status.online"],
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

/** 空对话时的起手示例（按当前讨论模式给，比一个光标干等着好）。 */
const examplesFor = (mode: "requirement" | "plan") =>
  mode === "requirement"
    ? [i18n.t("reqChat.exampleReq1"), i18n.t("reqChat.exampleReq2"), i18n.t("reqChat.exampleReq3")]
    : [i18n.t("reqChat.examplePlan1"), i18n.t("reqChat.examplePlan2"), i18n.t("reqChat.examplePlan3")];

/** 服务端每 15s 发一跳 keepalive。vite/nginx 等反代在上游断开时不会掐断
 *  下游连接，read() 会永远挂着 —— 超过这个时限没收到任何字节就判死重连。 */
const STALE_MS = 40_000;

/** 右侧 AI 栏宽度（像素），拖动分隔条调整；主区至少保留 MAIN_MIN。 */
// EGM 评审状态：incomplete 是「自报未完成」，不能画成绿色通过
const REVIEW_ST: Record<string, { color: string; text: MsgKey }> = {
  running: { color: "processing", text: "reqChat.reviewRunning" },
  done: { color: "success", text: "reqChat.reviewDone" },
  incomplete: { color: "warning", text: "reqChat.reviewIncomplete" },
  failed: { color: "error", text: "reqChat.reviewFailed" },
};

/** 评审批注：把 finding 的 Affected Scope 对到方案章节上。 */
interface Finding {
  title?: string;
  severity?: string;
  scope?: string;
  description?: string;
  recommendation?: string;
}

const SEV_ST: Record<string, { color: string; rank: number; icon: string }> = {
  Blocker: { color: "red", rank: 0, icon: "🔴" },
  Major: { color: "gold", rank: 1, icon: "🟡" },
  Nit: { color: "default", rank: 2, icon: "🟢" },
};

const sevOf = (f: Finding) => SEV_ST[f.severity || ""] || SEV_ST.Nit;
const normText = (s: string) => s.replace(/[`*#\s]/g, "").toLowerCase();

/** EGM 官方标识（egm.dev favicon）：象头 + 金鱼。 */
function EgmMark({ size = 14 }: { size?: number }) {
  return (
    <svg viewBox="1.5 1.5 21 21" width={size} height={size} aria-hidden="true" style={{ verticalAlign: "-2px" }}>
      <g fill="#1b6ef3" stroke="#1b6ef3" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
        <path
          stroke="none"
          d="M10.30,6.80 A5.20,5.20 0 0 0 10.30,17.20 A1.20,1.20 0 0 0 11.50,16.00 L11.50,8.00 A1.20,1.20 0 0 0 10.30,6.80Z"
        />
        <path fill="none" d="M7.20,12.00 C6.80,14.60 3.40,14.70 3.20,12.60 C3.10,11.20 3.30,10.20 3.60,9.40" />
      </g>
      <g fill="#e86e00" stroke="#e86e00" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
        <path
          stroke="none"
          d="M13.70,6.80 A5.20,5.20 0 0 1 13.70,17.20 A1.20,1.20 0 0 1 12.50,16.00 L12.50,8.00 A1.20,1.20 0 0 1 13.70,6.80Z"
        />
        <path
          fill="none"
          d="M16.80,12.00 L18.00,12.00 C18.80,12.00 19.40,10.60 20.90,9.40 M18.00,12.00 C18.80,12.00 19.40,13.40 20.90,14.60"
        />
      </g>
    </svg>
  );
}

/** react-markdown 的 children 里抠纯文本（标题匹配用）。 */
function textOf(node: any): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return node?.props?.children ? textOf(node.props.children) : "";
}

/** 方案里的标题和起始行（1-based）。 */
function planHeadings(content: string): { line: number; text: string }[] {
  return content.split("\n").flatMap((l, i) => {
    const m = l.match(/^#{1,6}\s+(.+?)\s*$/);
    return m ? [{ line: i + 1, text: m[1] }] : [];
  });
}

/** finding.scope → 章节标题：先按行号落到所在章节，再按标题文本互相包含匹配。 */
function resolveScope(content: string, scope?: string): string | null {
  const s = (scope || "").trim();
  if (!s) return null;
  const heads = planHeadings(content);
  const nums = s.match(/(\d+)\s*[-–—~至]\s*(\d+)/) || s.match(/(?:lines?|行)\s*(\d+)/i);
  if (nums) {
    const start = Number(nums[1]);
    let hit: string | null = null;
    for (const h of heads) {
      if (h.line <= start) hit = h.text;
      else break;
    }
    if (hit) return hit;
  }
  const ns = normText(s);
  let best: string | null = null;
  for (const h of heads) {
    const nh = normText(h.text);
    if (nh && (ns.includes(nh) || nh.includes(ns)) && (!best || h.text.length > best.length)) best = h.text;
  }
  return best;
}

/** 报告条目 → 章节分组 + 对不上章节的文档级条目。 */
function groupFindings(content: string, findings: Finding[]) {
  const byHeading = new Map<string, Finding[]>();
  const docLevel: Finding[] = [];
  for (const f of findings) {
    const h = resolveScope(content, f.scope);
    if (!h) docLevel.push(f);
    else byHeading.set(normText(h), [...(byHeading.get(normText(h)) || []), f]);
  }
  return { byHeading, docLevel };
}

function FindingCard({ f }: { f: Finding }) {
  const sev = sevOf(f);
  return (
    <div style={{ maxWidth: 420 }}>
      <Space size={4} wrap>
        <Tag color={sev.color}>{sev.icon} {f.severity || "Nit"}</Tag>
        {f.title && <strong>{f.title}</strong>}
      </Space>
      {f.description && <div style={{ marginTop: 6, fontSize: 12 }}>{f.description}</div>}
      {f.recommendation && (
        <div style={{ marginTop: 6, fontSize: 12, color: "#666" }}>
          {i18n.t("reqChat.advice")}
          {f.recommendation}
        </div>
      )}
      {f.scope && (
        <div style={{ marginTop: 6, fontSize: 11, opacity: 0.6 }}>
          {i18n.t("reqChat.scope")}
          {f.scope}
        </div>
      )}
    </div>
  );
}

/** 章节标题右边的批注角标：点开看该章节的全部问题。 */
function AnnoBadge({
  hits,
  open,
  onOpenChange,
}: {
  hits: Finding[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const worst = hits.reduce((a, b) => (sevOf(a).rank <= sevOf(b).rank ? a : b));
  const sev = sevOf(worst);
  return (
    <Popover
      trigger="click"
      open={open}
      onOpenChange={onOpenChange}
      title={i18n.t("reqChat.annoTitle", { n: hits.length })}
      content={
        <div>
          {hits.map((f, i) => (
            <div key={i} style={{ marginBottom: i === hits.length - 1 ? 0 : 12 }}>
              <FindingCard f={f} />
            </div>
          ))}
        </div>
      }
    >
      <Tag color={sev.color} style={{ marginInlineStart: 8, cursor: "pointer", verticalAlign: "middle" }}>
        {sev.icon}
        {hits.length > 1 ? ` ${hits.length}` : ""}
      </Tag>
    </Popover>
  );
}

/** 带批注的标题渲染器（react-markdown components）。 */
function headingWithAnno(
  Tag: any,
  byHeading: Map<string, Finding[]>,
  openKey: string | null,
  onOpenChange: (k: string | null) => void,
) {
  return function AnnotatedHeading({ children }: any) {
    const key = normText(textOf(children));
    const hits = byHeading.get(key);
    return (
      <Tag>
        {children}
        {hits?.length ? (
          <AnnoBadge
            hits={hits}
            open={openKey === key}
            onOpenChange={(v) => onOpenChange(v ? key : null)}
          />
        ) : null}
      </Tag>
    );
  };
}

const DOCK_MIN = 360;
const DOCK_DEFAULT = 460;
const MAIN_MIN = 480;
const SPLIT_W = 14;

/** SSE：fetch 流式读 /plan/stream（EventSource 带不了 Authorization 头）。 */
export async function openStream(
  reqId: number,
  signal: AbortSignal,
  on: (evt: any) => void,
  mode?: "requirement" | "plan",
): Promise<void> {
  const q = mode ? `?mode=${mode}` : "";
  const r = await fetch(`/api/requirements/${reqId}/plan/stream${q}`, {
    headers: { Authorization: `Bearer ${token.get()}` },
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`stream ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    while (!signal.aborted) {
      const readP = reader.read();
      readP.catch(() => {}); // 输给看门狗后被 cancel 的 read，别变成 unhandled rejection
      let timer: number | undefined;
      let res: ReadableStreamReadResult<Uint8Array>;
      try {
        res = await Promise.race([
          readP,
          new Promise<never>((_, rej) => {
            timer = window.setTimeout(() => rej(new Error("stale")), STALE_MS);
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
      const { done, value } = res;
      if (done) return;
      buf += dec.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        for (const line of block.split("\n")) {
          if (line.startsWith("data: ")) {
            try {
              on(JSON.parse(line.slice(6)));
            } catch {
              /* 跳过坏帧 */
            }
          }
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

/** 预览目标：需求文档版本 或 方案版本 */
type Preview = { type: "doc" | "plan"; data: any } | null;

// ---------- 思考链节点（需求对话 / 任务详情共用，对齐官方 ThoughtChain demo 的分类型展示） ----------

/** 工具 → 类型图标 + 人话标签 + 关键入参字段（动作行展示用） */
const TOOL_META: Record<string, { icon: React.ReactNode; label: MsgKey; arg?: string }> = {
  Bash: { icon: <CodeOutlined />, label: "reqChat.tool.bash", arg: "command" },
  Write: { icon: <FormOutlined />, label: "reqChat.tool.write", arg: "file_path" },
  Edit: { icon: <EditOutlined />, label: "reqChat.tool.edit", arg: "file_path" },
  Read: { icon: <FileTextOutlined />, label: "reqChat.tool.read", arg: "file_path" },
  Grep: { icon: <SearchOutlined />, label: "reqChat.tool.grep", arg: "pattern" },
  Glob: { icon: <FileSearchOutlined />, label: "reqChat.tool.glob", arg: "pattern" },
  Task: { icon: <RobotOutlined />, label: "reqChat.tool.task", arg: "description" },
  WebFetch: { icon: <GlobalOutlined />, label: "reqChat.tool.webFetch", arg: "url" },
  WebSearch: { icon: <GlobalOutlined />, label: "reqChat.tool.webSearch", arg: "query" },
  AskUserQuestion: { icon: <QuestionCircleOutlined />, label: "reqChat.tool.ask", arg: "question" },
  ExitPlanMode: { icon: <CheckOutlined />, label: "reqChat.tool.exitPlan", arg: "plan" },
};

export function toolMeta(name?: string) {
  const m = TOOL_META[name || ""];
  return m
    ? { icon: m.icon, label: i18n.t(m.label), arg: m.arg }
    : { icon: <ToolOutlined />, label: name || i18n.t("reqChat.tool.fallback"), arg: undefined };
}

/** 从工具入参 JSON 里挑人话字段（命令/文件路径/描述…），截断 JSON 退回首行 */
export function toolArg(name: string | undefined, input: string): string {
  try {
    const obj = JSON.parse(input || "{}");
    // AskUserQuestion 的 questions 是「JSON 字符串套数组」，取首个问题的正文
    if (name === "AskUserQuestion") {
      const q = typeof obj.questions === "string" ? JSON.parse(obj.questions) : obj.questions;
      const first = Array.isArray(q) ? q[0]?.question : "";
      if (typeof first === "string" && first) return first;
    }
    const arg = TOOL_META[name || ""]?.arg;
    if (arg) {
      const v = obj[arg];
      if (typeof v === "string" && v) return v;
    }
  } catch {
    /* 截断过的坏 JSON */
  }
  return (input || "").split("\n")[0].slice(0, 80);
}

/** AskUserQuestion 的入参 → 可点选项（CLI 用 label/description，文本提问块用 label/desc）。 */
function toolAsk(input: string): AskQuestion | null {
  try {
    const obj = JSON.parse(input || "{}");
    const q = typeof obj.questions === "string" ? JSON.parse(obj.questions) : obj.questions;
    const first = Array.isArray(q) ? q[0] : null;
    const options = (Array.isArray(first?.options) ? first.options : [])
      .filter((o: any) => o && typeof o.label === "string")
      .map((o: any) => ({
        label: o.label,
        desc: typeof o.description === "string" ? o.description : undefined,
      }));
    if (typeof first?.question === "string" && options.length) {
      return { question: first.question, options };
    }
  } catch {
    /* 流式半截 JSON */
  }
  return null;
}

/** 某个泳道里还挂着没结果的 AskUserQuestion（被拒的也算有了结局）。 */
function pendingAsk(evts: any[], tid: string): AskQuestion | null {
  const done = new Set(evts.filter((e) => e.kind === "tool_result").map((e) => e.tool_use_id));
  const denied = new Set(
    evts.filter((e) => e.subtype === "permission_denied").map((e) => e.tool_use_id || ""),
  );
  const lane = evts.filter((e) => (e.parent || "") === tid);
  for (let i = lane.length - 1; i >= 0; i--) {
    const e = lane[i];
    if (e.kind !== "tool_use" || e.name !== "AskUserQuestion") continue;
    if (done.has(e.id) || denied.has(e.id)) continue;
    return toolAsk(e.input || "");
  }
  return null;
}

/** 官方 demo 同款动作行：灰底圆角 + 类型图标 + 等宽参数 */
export function actionRow(icon: React.ReactNode, text: string, tone?: "ok" | "err", label?: string) {
  return (
    <div className={`tc-action${tone ? ` ${tone}` : ""}`}>
      <span className="tc-action-ico">{icon}</span>
      {label && <span className="tc-action-label">{label}</span>}
      <code>{text}</code>
    </div>
  );
}

/** 思考正文块：左侧细竖线（官方 demo「深度思考」样式） */
export function thinkBlock(text: string) {
  return <div className="tc-think-block">{text}</div>;
}

/** 思考链展开态：活跃末节点自动展开，用户点过的节点以手动选择为准。
 *  state（manual）必须挂在组件顶部无条件调用；expandedKeys 渲染期纯计算，
 *  避免在渲染路径里 setState（ThoughtChain 受控模式会告警）。 */
export function chainExpandedKeys(manual: Record<string, boolean>, lastKey?: string): string[] {
  const keys = Object.entries(manual)
    .filter(([, v]) => v)
    .map(([k]) => k);
  if (lastKey && manual[lastKey] === undefined) keys.push(lastKey);
  return keys;
}

export function chainExpandSetter(
  setManual: React.Dispatch<React.SetStateAction<Record<string, boolean>>>,
  prevKeys: string[],
) {
  return (keys: string[]) =>
    // ThoughtChain 在自家 state updater 里回调 onExpand，同步写父组件 state 会触发
    // 「渲染过程中更新其他组件」告警；丢到微任务里等本次渲染结束再写
    queueMicrotask(() =>
      setManual((m) => {
        const next = { ...m };
        keys.filter((k) => !prevKeys.includes(k)).forEach((k) => (next[k] = true));
        prevKeys.filter((k) => !keys.includes(k)).forEach((k) => (next[k] = false));
        return next;
      }),
    );
}

export type SubItem = {
  key: string;
  desc: string;
  subagent_type: string;
  tool_uses: number;
  total_tokens: number;
};

/** 单条 task_progress 事件 → 子代理节点 */
export function subItem(e: any): SubItem {
  return {
    key: e.tool_use_id || String(Date.now()),
    desc: e.description || "",
    subagent_type: e.subagent_type || "",
    tool_uses: e.usage?.tool_uses || 0,
    total_tokens: e.usage?.total_tokens || 0,
  };
}

/** 事件流里的 task_progress → 子代理节点（同一 tool_use 后到覆盖先到） */
export function deriveSubs(evts: any[]): SubItem[] {
  const map = new Map<string, SubItem>();
  for (const e of evts) {
    if (e.subtype !== "task_progress") continue;
    const item = {
      key: e.tool_use_id || String(map.size),
      desc: e.description || "",
      subagent_type: e.subagent_type || "",
      tool_uses: e.usage?.tool_uses || 0,
      total_tokens: e.usage?.total_tokens || 0,
    };
    map.set(item.key, item);
  }
  return [...map.values()];
}

type ChainNode = ThoughtChainItemType & {
  kind: "think" | "tool" | "sub" | "raw" | "text";
  toolName?: string;
  arg?: string;
  text?: string;
  /** 子代理节点的派发 id（= Agent 工具的 tool_use id），用来挂嵌套链和进度 */
  tid?: string;
  /** 工具调用的 tool_use id：并行调用时结果靠它对回原节点 */
  toolId?: string;
};

/** Agent / Task 工具调用：标题取子代理类型，描述取本次派发说明 */
function agentMeta(name: string, input: string) {
  if (name !== "Agent" && name !== "Task") return null;
  let obj: any = {};
  try {
    obj = JSON.parse(input || "{}");
  } catch {
    /* 流式半截 JSON */
  }
  return { type: obj.subagent_type || "", desc: obj.description || "" };
}

export type AgentState = "work" | "wait" | "block" | "done" | "error";

export type AgentRow = {
  key: string;
  /** Agent 派发 id（主会话为空串），用来取它的嵌套会话 */
  tid: string;
  name: string;
  state: AgentState;
  /** 当前动作 / 卡在哪 */
  action: string;
  /** 有没有可展开的会话记录（旧数据或流被截断时没有） */
  canOpen: boolean;
};

const AGENT_ST: Record<AgentState, { text: MsgKey; short: MsgKey }> = {
  work: { text: "reqChat.agentState.work", short: "reqChat.agentState.workShort" },
  wait: { text: "reqChat.agentState.wait", short: "reqChat.agentState.waitShort" },
  block: { text: "reqChat.agentState.block", short: "reqChat.agentState.blockShort" },
  done: { text: "reqChat.agentState.done", short: "reqChat.agentState.doneShort" },
  error: { text: "reqChat.agentState.error", short: "reqChat.agentState.errorShort" },
};

const AGENT_ICON: Record<AgentState, React.ReactNode> = {
  work: <LoadingOutlined spin />,
  wait: <ClockCircleOutlined />,
  block: <StopOutlined />,
  done: <CheckCircleFilled />,
  error: <CloseCircleFilled />,
};

/** 子代理类型展示名：去掉插件命名空间（egm:egm-zoom-out → egm-zoom-out） */
function shortAgentName(name: string) {
  return name.includes(":") ? name.split(":").pop() || name : name;
}

/** 把实时事件流归纳成主/子代理状态行：谁在工作、谁在等人、谁被权限拦住。
 *  Agent 工具的 tool_use 是子代理的锚点，任务生命周期与 task_progress 都归到它名下；
 *  permission_denied 只带被拒工具的 id，靠 tool_use 的 parent 反查归属。 */
export function agentBoard(evts: any[], live: boolean): AgentRow[] {
  const mainLane = evts.filter((e) => !e.parent);
  const rows = new Map<string, AgentRow>();
  const order: string[] = [];
  const ensure = (tid: string, name: string, desc = "") => {
    let r = rows.get(tid);
    if (!r) {
      r = { key: tid, tid, name: name || i18n.t("reqChat.subAgent"), state: "work", action: desc, canOpen: false };
      rows.set(tid, r);
      order.push(tid);
    }
    return r;
  };

  // 派发点：Agent/Task 工具调用；task_started 兜底（流被截断时工具事件可能没到）
  for (const e of mainLane) {
    if (e.kind !== "tool_use") continue;
    const ag = agentMeta(e.name, e.input || "");
    if (ag) ensure(e.id, ag.type, ag.desc);
  }
  const taskToTid = new Map<string, string>();
  for (const e of evts) {
    if (e.subtype !== "task_started" || !e.task_id) continue;
    if (e.tool_use_id) taskToTid.set(e.task_id, e.tool_use_id);
    const r = ensure(e.tool_use_id || e.task_id, e.subagent_type || "", e.description || "");
    if (e.subagent_type) r.name = e.subagent_type;
  }
  for (const s of deriveSubs(evts)) {
    const r = rows.get(s.key) || ensure(s.key, s.subagent_type, s.desc);
    if (s.subagent_type) r.name = s.subagent_type;
    if (s.desc) r.action = s.desc;
  }
  rows.forEach((r) => {
    r.name = shortAgentName(r.name);
    r.canOpen = evts.some((e) => (e.parent || "") === r.tid);
  });

  // 终态：Agent 工具结果 / 任务生命周期（task_updated 无 tool_use_id，用 task_id 映射回派发 id）
  for (const e of mainLane) {
    if (e.kind === "tool_result" && e.tool_use_id) {
      const r = rows.get(e.tool_use_id);
      if (r) r.state = e.is_error ? "error" : "done";
    }
  }
  for (const e of evts) {
    if (e.subtype !== "task_updated" && e.subtype !== "task_notification") continue;
    const r = rows.get(e.tool_use_id || taskToTid.get(e.task_id) || "");
    if (!r) continue;
    const st = e.subtype === "task_updated" ? e.patch?.status : e.status;
    if (st === "completed") r.state = "done";
    else if (st === "failed" || st === "error" || st === "killed") {
      r.state = "error";
      // 失败通知带原因（API 断网、提前终止…），比停留在最后一个动作有用
      if (typeof e.summary === "string" && e.summary) r.action = e.summary.slice(0, 120);
    }
  }

  // 权限拦截：raw 事件只带被拒工具的 id，用 tool_use 事件反查它属于主会话还是哪个子代理
  const owner = new Map<string, string>();
  for (const e of evts) if (e.kind === "tool_use") owner.set(e.id, e.parent || "");
  const denied = new Map<string, string>();
  const deniedIds = new Set<string>();
  // 被拒后这条泳道只要再有事件，就说明模型继续跑了（被拒的工具常常不回 tool_result）
  const deniedAt = new Map<string, number>();
  const laneLast = new Map<string, number>();
  evts.forEach((e, i) => {
    laneLast.set(e.parent || "", i);
    if (e.subtype !== "permission_denied") return;
    deniedIds.add(e.tool_use_id || "");
    const own = owner.get(e.tool_use_id) || "";
    denied.set(own, e.tool_name || i18n.t("reqChat.tool.fallback"));
    deniedAt.set(own, i);
  });
  const stillBlocked = (own: string) =>
    deniedAt.has(own) && (laneLast.get(own) ?? -1) <= deniedAt.get(own)!;

  // 各泳道里还没回结果的工具：「向你确认 / 提交方案」意味着这一层在等人
  const pending = (lane: any[]) => {
    const done = new Set(lane.filter((e) => e.kind === "tool_result").map((e) => e.tool_use_id));
    // 被拒也是终态：CLI 不一定会为它回 tool_result
    return lane.filter(
      (e) => e.kind === "tool_use" && !done.has(e.id) && !deniedIds.has(e.id),
    );
  };
  rows.forEach((r) => {
    if (r.state === "done" || r.state === "error") return;
    if (!live) {
      // 本轮已结束：没等到终态的（流被截断/旧记录）不再冒充还在跑
      r.state = "done";
      r.action = i18n.t("reqChat.actionEnded");
      return;
    }
    const lane = evts.filter((e) => (e.parent || "") === r.tid);
    const lanePend = pending(lane);
    const ask = lanePend.find((e) => e.name === "AskUserQuestion" || e.name === "ExitPlanMode");
    if (deniedIds.has(r.tid) || stillBlocked(r.tid)) {
      r.state = "block";
      r.action = i18n.t("reqChat.notAuthorized", {
        tool: denied.get(r.tid) || i18n.t("reqChat.tool.fallback"),
      });
    } else if (ask) {
      r.state = "wait";
      r.action = toolArg(ask.name, ask.input || "") || i18n.t("reqChat.waitingConfirm");
    } else {
      r.state = "work";
    }
  });

  // 主会话：等人 > 被权限拦 > 等子代理 / 执行当前工具 > 思考；本轮结束即完成
  const mainPend = pending(mainLane).filter((e) => !agentMeta(e.name, e.input || ""));
  const mainAsk = mainPend.find((e) => e.name === "AskUserQuestion" || e.name === "ExitPlanMode");
  // 主会话等的是一切没落终态的子代理：阻塞/等确认的也算它还在跑
  const running = order.filter((k) => !["done", "error"].includes(rows.get(k)!.state));
  const main: AgentRow = {
    key: "__main__",
    tid: "",
    name: i18n.t("reqChat.mainAgent"),
    state: live ? "work" : "done",
    action: live ? i18n.t("reqChat.thinking") : i18n.t("reqChat.turnEnded"),
    canOpen: true,
  };
  // 主会话被拒且之后没有任何新事件，才算它还卡在授权上
  const mainDenied = live && stillBlocked("");
  if (mainAsk) {
    main.state = "wait";
    main.action = i18n.t("reqChat.waitingConfirmArg", {
      arg: toolArg(mainAsk.name, mainAsk.input || ""),
    });
  } else if (mainDenied) {
    main.state = "block";
    main.action = i18n.t("reqChat.notAuthorized", {
      tool: denied.get("") || i18n.t("reqChat.tool.fallback"),
    });
  } else if (live && running.length) {
    main.action = i18n.t("reqChat.waitingSubs", { n: running.length });
  } else if (live && mainPend.length) {
    const p = mainPend[mainPend.length - 1];
    main.action = `${toolMeta(p.name).label} · ${toolArg(p.name, p.input || "")}`;
  }
  return [main, ...order.map((k) => rows.get(k)!)];
}

/** 子代理会话：嵌在派生它的 Agent 节点里（Claude 式），展开才看它的思考与工具调用，
 *  子代理的正文/工具行不再混进主会话的链。 */
function SubChain({
  evts,
  parentId,
  prefix,
  live,
  follow,
}: {
  evts: any[];
  parentId: string;
  prefix: string;
  live?: boolean;
  /** 状态板里看单个代理时自动展开最新一步；嵌在主链里的子会话保持全折叠 */
  follow?: boolean;
}) {
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const items = buildChain(evts, { prefix, rootParent: parentId, showText: true, live });
  if (!items.length) return null;
  const lastKey = items[items.length - 1]?.key;
  const expandedKeys = chainExpandedKeys(manual, follow && live ? lastKey : undefined);
  return (
    <div className="rc-subchain">
      <ThoughtChain
        items={items}
        expandedKeys={expandedKeys}
        onExpand={chainExpandSetter(setManual, expandedKeys)}
      />
    </div>
  );
}

/** 状态板里选中的代理记录：内容长了跟着最新事件滚，别让人手动追 */
function BoardChain({ evts, parentId, live }: { evts: any[]; parentId: string; live: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el && live) el.scrollTop = el.scrollHeight;
  }, [evts.length, live]);
  return (
    <div className="rc-ag-view" ref={ref}>
      <SubChain
        evts={evts}
        parentId={parentId}
        prefix={`board-${parentId || "main"}`}
        live={live}
        follow
      />
    </div>
  );
}

/** 一轮内的主/子代理总览：状态一行一个，点开就地看该代理的实时记录。 */
export function AgentBoard({
  rows,
  evts,
  live,
  onAnswer,
}: {
  rows: AgentRow[];
  evts: any[];
  live: boolean;
  /** 点选项 = 把答案作为下一轮发言（与文本提问块同一路径） */
  onAnswer?: (text: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const [sel, setSel] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // 选中后列表变矮，保证被点的那一行仍在可视区
  useEffect(() => {
    const list = listRef.current;
    const el = sel ? list?.querySelector<HTMLElement>(`[data-ag="${sel}"]`) : null;
    if (!list || !el) return;
    if (el.offsetTop < list.scrollTop) list.scrollTop = el.offsetTop;
    else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight)
      list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight;
  }, [sel]);
  const subs = rows.filter((r) => r.tid);
  const cur = rows.find((r) => r.key === sel);
  const cnt = (s: AgentState) => subs.filter((r) => r.state === s).length;
  const summary = [
    subs.length ? t("reqChat.subsCount", { n: subs.length }) : t("reqChat.mainSession"),
    ...(["work", "wait", "block", "error", "done"] as AgentState[])
      .map((s) => (cnt(s) ? `${cnt(s)} ${t(AGENT_ST[s].short)}` : ""))
      .filter(Boolean),
  ].join(" · ");
  return (
    <div className="rc-agents">
      <button type="button" className="rc-agents-head" onClick={() => setOpen((v) => !v)}>
        <b>{t("reqChat.boardTitle")}</b>
        <span className="rc-agents-sum">{summary}</span>
        <span className="rc-agents-fold">{open ? t("reqChat.fold") : t("reqChat.unfold")}</span>
      </button>
      {open && (
        <div className={`rc-agents-list${cur ? " compact" : ""}`} ref={listRef}>
          {rows.map((r) => {
            const on = sel === r.key;
            // 等人拍板的代理直接把选项铺出来，别让人再去找哪里能点
            const ask = r.state === "wait" ? pendingAsk(evts, r.tid) : null;
            return (
              <div key={r.key} data-ag={r.key}>
                <button
                  type="button"
                  className={`rc-ag${on ? " on" : ""}`}
                  title={`${r.name} · ${t(AGENT_ST[r.state].text)}${r.action ? ` · ${r.action}` : ""}`}
                  aria-pressed={on}
                  onClick={() => r.canOpen && setSel(on ? null : r.key)}
                >
                  <span className={`rc-ag-state ${r.state}`}>{AGENT_ICON[r.state]}</span>
                  <span className="rc-ag-name">{r.name}</span>
                  <span className={`rc-ag-tag ${r.state}`}>{t(AGENT_ST[r.state].text)}</span>
                  <span className="rc-ag-act">{r.action || "—"}</span>
                  {r.canOpen && (
                    <span className="rc-ag-more">{on ? t("reqChat.fold") : t("reqChat.view")}</span>
                  )}
                </button>
                {ask && (
                  <div className="rc-ag-ask">
                    {ask.options.map((o, j) => (
                      <button
                        key={j}
                        type="button"
                        className="rc-ask-opt"
                        disabled={!onAnswer}
                        title={onAnswer ? undefined : t("reqChat.answerAfterTurn")}
                        onClick={() => onAnswer?.(o.label)}
                      >
                        <span className="rc-ask-num">{j + 1}</span>
                        <span className="rc-ask-label">{o.label}</span>
                        {o.desc && <span className="rc-ask-desc">{o.desc}</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {open && cur?.canOpen && (
        <div className="rc-ag-view-wrap">
          <div className="rc-ag-view-head">
            <span>
              {cur.name} · {t(AGENT_ST[cur.state].text)} · {t("reqChat.liveRecord")}
            </span>
            <button type="button" onClick={() => setSel(null)}>
              {t("reqChat.fold")}
            </button>
          </div>
          <BoardChain evts={evts} parentId={cur.tid} live={live} />
        </div>
      )}
    </div>
  );
}

/** 一轮过程事件 → 思考链节点：每次思考可折叠连成链、工具按类型出动作行、
 *  子代理（Agent/Task）收成一个节点，它自己的事件作为嵌套链挂在下面。
 *  prefix 区分不同回合，避免多轮节点 key 撞车串展开态；rootParent 让同一函数
 *  复用于任意一层子会话（parent 为空 = 主会话）。 */
export function buildChain(
  evts: any[],
  opts: {
    prefix: string;
    live?: boolean;
    thinkTokens?: number;
    subs?: SubItem[];
    rootParent?: string;
    showText?: boolean;
  },
): ChainNode[] {
  const { prefix, live, thinkTokens, subs, showText } = opts;
  const rootParent = opts.rootParent ?? "";
  // 先按 parent 分流：本层只画直接事件，派生出的事件留给 Agent 节点嵌套
  const items: ChainNode[] = [];
  const lane = evts.filter((e) => (e.parent || "") === rootParent);
  lane.forEach((e, i) => {
    if (e.kind === "thinking") {
      items.push({
        kind: "think",
        key: `${prefix}-think-${e.id || i}`,
        icon: <BulbOutlined />,
        title: i18n.t("taskDetail.thinking"),
        collapsible: true,
        content: thinkBlock(e.text || ""),
      });
    } else if (e.kind === "tool_use") {
      const ag = agentMeta(e.name, e.input || "");
      if (ag) {
        // 子代理：主链上只留一个节点，它自己的会话嵌在下面（Claude 的 Task 行）
        items.push({
          kind: "sub",
          key: `${prefix}-agent-${e.id || i}`,
          icon: <RobotOutlined />,
          title: ag.type || i18n.t("reqChat.subAgent"),
          description: ag.desc,
          status: "loading",
          tid: e.id,
          toolId: e.id,
        });
        return;
      }
      const m = toolMeta(e.name);
      const arg = toolArg(e.name, e.input || "");
      items.push({
        kind: "tool",
        key: `${prefix}-${e.id || i}`,
        title: m.label,
        status: "loading",
        collapsible: true,
        toolName: e.name,
        toolId: e.id,
        arg,
        content: actionRow(m.icon, arg || "…"),
      });
    } else if (e.kind === "tool_result") {
      // 并行工具/子代理的结果可能乱序回来：优先按 tool_use_id 精确配对，
      // 旧数据没有 id 才退回流序里最近的待定节点
      const pending = (n: ChainNode) =>
        (n.kind === "tool" || n.kind === "sub") && n.status === "loading";
      const it = e.tool_use_id
        ? items.find((n) => n.toolId === e.tool_use_id && pending(n))
        : [...items].reverse().find(pending);
      if (!it) return;
      if (it.kind === "sub") {
        // 子代理结束：嵌套链里已有它的全部过程，这里只翻状态
        it.status = e.is_error ? "error" : "success";
        return;
      }
      const m = toolMeta(it.toolName);
      const line = (e.content || "").split("\n")[0].slice(0, 100);
      it.status = e.is_error ? "error" : "success";
      it.content = (
        <>
          {actionRow(m.icon, it.arg || "…")}
          {actionRow(
            e.is_error ? <CloseOutlined /> : <CheckOutlined />,
            line || i18n.t("events.noOutput"),
            e.is_error ? "err" : "ok",
            e.is_error ? i18n.t("taskDetail.execError") : i18n.t("taskDetail.done"),
          )}
          {e.is_error && <pre className="tc-body tc-err">{e.content}</pre>}
        </>
      );
    } else if (e.kind === "text") {
      // 只有子代理会话画正文（主会话正文走消息气泡）；连续片段并成一段
      if (!showText || !(e.text || "").trim()) return;
      const prev = items[items.length - 1];
      if (prev?.kind === "text") {
        prev.text = (prev.text || "") + e.text;
        prev.content = <div className="tc-sub-text">{prev.text}</div>;
      } else {
        items.push({
          kind: "text",
          key: `${prefix}-text-${e.id || i}`,
          // 有图标才不会退化成思维链的序号，看起来像正文
          icon: <MessageOutlined />,
          text: e.text,
          content: <div className="tc-sub-text">{e.text}</div>,
        });
      }
    } else if (e.kind === "raw") {
      // task_progress 已由下面的子代理节点承接，再画一遍就重复了
      if (e.subtype === "task_progress") return;
      // 其余 system 事件（子任务/压缩/重试/拒答…）统一由 eventNodes 出节点，认不出才折叠 JSON
      const node = rawNode(e, `${prefix}-raw-${e.uuid || i}`);
      if (node) items.push(node);
    }
  });
  // 子代理节点：挂嵌套会话 + 用最新 task_progress 补标题/当前动作/用量
  const metrics = new Map(deriveSubs(evts).map((s) => [s.key, s]));
  items.forEach((n) => {
    if (n.kind !== "sub" || !n.tid) return;
    const s = metrics.get(n.tid);
    if (s) {
      if (s.subagent_type) n.title = s.subagent_type;
      if (s.desc) n.description = s.desc;
      n.footer = i18n.t("reqChat.toolUsage", {
        n: s.tool_uses,
        tokens: (s.total_tokens / 1000).toFixed(1),
      });
    }
    // 没有子事件（旧数据 / 回放只存了主会话）就不给可展开的空壳
    if (evts.some((e) => (e.parent || "") === n.tid)) {
      n.collapsible = true;
      n.content = (
        <SubChain
          evts={evts}
          parentId={n.tid}
          prefix={`${prefix}-${n.tid}`}
          live={!!live && n.status === "loading"}
        />
      );
    }
  });
  // 没有对应的 Agent 事件（旧数据 / 事件被截断）才退回末尾的进展芯片
  if (rootParent === "") {
    const shown = new Set(items.filter((n) => n.tid).map((n) => n.tid));
    (subs ?? deriveSubs(evts)).forEach((s) => {
      if (shown.has(s.key)) return;
      items.push({
        kind: "sub",
        key: `${prefix}-sub-${s.key}`,
        icon: <RobotOutlined />,
        title: s.subagent_type || i18n.t("reqChat.subAgent"),
        description: s.desc,
        footer: i18n.t("reqChat.toolUsage", {
          n: s.tool_uses,
          tokens: (s.total_tokens / 1000).toFixed(1),
        }),
        status: live ? "loading" : undefined,
      });
    });
  }
  const last = items.length - 1;
  items.forEach((n, i) => {
    const isLast = i === last;
    n.blink = false;
    if (n.kind === "think") {
      // 思考中 = 转圈（status 压过灯泡图标）+ token 计数；结束回落灯泡
      if (live && isLast) {
        n.status = "loading";
        n.description =
          thinkTokens == null
            ? i18n.t("reqChat.thinking")
            : i18n.t("reqChat.thinkingTokens", { n: thinkTokens });
        n.blink = true;
      } else {
        n.status = undefined;
        n.description = undefined;
      }
    } else if (n.status === "loading" && !live) {
      // 本轮结束仍未回结果：子代理回落图标，工具标中止
      n.status = n.kind === "sub" ? undefined : "abort";
    }
    if (live && isLast && n.status === "loading") n.blink = true;
  });
  return items;
}

/** 方案块标记：AI 按提示词把真正的方案包在 HTML 注释里（Markdown 渲染时不可见）。 */
interface AskQuestion {
  question: string;
  options: { label: string; desc?: string }[];
}
type Block = { kind: "text" | "plan" | "ask" | "doc"; body: string; open?: boolean; ask?: AskQuestion };

/** 提问块正文是 JSON（模型偶尔包一层 ```json 围栏，去掉再解析）。 */
function parseAskQuestion(body: string): AskQuestion | null {
  const raw = body.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/, "");
  try {
    const q = JSON.parse(raw);
    const options = (Array.isArray(q?.options) ? q.options : [])
      .filter((o: any) => o && typeof o.label === "string")
      .map((o: any) => ({ label: o.label, desc: typeof o.desc === "string" ? o.desc : undefined }));
    if (typeof q?.question === "string" && options.length) return { question: q.question, options };
  } catch {
    // 流式半截 JSON 解析失败是正常现象，交给调用方决定展示
  }
  return null;
}

/** 按标记切分 AI 回复：方案 / 需求文档 / 提问块渲染成卡片，其余是普通对话。
 *  流式未闭合时尾巴算未完成块（按钮禁用）；模型偶尔改用 ```plan / ```doc 围栏，没有注释标记时兼容识别。 */
export function splitPlanBlocks(text: string): Block[] {
  let src = text;
  if (!/vega:(plan|doc):begin/i.test(src)) {
    src = src
      .replace(
        /```plan[^\n]*\n([\s\S]*?)```/gi,
        (_, body) => `<!-- vega:plan:begin -->\n${body}<!-- vega:plan:end -->`,
      )
      .replace(
        /```doc[^\n]*\n([\s\S]*?)```/gi,
        (_, body) => `<!-- vega:doc:begin -->\n${body}<!-- vega:doc:end -->`,
      );
  }
  const out: Block[] = [];
  const re = /<!--\s*vega:(plan|ask|doc):begin\s*-->([\s\S]*?)(?:<!--\s*vega:\1:end\s*-->|$)/gi;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m.index > last) out.push({ kind: "text", body: src.slice(last, m.index) });
    const kind = m[1].toLowerCase();
    const open = !new RegExp(`<!--\\s*vega:${kind}:end\\s*-->\\s*$`, "i").test(m[0]);
    if (kind === "ask") {
      const ask = parseAskQuestion(m[2]);
      // 解析失败且已闭合：按原文展示，别把内容吞掉
      out.push(ask || open ? { kind: "ask", body: m[2], open, ask: ask ?? undefined } : { kind: "text", body: m[2] });
    } else {
      out.push({ kind: kind === "doc" ? "doc" : "plan", body: m[2], open });
    }
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push({ kind: "text", body: src.slice(last) });
  return out;
}

/** AI 回复正文：方案 / 需求文档块渲染成卡片（带「采用」按钮），提问块渲染成可选项卡片。 */
function AssistantBody({
  text,
  onAdopt,
  onAdoptDoc,
  onPick,
}: {
  text: string;
  onAdopt?: (md: string) => Promise<void>;
  onAdoptDoc?: (md: string) => Promise<void>;
  onPick?: (label: string) => void;
}) {
  const { t } = useTranslation();
  const [adopting, setAdopting] = useState(false);
  return (
    <div className="md-body">
      {splitPlanBlocks(text).map((b, i) => {
        if (b.kind === "ask") {
          return (
            <div className="rc-ask" key={i}>
              <div className="rc-ask-title">{b.ask?.question ?? t("reqChat.organizing")}</div>
              {b.ask?.options.map((o, j) => (
                <button
                  key={j}
                  className="rc-ask-opt"
                  disabled={b.open || !onPick}
                  onClick={() => onPick?.(o.label)}
                >
                  <span className="rc-ask-num">{j + 1}</span>
                  <span className="rc-ask-label">{o.label}</span>
                  {o.desc && <span className="rc-ask-desc">{o.desc}</span>}
                </button>
              ))}
            </div>
          );
        }
        if (b.kind === "text") return <Markdown key={i}>{b.body}</Markdown>;
        const isDoc = b.kind === "doc";
        const adopt = isDoc ? onAdoptDoc : onAdopt;
        return (
          <div className="rc-plan-block" key={i}>
            <div className="rc-plan-head">
              <span className="rc-plan-title">{isDoc ? t("reqChat.blockDoc") : t("reqChat.blockPlan")}</span>
              {adopt && (
                <Button
                  size="small"
                  type="primary"
                  ghost
                  loading={adopting}
                  disabled={b.open}
                  onClick={async () => {
                    setAdopting(true);
                    try {
                      await adopt(b.body);
                    } catch (e: any) {
                      message.error(e.message);
                    } finally {
                      setAdopting(false);
                    }
                  }}
                >
                  {isDoc ? t("reqChat.adoptDoc") : t("reqChat.adoptPlan")}
                </Button>
              )}
            </div>
            <Markdown>{b.body}</Markdown>
          </div>
        );
      })}
    </div>
  );
}

/** 等待首字节的加载泡：超过 3 秒显示已等待时长，长时间思考时不像卡死。 */
function LoadingWait() {
  const { t } = useTranslation();
  const [sec, setSec] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setSec((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="rc-load">
      <span />
      <span />
      <span />
      {sec >= 3 && <em className="rc-load-sec">{t("reqChat.waited", { sec })}</em>}
    </div>
  );
}

/** 单轮思考链：自持展开态（多轮历史各展开各的） */
export function TurnChain({
  evts,
  live,
  prefix,
  subs,
  thinkTokens,
}: {
  evts: any[];
  live?: boolean;
  prefix: string;
  subs?: SubItem[];
  thinkTokens?: number;
}) {
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const items = buildChain(evts, { prefix, live, thinkTokens, subs });
  const lastKey = items[items.length - 1]?.key;
  const expandedKeys = chainExpandedKeys(manual, live ? lastKey : undefined);
  const onExpand = chainExpandSetter(setManual, expandedKeys);
  return (
    <div className="rc-process">
      <ThoughtChain items={items} expandedKeys={expandedKeys} onExpand={onExpand} />
    </div>
  );
}

export default function RequirementChat() {
  const { t } = useTranslation();
  const { id } = useParams();
  const reqId = Number(id);
  const nav = useNavigate();

  const [req, setReq] = useState<any>(null);
  // 需求已被他人认领：后端 403，页面只给提示，不渲染详情
  const [denied, setDenied] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [docs, setDocs] = useState<any[]>([]);
  const [planVers, setPlanVers] = useState<any[]>([]);
  // EGM Goldfish 评审：报告留库，抽屉里回看；resolve 时引用其中一份
  const [reviews, setReviews] = useState<any[]>([]);
  const [reviewOpen, setReviewOpen] = useState(false);
  // 当前展开的批注角标 key（章节规范化标题或 "doc"）；方案一滚动就收起
  const [annoOpen, setAnnoOpen] = useState<string | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [tasks, setTasks] = useState<any[]>([]);
  const [preview, setPreview] = useState<Preview>(null);
  const [err, setErr] = useState("");
  const [input, setInput] = useState("");
  const [ask, setAsk] = useState<Ask | null>(null);
  // 本轮 plan 会话过程（主会话思考/工具事件，SSE plan_raw 实时流入；下一轮发送时清空）
  const [liveEvts, setLiveEvts] = useState<any[]>([]);
  const [thinkTokens, setThinkTokens] = useState(0);
  // 子代理实时进展（claude task_progress 事件按 tool_use_id 分组，即会话树的子节点）
  const [subs, setSubs] = useState<
    { key: string; desc: string; subagent_type: string; tool_uses: number; total_tokens: number }[]
  >([]);
  // AI 对话栏：页面右侧常驻停靠（占位不遮挡，默认展开）
  const [chatOpen, setChatOpen] = useState(true);
  // 清除会话后 +1 强制重连 SSE：回放空历史，顺带清掉所有实时态
  const [streamNonce, setStreamNonce] = useState(0);
  // 输入框里的团队/专家：每轮随消息下发，切换后下一轮的 claude 请求头即生效
  const [teamId, setTeamId] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  // 主区（需求对话）与右侧 AI 栏之间的分隔条：宽度可拖，记住上次位置
  const [dockW, setDockW] = useState<number>(() => {
    const v = Number(localStorage.getItem("vega.dockW"));
    return v >= DOCK_MIN ? v : DOCK_DEFAULT;
  });
  const [dragging, setDragging] = useState(false);
  const shellRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; w: number; max: number } | null>(null);
  const busyRef = useRef(false);

  // 阶段由文档/方案是否定稿决定；提到 SSE effect 之前，effect 才能依赖它按模式重连
  const chatMode: "requirement" | "plan" | null =
    req && req.status === "claimed" ? (docs.some((d) => d.is_final) ? "plan" : "requirement") : null;

  const reload = async () => {
    // 被他人认领后详情只对认领人开放：后端 403 就停在这里给出提示，
    // 不要继续往下发后续请求（会连着弹一串同样的报错）
    try {
      // 一起取完再落状态：req 先到、docs 后到会让「当前模式」闪一下需求讨论
      const [r, d, taskList] = await Promise.all([
        api<any>(`/api/requirements/${reqId}`),
        api<any[]>(`/api/requirements/${reqId}/docs`),
        api<any[]>(`/api/dev-tasks?requirement_id=${reqId}`),
      ]);
      setReq(r);
      setDocs(d);
      setTasks(taskList);
    } catch (e: any) {
      setDenied(e.message);
      return;
    }
    api<any[]>(`/api/requirements/${reqId}/plan-versions`).then(setPlanVers).catch(() => setPlanVers([]));
    api<any[]>(`/api/requirements/${reqId}/plan/reviews`).then(setReviews).catch(() => setReviews([]));
  };
  useEffect(() => {
    reload();
    setPreview(null);
    setAsk(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId]);

  useEffect(() => {
    // 预览升级为主区后自动选中：优先最新方案快照，无方案再取定稿文档
    setPreview((p) => {
      if (p) return p;
      if (planVers.length) return { type: "plan", data: planVers[planVers.length - 1] };
      if (docs.length) return { type: "doc", data: docs.find((d) => d.is_final) || docs[docs.length - 1] };
      return p;
    });
  }, [planVers, docs]);

  // 切模式/换需求：输入框的团队/专家先归零，再由该模式的历史回放恢复上次选择
  useEffect(() => {
    setTeamId(null);
    setAgentId(null);
  }, [reqId, chatMode]);

  useEffect(() => {
    localStorage.setItem("vega.dockW", String(Math.round(dockW)));
  }, [dockW]);

  useEffect(() => {
    if (!chatMode) return;
    // 切换讨论模式：旧模式的流式/直播态全部清掉，历史由新连接的 ready 回放重建
    setMsgs([]);
    setStreaming("");
    setLiveEvts([]);
    setSubs([]);
    setThinkTokens(0);
    setErr("");
    setAsk(null);
    setBusy(false);
    busyRef.current = false;
    // SSE：连接 → 历史回放（ready 前的 message 全量重建）→ 实时事件。
    // 断线自动重连：连接被掐时 fetch/read 立刻报错；反代吞掉上游断开时
    // 由 openStream 的心跳看门狗判死。重连后的全量回放会补上断线期间
    // 已落库的 AI 回复 —— 否则 busy 永远不清，看起来像 AI 不回复。
    const ac = new AbortController();
    let failures = 0;
    // 只在本次连接的首个 ready 恢复选择：重连不清掉用户刚改、还没发的选择
    let pickerRestored = false;
    const run = async () => {
      while (!ac.signal.aborted) {
        const replay: Msg[] = [];
        let streamingAcc = "";
        try {
          await openStream(
            reqId,
            ac.signal,
            (evt) => {
              failures = 0;
              if (evt.type === "message") replay.push(evt);
              else if (evt.type === "ready") {
                setMsgs(replay);
                setErr("");
                if (!pickerRestored) {
                  pickerRestored = true;
                  const lastUser = [...replay].reverse().find((m) => m.role === "user");
                  setTeamId(lastUser?.team_id ?? null);
                  setAgentId(lastUser?.agent_id ?? null);
                }
                // 断线期间回复可能已生成落库：最后一条是 assistant 就解除等待态；
                // 反之最后一条是 user 说明本轮还在跑（刷新/重连后恢复等待态，思考块才可见）
                const last = replay[replay.length - 1];
                if (last && last.role === "assistant") {
                  streamingAcc = "";
                  setStreaming("");
                  // 回放里已带该轮过程事件，实时态清空避免同一条链画两遍
                  setLiveEvts([]);
                  setSubs([]);
                  setThinkTokens(0);
                  setBusy(false);
                  busyRef.current = false;
                } else if (last && last.role === "user") {
                  setBusy(true);
                  busyRef.current = true;
                  // 本轮已经跑出来的过程事件随 ready 补发：重连/刷新后思考链不空白
                  const pend: any[] = Array.isArray(evt.pending_events) ? evt.pending_events : [];
                  setLiveEvts(pend.filter((e) => e && e.kind));
                  setSubs(pend.filter((e) => e && e.subtype === "task_progress").map(subItem));
                }
              } else if (evt.type === "plan_raw") {
                const e = evt.event || {};
                if (e.subtype === "thinking_tokens") {
                  // 每 token 一跳，按 32 token 一档节流，避免每秒几十次重渲
                  const n = e.estimated_tokens || 0;
                  setThinkTokens((p) => (Math.floor(n / 32) !== Math.floor(p / 32) ? n : p));
                } else if (e.subtype === "task_progress") {
                  const item = subItem(e);
                  setSubs((list) => {
                    const i = list.findIndex((s) => s.key === item.key);
                    return i >= 0 ? list.map((s, j) => (j === i ? item : s)) : [...list, item];
                  });
                } else {
                  // 归一化块（thinking / tool_use / tool_result）自带 kind；system 事件
                  //（task_started / hook_* / api_retry …）只有 type/subtype，补 raw 才能进链
                  setLiveEvts((l) => [...l, e.kind ? e : { ...e, kind: "raw" }]);
                }
              } else if (evt.type === "plan_text") {
                streamingAcc += evt.text;
                setStreaming(streamingAcc);
              } else if (evt.type === "plan_done") {
                streamingAcc = "";
                setStreaming("");
                // 消息自带本轮过程事件 → 链随消息渲染；实时态清掉，避免重复
                setMsgs((m) => [...m, evt.message]);
                setLiveEvts([]);
                setSubs([]);
                setThinkTokens(0);
                setBusy(false);
                busyRef.current = false;
              } else if (evt.type === "plan_error") {
                streamingAcc = "";
                setStreaming("");
                setErr(evt.error || i18n.t("reqChat.execFailed"));
                setBusy(false);
                busyRef.current = false;
              } else if (evt.type === "plan_review_done") {
                // 评审是独立任务，不影响主对话的等待态；报告直接进抽屉列表
                const r = evt.review;
                setReviews((list) => [r, ...list.filter((x) => x.id !== r.id)]);
                if (r.status === "done") {
                  if (r.blockers)
                    message.warning(i18n.t("reqChat.reviewDoneBlockers", { n: r.blockers }));
                  else message.success(i18n.t("reqChat.reviewDoneOk"));
                } else if (r.status !== "running")
                  message.warning(i18n.t("reqChat.reviewUnfinished", { status: r.status }));
              }
            },
            chatMode,
          );
        } catch {
          if (ac.signal.aborted) return; // 组件卸载，安静退出
        }
        if (ac.signal.aborted) return;
        failures += 1;
        if (failures >= 2 && streamingAcc === "") setErr(i18n.t("reqChat.connLost"));
        await new Promise((r) => setTimeout(r, 2000));
      }
    };
    run();
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId, chatMode, streamNonce]);

  const send = async (
    text: string,
    egm?: "brainstorm" | "draft" | "resolve",
    reviewId?: number,
  ) => {
    setErr("");
    setAsk(null); // 直接发言视为「继续聊」，收起选择框
    setLiveEvts([]);
    setSubs([]);
    setThinkTokens(0);
    const optimisticId = -Date.now();
    setMsgs((m) => [...m, { id: optimisticId, role: "user", content: text }]);
    setBusy(true);
    busyRef.current = true;
    try {
      await api(`/api/requirements/${reqId}/plan/messages`, {
        method: "POST",
        body: JSON.stringify({
          content: text,
          mode: chatMode,
          team_id: teamId,
          agent_id: agentId,
          egm,
          review_id: reviewId,
        }),
      });
    } catch (e: any) {
      // 服务端拒收（如另一标签页已把阶段推进）时收回乐观气泡
      setMsgs((m) => m.filter((x) => x.id !== optimisticId));
      setBusy(false);
      busyRef.current = false;
      setErr(e.message);
    }
  };

  /** 清除会话（类似 /clear）：清空当前模式聊天记录并换新 claude session；文档与方案不动。 */
  const clearSession = async () => {
    if (!chatMode) return;
    try {
      await api(`/api/requirements/${reqId}/plan/clear`, {
        method: "POST",
        body: JSON.stringify({ mode: chatMode }),
      });
      setStreamNonce((n) => n + 1);
      message.success(t("reqChat.sessionCleared"));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  /** 停止本轮：后端立即落「已停止」消息并让 runner 杀掉 claude，plan_done 会清等待态。 */
  const stop = async () => {
    try {
      await api(`/api/requirements/${reqId}/plan/stop`, { method: "POST" });
    } catch (e: any) {
      // 已经没有在跑的任务：本地也把等待态收掉
      setBusy(false);
      busyRef.current = false;
      setStreaming("");
      message.info(e.message);
    }
  };

  /** 发起 Goldfish 评审：零上下文的独立任务，评审最新一版方案。 */
  const runReview = async () => {
    if (!planVers.length) {
      message.info(t("reqChat.reviewNeedPlan"));
      return;
    }
    setReviewBusy(true);
    try {
      const r = await api<any>(`/api/requirements/${reqId}/plan/review`, {
        method: "POST",
        body: JSON.stringify({ plan_version_id: planVers[planVers.length - 1].id }),
      });
      setReviews((list) => [r, ...list]);
      // 上一轮评审的过程事件还留在实时区的话，先清掉，别让两轮子代理混在同一张状态板
      if (!busy && !streaming) {
        setLiveEvts([]);
        setSubs([]);
        setThinkTokens(0);
      }
      message.success(t("reqChat.reviewSubmitted"));
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setReviewBusy(false);
    }
  };

  const finalizeDoc = async (doc: any) => {
    try {
      await api(`/api/requirements/${reqId}/docs/${doc.id}/finalize`, { method: "POST" });
      message.success(t("reqChat.docFinalized", { version: doc.version }));
      // 定稿即移交：自己不是开发的话这一刻就失去详情访问，直接回需求池，
      // 否则留在页面上只会看到 403 提示
      try {
        await api(`/api/requirements/${reqId}`);
      } catch {
        message.info(t("reqChat.handedOver"));
        nav("/reqs");
        return;
      }
      await reload();
      setPreview({ type: "doc", data: { ...doc, is_final: true } });
    } catch (e: any) {
      message.error(e.message);
    }
  };

  /** 撤回需求文档定稿：方案未定稿前可回退，回到需求讨论。 */
  const unfinalizeDoc = async (doc: any) => {
    try {
      await api(`/api/requirements/${reqId}/docs/${doc.id}/unfinalize`, { method: "POST" });
      message.success(t("reqChat.docUnfinalized", { version: doc.version }));
      await reload();
      setPreview({ type: "doc", data: { ...doc, is_final: false } });
    } catch (e: any) {
      message.error(e.message);
    }
  };

  /** 采用对话里的方案块：人点了才落成方案版本（重复点同内容不会重复成版）。 */
  const adoptPlan = async (content: string) => {
    const d = await api<{ id: number; version: number; created: boolean }>(
      `/api/requirements/${reqId}/plan-versions`,
      { method: "POST", body: JSON.stringify({ content }) },
    );
    message.success(
      d.created
        ? t("reqChat.planAdopted", { version: d.version })
        : t("reqChat.planAdoptedSame", { version: d.version }),
    );
    const list = await api<any[]>(`/api/requirements/${reqId}/plan-versions`);
    setPlanVers(list);
    // 采用会触发自动评审：立刻把 running 的评审拉回来，子代理状态板才知道这批事件还在跑
    api<any[]>(`/api/requirements/${reqId}/plan/reviews`)
      .then(setReviews)
      .catch(() => {});
    const v = list.find((x) => x.id === d.id);
    if (v) setPreview({ type: "plan", data: v });
    // 采用后追问：按此方案执行（定稿入任务池）还是继续聊
    if (v && !v.is_final) {
      setAsk({
        title: t("reqChat.planAskTitle", { version: v.version }),
        options: [
          {
            label: t("reqChat.planAskRun"),
            desc: t("reqChat.planAskRunDesc"),
            run: () => finalizePlan(v),
          },
          {
            label: t("reqChat.planAskChat"),
            desc: t("reqChat.planAskChatDesc"),
            run: () => {},
          },
        ],
      });
    }
  };

  /** 采用对话里的需求文档块：人点了才落成新版本（非定稿），定稿仍走预览区。 */
  const adoptDoc = async (content: string) => {
    const d = await api<{ id: number; filename: string; version: number; is_final: boolean; created: boolean }>(
      `/api/requirements/${reqId}/docs/from-ai`,
      { method: "POST", body: JSON.stringify({ content }) },
    );
    message.success(
      d.created
        ? t("reqChat.docAdopted", { version: d.version })
        : t("reqChat.docAdoptedSame", { version: d.version }),
    );
    const list = await api<any[]>(`/api/requirements/${reqId}/docs`);
    setDocs(list);
    const doc = list.find((x) => x.id === d.id);
    if (doc) setPreview({ type: "doc", data: doc });
  };

  const doFinalizePlan = async (v: any) => {
    try {
      await api(`/api/requirements/${reqId}/plan-versions/${v.id}/finalize`, { method: "POST" });
      message.success(t("reqChat.planFinalized", { version: v.version }));
      await reload();
      setPreview({ type: "plan", data: { ...v, is_final: true } });
    } catch (e: any) {
      message.error(e.message);
    }
  };

  /** 定稿前软提醒：这一版没有完成的评审时，让人确认一次再入任务池。 */
  const finalizePlan = async (v: any) => {
    const reviewed = reviews.some((r) => r.plan_version_id === v.id && r.status === "done");
    if (reviewed) return doFinalizePlan(v);
    Modal.confirm({
      title: t("reqChat.planNoReviewTitle", { version: v.version }),
      content: t("reqChat.planNoReviewContent"),
      okText: t("reqChat.okFinalize"),
      cancelText: t("reqChat.cancelReviewFirst"),
      onOk: () => doFinalizePlan(v),
    });
  };

  if (denied)
    return (
      <Page title={t("reqChat.detailTitle")} desc={t("reqChat.detailDesc")}>
        <div className="page-card" style={{ padding: 48, textAlign: "center" }}>
          <Typography.Title level={5} style={{ marginBottom: 8 }}>
            {denied}
          </Typography.Title>
          <Typography.Text type="secondary">{t("reqChat.deniedHint")}</Typography.Text>
          <div style={{ marginTop: 16 }}>
            <Button onClick={() => nav("/reqs")}>{t("reqChat.backToPool")}</Button>
          </div>
        </div>
      </Page>
    );
  if (!req) return <Spin style={{ marginTop: 80, width: "100%" }} />;
  const st = STATUS[req.status];
  const chatable = req.status === "claimed";
  // 阶段：需求文档未定稿 → 需求讨论（AI 可改文档）；已定稿 → 方案讨论；方案定稿 → 开发阶段
  const planFinal = ["confirmed", "archived", "online"].includes(req.status);
  const examples = examplesFor(chatMode ?? "plan");
  // 池中却认领不了 = 角色不对：文档已定稿该开发上，没定稿该产品上
  const claimHint = docs.some((d) => d.is_final)
    ? t("reqChat.claimHintFinalized")
    : t("reqChat.claimHintUnfinalized");

  /** 附件卡片的图标（按文件名后缀）。 */
  const fileIcon = (name: string): "markdown" | "word" | "default" =>
    /\.(md|markdown)$/i.test(name) ? "markdown" : /\.docx?$/i.test(name) ? "word" : "default";

  // 只有最后一条带提问的 AI 回复可点：之后的失败提示等消息不该把还挂着的提问锁死
  const lastAskId = [...msgs]
    .reverse()
    .find((m) => m.role === "assistant" && /<!--\s*vega:ask:begin\s*-->/i.test(m.content || ""))?.id;
  // 评审是独立任务：主对话的 busy 不亮，靠 running 的评审记录判断这批事件还在跑
  const reviewRunning = reviews.some((r) => r.status === "running");
  const liveTurn = !!(busy || streaming || (reviewRunning && liveEvts.length > 0));
  // 消息 → 气泡：带过程事件的 AI 回复前面插入该轮的思考链（顺序与直播一致）
  const bubbles: any[] = [];
  msgs.forEach((m) => {
    if (m.role === "assistant" && m.events?.length) {
      bubbles.push({
        key: `__proc-${m.id}`,
        role: "process",
        content: <TurnChain evts={m.events} prefix={`m${m.id}`} />,
      });
    }
    bubbles.push({
      key: String(m.id),
      role: m.role,
      content:
        m.role === "assistant" ? (
          <AssistantBody
            text={m.content}
            onAdopt={chatMode === "plan" ? adoptPlan : undefined}
            onAdoptDoc={chatMode === "requirement" ? adoptDoc : undefined}
            onPick={
              chatable && !busy && !streaming && m.id === lastAskId
                ? (label) => send(label)
                : undefined
            }
          />
        ) : (
          m.content
        ),
    });
  });
  // 等待首字节：骨架加载泡（Bubble loading）；流式回复：打字机逐字浮现
  if (busy && !streaming)
    bubbles.push({
      key: "__loading",
      role: "assistant",
      content: "",
      loading: true,
      loadingRender: () => <LoadingWait />,
    });
  if (streaming)
    bubbles.push({
      key: "__streaming",
      role: "assistant",
      content: streaming,
      // 内容已经整块到手，打字机只是过场：约 375 字/秒，长回复不拖几十秒
      typing: { effect: "typing", step: 6, interval: 16 },
    });

  // 本轮实时思考链：插在用户提问之后、正在生成的回复之前；
  // 本轮结束（plan_done）后清空实时态，改由落库事件随消息回放，刷新也不丢
  if (liveEvts.length > 0 || subs.length > 0) {
    bubbles.splice(busy || streaming ? bubbles.length - 1 : bubbles.length, 0, {
      key: "__process",
      role: "process",
      content: (
        <TurnChain
          evts={liveEvts}
          prefix="live"
          live={liveTurn}
          subs={subs}
          thinkTokens={thinkTokens}
        />
      ),
    });
  }

  // 主/子代理状态板：本轮有子代理才占地方；只有主会话时仅在被拦/等人时露出
  const agentRows = agentBoard(liveEvts, liveTurn);
  const showAgentBoard =
    chatable &&
    liveEvts.length > 0 &&
    (agentRows.length > 1 || agentRows[0]?.state === "wait" || agentRows[0]?.state === "block");

  const previewDoc = preview?.type === "doc" ? preview.data : null;
  const previewPlan = preview?.type === "plan" ? preview.data : null;

  // 当前预览版本的最近一次完成评审 → 文档批注；范围对不上章节的归入文档级。
  const annoReview = previewPlan
    ? reviews.find(
        (r) =>
          r.status === "done" &&
          r.plan_version_id === previewPlan.id &&
          Array.isArray(r.findings) &&
          r.findings.length > 0,
      )
    : null;
  const planAnno = annoReview ? groupFindings(previewPlan.content, annoReview.findings as Finding[]) : null;
  const annoComponents = planAnno
    ? {
        h1: headingWithAnno("h1", planAnno.byHeading, annoOpen, setAnnoOpen),
        h2: headingWithAnno("h2", planAnno.byHeading, annoOpen, setAnnoOpen),
        h3: headingWithAnno("h3", planAnno.byHeading, annoOpen, setAnnoOpen),
        h4: headingWithAnno("h4", planAnno.byHeading, annoOpen, setAnnoOpen),
      }
    : undefined;

  // 注入 AI 上下文的文件卡片：定稿文档（无定稿取最新版）+ 最新方案快照
  const ctxDoc = docs.find((d) => d.is_final) || docs[docs.length - 1];
  const ctxPlan = planVers[planVers.length - 1];

  // ---- 分隔条拖拽：AI 栏取 px 宽度，主区吃掉剩余空间 ----
  const dockMax = () => {
    const shellW = shellRef.current?.clientWidth ?? 0;
    return Math.max(DOCK_MIN, shellW - MAIN_MIN - SPLIT_W);
  };
  const splitDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = e.currentTarget.nextElementSibling as HTMLElement | null;
    // 窗口缩小后 CSS 会把 AI 栏压到比 dockW 更窄，先以实际宽度对齐，避免拖一下跳一截
    const w = Math.round(el?.getBoundingClientRect().width || dockW);
    setDockW(w);
    dragRef.current = { x: e.clientX, w, max: dockMax() };
    setDragging(true);
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // 合成事件没有活动指针可捕获；真实拖拽不会走到这里
    }
  };
  const splitMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    setDockW(Math.min(d.max, Math.max(DOCK_MIN, d.w - (e.clientX - d.x))));
  };
  const splitUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    setDragging(false);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };

  return (
    <div className="req-page">
      <div className="rc-shell" ref={shellRef}>
      <ProCard
        className="rc-main"
        title={
          <Space>
            <span>{t("reqChat.convTitle")}</span>
            <span className="mono" style={{ fontSize: 13, color: "rgba(0,0,0,0.45)" }}>
              {req.code}
            </span>
            <Tag color={st?.[0] ?? "default"}>{st ? t(st[1]) : req.status}</Tag>
          </Space>
        }
        subTitle={req.title}
        extra={
          <Space>
            {!chatOpen && (
              <Button size="small" onClick={() => setChatOpen(true)}>
                {t("reqChat.aiChat")}
              </Button>
            )}
            <Link to="/reqs">{t("reqChat.backToPool")}</Link>
          </Space>
        }
        bodyStyle={{ padding: 12 }}
      >
        <Row gutter={12}>
          {/* 左：文件列表（需求文档多版本 + 方案版本）+ 任务池 + 需求信息 */}
          <Col xs={24} lg={5}>
            <div className="panel rc-files">
              <h4>
                {t("reqChat.docSection")}{" "}
                {!planFinal && (
                  <Upload
                    showUploadList={false}
                    customRequest={async (opt) => {
                      const fd = new FormData();
                      fd.append("file", opt.file as any);
                      try {
                        const up = await api<{ is_final: boolean }>(`/api/requirements/${reqId}/docs`, {
                          method: "POST",
                          body: fd,
                        });
                        message.success(up.is_final ? t("reqChat.uploadedFinal") : t("reqChat.uploaded"));
                        try {
                          await api(`/api/requirements/${reqId}`);
                        } catch {
                          nav("/reqs");
                          return;
                        }
                        (opt as any).onSuccess?.({});
                        reload();
                      } catch (e: any) {
                        message.error(e.message);
                      }
                    }}
                  >
                    <Button size="small" type="text" icon={<InboxOutlined />}>
                      {t("reqChat.upload")}
                    </Button>
                  </Upload>
                )}
              </h4>
              {docs.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("reqChat.noDocs")}
                </Typography.Text>
              )}
              {docs.map((d) => (
                <div
                  key={d.id}
                  className={`rc-file${previewDoc?.id === d.id ? " on" : ""}`}
                  onClick={() => setPreview({ type: "doc", data: d })}
                  title={t("reqChat.clickPreview")}
                >
                  <span className="rc-file-name">{d.is_final && <span className="ver-final">🎯 </span>}{d.filename}</span>
                  <span className="rc-file-ver">v{d.version}</span>
                </div>
              ))}
              {planVers.length > 0 && (
                <>
                  <h4 style={{ marginTop: 12 }}>{t("reqChat.planVersions")}</h4>
                  {planVers.map((v) => (
                    <div
                      key={v.id}
                      className={`rc-file${previewPlan?.id === v.id ? " on" : ""}`}
                      onClick={() => setPreview({ type: "plan", data: v })}
                      title={t("reqChat.clickPreview")}
                    >
                      <span className="rc-file-name">{v.is_final && <span className="ver-final">🎯 </span>}{t("reqChat.planSnapshot")}</span>
                      <span className="rc-file-ver">v{v.version} · {(v.created_at || "").slice(5, 10)}</span>
                    </div>
                  ))}
                </>
              )}
            </div>

            {req.status === "confirmed" && (
              <div className="panel">
                <h4>{t("reqChat.taskPool")}</h4>
                {tasks.map((task) => {
                  const taskSt = TASK_STATUS[task.status];
                  return (
                  <div key={task.id} className="task-pill" title={task.scope || undefined}>
                    <span className="mono">{task.code}</span>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {task.title || "—"}
                    </span>
                    <Tag
                      color={task.status === "failed" ? "error" : task.status === "mr_created" ? "success" : "processing"}
                      style={{ marginInlineEnd: 0 }}
                    >
                      {taskSt ? t(taskSt) : task.status}
                    </Tag>
                    {task.mr_url ? (
                      <a href={task.mr_url} target="_blank" rel="noreferrer" className="mono">
                        MR !{task.mr_iid}
                      </a>
                    ) : null}
                    <Link to={`/tasks/${task.id}`}>{t("reqChat.taskDetail")}</Link>
                  </div>
                  );
                })}
              </div>
            )}

            <div className="panel">
              <h4>{t("reqChat.reqInfo")}</h4>
              <Descriptions size="small" column={1}>
                <Descriptions.Item label={t("reqChat.fDesc")}>{req.description || "—"}</Descriptions.Item>
                <Descriptions.Item label={t("reqChat.fProject")}>{req.project_name || `#${req.project_id}`}</Descriptions.Item>
                <Descriptions.Item label={t("reqChat.fClaimant")}>{req.claimant_name || "—"}</Descriptions.Item>
                <Descriptions.Item label={t("reqChat.fProposer")}>{req.proposer_name || "—"}</Descriptions.Item>
                <Descriptions.Item label={t("reqChat.fCreated")}>
                  <span className="mono">{(req.created_at || "").slice(0, 19)}</span>
                </Descriptions.Item>
              </Descriptions>
            </div>
          </Col>

          {/* 主区：文件/方案预览（页面主体；AI 对话在右侧抽屉） */}
          <Col xs={24} lg={19}>
            <div className="rc-preview">
              {previewDoc ? (
                <>
                  <div className="rc-preview-head">
                    <span className="rc-preview-title">
                      📄 {previewDoc.filename}
                      <Tag style={{ marginInlineStart: 8 }}>v{previewDoc.version}</Tag>
                      {previewDoc.is_final && <Tag color="success">{t("reqChat.finalTag")}</Tag>}
                    </span>
                    {!previewDoc.is_final && !planFinal && (
                      <Button size="small" type="primary" onClick={() => finalizeDoc(previewDoc)}>
                        {t("reqChat.setFinal")}
                      </Button>
                    )}
                    {previewDoc.is_final && !planFinal && (
                      <Popconfirm
                        title={t("reqChat.unfinalizeConfirm")}
                        onConfirm={() => unfinalizeDoc(previewDoc)}
                      >
                        <Button size="small">{t("reqChat.unfinalize")}</Button>
                      </Popconfirm>
                    )}
                  </div>
                  <div className="rc-preview-body">
                    <DocPreview reqId={reqId} doc={previewDoc} />
                  </div>
                </>
              ) : previewPlan ? (
                <>
                  <div className="rc-preview-head">
                    <span className="rc-preview-title">
                      📋 {t("reqChat.planSnapshot")}
                      <Tag style={{ marginInlineStart: 8 }}>v{previewPlan.version}</Tag>
                      {previewPlan.is_final && <Tag color="success">{t("reqChat.finalTag")}</Tag>}
                    </span>
                    {chatMode === "plan" && !previewPlan.is_final && (
                      <Button size="small" type="primary" onClick={() => finalizePlan(previewPlan)}>
                        {t("reqChat.finalizePlanBtn")}
                      </Button>
                    )}
                  </div>
                  <div className="rc-preview-body" onScroll={() => setAnnoOpen(null)}>
                    {planAnno && (
                      <div className="rc-anno-bar">
                        <span style={{ fontWeight: 600 }}>
                          <EgmMark /> {t("reqChat.goldfish")}
                        </span>
                        {(["Blocker", "Major", "Nit"] as const).map((sev) => {
                          const n = (annoReview.findings as Finding[]).filter(
                            (f) => (f.severity || "Nit") === sev,
                          ).length;
                          return n ? (
                            <Tag key={sev} color={SEV_ST[sev].color}>
                              {SEV_ST[sev].icon} {sev} {n}
                            </Tag>
                          ) : null;
                        })}
                        {planAnno.docLevel.length > 0 && (
                          <Popover
                            trigger="click"
                            open={annoOpen === "doc"}
                            onOpenChange={(v) => setAnnoOpen(v ? "doc" : null)}
                            title={t("reqChat.docLevelTitle", { n: planAnno.docLevel.length })}
                            content={
                              <div>
                                {planAnno.docLevel.map((f, i) => (
                                  <div
                                    key={i}
                                    style={{ marginBottom: i === planAnno.docLevel.length - 1 ? 0 : 12 }}
                                  >
                                    <FindingCard f={f} />
                                  </div>
                                ))}
                              </div>
                            }
                          >
                            <a>{t("reqChat.docLevel", { n: planAnno.docLevel.length })}</a>
                          </Popover>
                        )}
                      </div>
                    )}
                    <div className="md-body">
                      <Markdown components={annoComponents as any}>{previewPlan.content}</Markdown>
                    </div>
                  </div>
                </>
              ) : req.status === "pool" ? (
                <div className="rc-preview-empty">
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    {req.title}
                  </Typography.Title>
                  <Typography.Text type="secondary">{req.description || t("reqChat.noDesc")}</Typography.Text>
                  {req.can_claim ? (
                    <Button
                      type="primary"
                      onClick={async () => {
                        try {
                          await api(`/api/requirements/${reqId}/claim`, { method: "POST" });
                          reload();
                        } catch (e: any) {
                          message.error(e.message);
                        }
                      }}
                    >
                      {t("reqChat.claimReq")}
                    </Button>
                  ) : (
                    <Typography.Text type="secondary">{claimHint}</Typography.Text>
                  )}
                </div>
              ) : (
                <div className="rc-preview-empty">
                  <Typography.Text type="secondary">
                    {chatMode === "requirement" ? (
                      <>
                        {t("reqChat.noDocYet")}
                        <br />
                        {t("reqChat.noDocYetHint")}
                      </>
                    ) : (
                      <>
                        {t("reqChat.noPlanYet")}
                        <br />
                        {t("reqChat.noPlanYetHint")}
                      </>
                    )}
                  </Typography.Text>
                </div>
              )}
            </div>
          </Col>
        </Row>
      </ProCard>

      {chatOpen && (
        <div
          className={`rc-split${dragging ? " on" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label={t("reqChat.resizeAria")}
          aria-valuenow={Math.round(dockW)}
          aria-valuemin={DOCK_MIN}
          tabIndex={0}
          title={t("reqChat.resizeTitle")}
          onPointerDown={splitDown}
          onPointerMove={splitMove}
          onPointerUp={splitUp}
          onPointerCancel={splitUp}
          onDoubleClick={() => setDockW(DOCK_DEFAULT)}
          onKeyDown={(e) => {
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            e.preventDefault();
            const step = e.key === "ArrowLeft" ? 32 : -32;
            setDockW((w) => Math.min(dockMax(), Math.max(DOCK_MIN, w + step)));
          }}
        />
      )}
      {chatOpen && (
        <aside
          className="rc-dock"
          style={{ flex: `0 0 clamp(${DOCK_MIN}px, ${dockW}px, calc(100% - ${MAIN_MIN + SPLIT_W}px))` }}
        >
          <div className="rc-dock-head">
            <span>{t("reqChat.aiChat")}</span>
            {chatMode && (
              // 阶段由文档/方案是否定稿决定，另一侧不可用即当前阶段
              <Segmented
                size="small"
                value={chatMode}
                options={[
                  { label: t("reqChat.tabReq"), value: "requirement", disabled: chatMode !== "requirement" },
                  { label: t("reqChat.tabPlan"), value: "plan", disabled: chatMode !== "plan" },
                ]}
              />
            )}
            <Tag color={st?.[0] ?? "default"}>{st ? t(st[1]) : req.status}</Tag>
            {(busy || streaming) && <i className="sess-live" />}
            {chatMode && (
              <Popconfirm
                title={t("reqChat.clearConfirm")}
                description={t("reqChat.clearDesc")}
                okText={t("reqChat.clear")}
                cancelText={t("reqChat.cancel")}
                onConfirm={clearSession}
              >
                <Button size="small" type="text" icon={<ClearOutlined />} title={t("reqChat.clearSession")} />
              </Popconfirm>
            )}
            <button
              className="rc-dock-toggle"
              onClick={() => setChatOpen(false)}
              title={t("reqChat.collapseDock")}
            >
              »
            </button>
          </div>
        <div className="chat-box">
          {(ctxDoc || ctxPlan) && req.status !== "pool" && (
            <div className="rc-filecards">
              <span className="rc-filecards-label">{t("reqChat.introduced")}</span>
              {ctxDoc && (
                <FileCard
                  size="small"
                  name={ctxDoc.filename}
                  byte={ctxDoc.size}
                  description={`v${ctxDoc.version}${ctxDoc.is_final ? ` · ${t("reqChat.finalMark")}` : ""}`}
                  icon={fileIcon(ctxDoc.filename)}
                  onClick={() => setPreview({ type: "doc", data: ctxDoc })}
                />
              )}
              {ctxPlan && (
                <FileCard
                  size="small"
                  name={t("reqChat.planFileName", { version: ctxPlan.version })}
                  description={ctxPlan.is_final ? t("reqChat.finalMark") : t("reqChat.snapshot")}
                  icon="markdown"
                  onClick={() => setPreview({ type: "plan", data: ctxPlan })}
                />
              )}
            </div>
          )}
          {showAgentBoard && (
            <AgentBoard
              rows={agentRows}
              evts={liveEvts}
              live={liveTurn}
              // 评审是独立任务：它跑着的时候发言会开一条并行 plan 轮，事件会混在一起
              onAnswer={
                chatable && !busy && !streaming && !reviewRunning ? (text) => send(text) : undefined
              }
            />
          )}
          {req.status === "pool" ? (
            <div className="chat-empty">
              <Typography.Title level={5} style={{ margin: 0 }}>
                {req.title}
              </Typography.Title>
              <Typography.Text type="secondary">{t("reqChat.claimToChat")}</Typography.Text>
              {req.can_claim ? (
                <Button
                  type="primary"
                  onClick={async () => {
                    try {
                      await api(`/api/requirements/${reqId}/claim`, { method: "POST" });
                      reload();
                    } catch (e: any) {
                      message.error(e.message);
                    }
                  }}
                >
                  {t("reqChat.claimReq")}
                </Button>
              ) : (
                <Typography.Text type="secondary">{claimHint}</Typography.Text>
              )}
            </div>
          ) : chatable ? (
            <>
              {msgs.length === 0 && !busy && !streaming && (
                <div style={{ overflowY: "auto" }}>
                  <Prompts
                    vertical
                    title={t("reqChat.tryThese")}
                    items={examples.map((e, i) => ({ key: String(i), label: e }))}
                    onItemClick={({ data }) => send(String(data.label ?? ""))}
                  />
                </div>
              )}
              {(msgs.length > 0 || busy || streaming) && (
                <Bubble.List
                  style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
                  autoScroll
                  items={bubbles}
                  role={{
                    user: {
                      placement: "end",
                      variant: "filled",
                      avatar: <Avatar icon={<UserOutlined />} style={{ background: "#1677ff" }} />,
                    },
                    process: {
                      placement: "start",
                      variant: "borderless",
                      // 气泡默认按内容收缩，思考链会被挤成一条窄柱
                      rootClassName: "rc-proc-bubble",
                      // 每轮链各自成泡（content 已是 TurnChain 元素），不套统一渲染
                      contentRender: (c: any) => c,
                    },
                    assistant: {
                      placement: "start",
                      variant: "outlined",
                      avatar: (
                        <Avatar
                          icon={<RobotOutlined />}
                          style={{ background: "#e6f4ff", color: "#1677ff" }}
                        />
                      ),
                      header: <span className="rc-bubble-name">Vega AI</span>,
                      // 历史消息在 bubbles 里已渲染成元素；流式气泡仍是字符串，现算
                      contentRender: (c: any) =>
                        typeof c === "string" ? (
                          <AssistantBody
                            text={c}
                            onAdopt={chatMode === "plan" ? adoptPlan : undefined}
                            onAdoptDoc={chatMode === "requirement" ? adoptDoc : undefined}
                          />
                        ) : (
                          c
                        ),
                      styles: { content: { fontSize: 13, lineHeight: 1.7 } },
                    },
                  }}
                />
              )}
              {err && (
                <Alert
                  type="error"
                  message={err}
                  style={{ margin: "8px 0" }}
                  closable
                  onClose={() => setErr("")}
                />
              )}
              {ask && (
                <div className="rc-ask">
                  <div className="rc-ask-title">{ask.title}</div>
                  {ask.options.map((o, i) => (
                    <button
                      key={i}
                      className="rc-ask-opt"
                      onClick={() => {
                        setAsk(null);
                        o.run();
                      }}
                    >
                      <span className="rc-ask-num">{i + 1}</span>
                      <span className="rc-ask-label">{o.label}</span>
                      {o.desc && <span className="rc-ask-desc">{o.desc}</span>}
                    </button>
                  ))}
                </div>
              )}
              {chatMode === "plan" && (
                <Space size={6} wrap style={{ marginTop: 8 }}>
                  <Button size="small" disabled={busy} onClick={() => send(t("reqChat.brainstormPrompt"), "brainstorm")}>
                    {t("reqChat.brainstorm")}
                  </Button>
                  <Button size="small" disabled={busy} onClick={() => send(t("reqChat.draftPrompt"), "draft")}>
                    {t("reqChat.draft")}
                  </Button>
                  <Button
                    size="small"
                    icon={<EgmMark />}
                    loading={reviewBusy}
                    disabled={!planVers.length}
                    onClick={runReview}
                  >
                    {t("reqChat.startReview")}
                  </Button>
                  <Badge
                    dot={reviews.some((r) => r.status === "done" && r.blockers > 0)}
                    title={t("reqChat.hasBlockers")}
                  >
                    <Button
                      size="small"
                      disabled={busy || !reviews.some((r) => r.status === "done")}
                      onClick={() => {
                        const latest = reviews.find((r) => r.status === "done");
                        if (latest) send(t("reqChat.revisePrompt"), "resolve", latest.id);
                      }}
                    >
                      {t("reqChat.reviseByVerdict")}
                    </Button>
                  </Badge>
                  <Button size="small" type="text" onClick={() => setReviewOpen(true)}>
                    {t("reqChat.reports")}{reviews.length ? ` (${reviews.length})` : ""}
                  </Button>
                </Space>
              )}
              <Sender
                style={{ marginTop: 8 }}
                loading={busy}
                onCancel={stop}
                value={input}
                onChange={setInput}
                placeholder={
                  chatMode === "requirement"
                    ? t("reqChat.placeholderReq")
                    : t("reqChat.placeholderPlan")
                }
                onSubmit={(text) => {
                  send(text);
                  setInput("");
                }}
                footer={
                  <div className="rc-sender-foot">
                    <div className="rc-sender-tip">{t("reqChat.senderTip")}</div>
                    <div className="rc-sender-foot-row">
                      <AmSelect
                        kind="team"
                        style={{ flex: 1, minWidth: 0 }}
                        prefix={<ApartmentOutlined />}
                        value={teamId}
                        onChange={(v) => {
                          setTeamId(v ?? null);
                          setAgentId(null); // 专家属于团队：换团队后旧专家不再适用
                        }}
                        placeholder={t("reqChat.teamDefault")}
                      />
                      <AmSelect
                        kind="agent"
                        teamId={teamId ?? undefined}
                        style={{ flex: 1, minWidth: 0 }}
                        prefix={<RobotOutlined />}
                        value={agentId}
                        onChange={(v) => setAgentId(v ?? null)}
                        placeholder={t("reqChat.agentDefault")}
                      />
                    </div>
                  </div>
                }
              />
            </>
          ) : (
            <div className="chat-empty">
              <Typography.Title level={5} style={{ margin: 0 }}>
                {t("reqChat.finalizedBanner")}
              </Typography.Title>
              <Typography.Text type="secondary">
                {t("reqChat.finalizedBannerDesc")}
              </Typography.Text>
            </div>
          )}
        </div>
        </aside>
      )}
      <Drawer
        title={t("reqChat.reviewDrawer")}
        width={720}
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
      >
        {reviews.length === 0 ? (
          <Empty description={t("reqChat.noReviews")} />
        ) : (
          reviews.map((r) => {
            const rst = REVIEW_ST[r.status];
            return (
              <div key={r.id} className="rc-review">
                <Space wrap>
                  <Tag color={rst?.color ?? "default"}>{rst ? t(rst.text) : r.status}</Tag>
                  {!!r.blockers && <Tag color="red">{t("reqChat.blockersTag", { n: r.blockers })}</Tag>}
                  <span style={{ fontSize: 12, opacity: 0.6 }}>{r.created_at}</span>
                  <Button
                    size="small"
                    disabled={r.status !== "done" || busy}
                    onClick={() => {
                      setReviewOpen(false);
                      send(t("reqChat.revisePrompt"), "resolve", r.id);
                    }}
                  >
                    {t("reqChat.reviseByThis")}
                  </Button>
                </Space>
                {r.report_md ? (
                  <div className="md-body" style={{ marginTop: 8 }}>
                    <Markdown>{r.report_md}</Markdown>
                  </div>
                ) : (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {r.status === "running" ? t("reqChat.reviewInProgress") : t("reqChat.noReport")}
                  </Typography.Text>
                )}
              </div>
            );
          })
        )}
      </Drawer>
      </div>
    </div>
  );
}
