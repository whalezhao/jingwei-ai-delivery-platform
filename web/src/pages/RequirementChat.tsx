import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Page from "../Page";
import { Bubble, FileCard, Prompts, Sender } from "@ant-design/x";
import ThoughtChain from "@ant-design/x/es/thought-chain";
import type { ThoughtChainItemType } from "@ant-design/x/es/thought-chain";
import {
  Alert,
  Avatar,
  Button,
  Col,
  Descriptions,
  Popconfirm,
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
  CheckOutlined,
  ClearOutlined,
  CloseOutlined,
  CodeOutlined,
  EditOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FormOutlined,
  GlobalOutlined,
  InboxOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SearchOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons";
import Markdown from "react-markdown";
import AmSelect from "../components/AmSelect";
import DocPreview from "../components/DocPreview";
import { rawNode } from "../eventNodes";
import { api, token } from "../api";

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

const STATUS: Record<string, [string, string]> = {
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
  mr_created: "待审核",
  reviewing: "审核中",
  cicd: "CICD",
  done: "完成",
  failed: "失败",
};

/** 空对话时的起手示例（按当前讨论模式给，比一个光标干等着好）。 */
const EXAMPLES: Record<"requirement" | "plan", string[]> = {
  requirement: ["帮我梳理这个需求的目标和边界", "这个需求有哪些遗漏的验收标准？", "把讨论结果整理成需求文档"],
  plan: ["帮我梳理这个需求的实现步骤", "这个需求有哪些边界情况要覆盖？", "把方案的实现要点列出来"],
};

/** 服务端每 15s 发一跳 keepalive。vite/nginx 等反代在上游断开时不会掐断
 *  下游连接，read() 会永远挂着 —— 超过这个时限没收到任何字节就判死重连。 */
const STALE_MS = 40_000;

/** 右侧 AI 栏宽度（像素），拖动分隔条调整；主区至少保留 MAIN_MIN。 */
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
const TOOL_META: Record<string, { icon: React.ReactNode; label: string; arg?: string }> = {
  Bash: { icon: <CodeOutlined />, label: "执行命令", arg: "command" },
  Write: { icon: <FormOutlined />, label: "创建文件", arg: "file_path" },
  Edit: { icon: <EditOutlined />, label: "编辑文件", arg: "file_path" },
  Read: { icon: <FileTextOutlined />, label: "读取文件", arg: "file_path" },
  Grep: { icon: <SearchOutlined />, label: "搜索内容", arg: "pattern" },
  Glob: { icon: <FileSearchOutlined />, label: "匹配文件", arg: "pattern" },
  Task: { icon: <RobotOutlined />, label: "派发子任务", arg: "description" },
  WebFetch: { icon: <GlobalOutlined />, label: "抓取网页", arg: "url" },
  WebSearch: { icon: <GlobalOutlined />, label: "联网搜索", arg: "query" },
  AskUserQuestion: { icon: <QuestionCircleOutlined />, label: "向你确认", arg: "question" },
  ExitPlanMode: { icon: <CheckOutlined />, label: "提交方案", arg: "plan" },
};

export function toolMeta(name?: string) {
  return TOOL_META[name || ""] || { icon: <ToolOutlined />, label: name || "工具", arg: undefined };
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
  kind: "think" | "tool" | "sub" | "raw";
  toolName?: string;
  arg?: string;
};

/** 一轮过程事件 → 思考链节点：每次思考可折叠连成链、工具按类型出动作行、
 *  子代理一个节点。prefix 区分不同回合，避免多轮节点 key 撞车串展开态。 */
export function buildChain(
  evts: any[],
  opts: { prefix: string; live?: boolean; thinkTokens?: number; subs?: SubItem[] },
): ChainNode[] {
  const { prefix, live, thinkTokens, subs } = opts;
  const items: ChainNode[] = [];
  evts.forEach((e, i) => {
    if (e.kind === "thinking") {
      items.push({
        kind: "think",
        key: `${prefix}-think-${e.id || i}`,
        icon: <BulbOutlined />,
        title: "深度思考",
        collapsible: true,
        content: thinkBlock(e.text || ""),
      });
    } else if (e.kind === "tool_use") {
      const m = toolMeta(e.name);
      const arg = toolArg(e.name, e.input || "");
      items.push({
        kind: "tool",
        key: `${prefix}-${e.id || i}`,
        title: m.label,
        status: "loading",
        collapsible: true,
        toolName: e.name,
        arg,
        content: actionRow(m.icon, arg || "…"),
      });
    } else if (e.kind === "tool_result") {
      // 结果无 tool_use_id，按流序配对最近的待定工具项
      const it = [...items].reverse().find((n) => n.kind === "tool" && n.status === "loading");
      if (it) {
        const m = toolMeta(it.toolName);
        const line = (e.content || "").split("\n")[0].slice(0, 100);
        it.status = e.is_error ? "error" : "success";
        it.content = (
          <>
            {actionRow(m.icon, it.arg || "…")}
            {actionRow(
              e.is_error ? <CloseOutlined /> : <CheckOutlined />,
              line || "（无输出）",
              e.is_error ? "err" : "ok",
              e.is_error ? "执行出错" : "完成",
            )}
            {e.is_error && <pre className="tc-body tc-err">{e.content}</pre>}
          </>
        );
      }
    } else if (e.kind === "raw") {
      // task_progress 已由下面的子代理节点承接，再画一遍就重复了
      if (e.subtype === "task_progress") return;
      // 其余 system 事件（子任务/压缩/重试/拒答…）统一由 eventNodes 出节点，认不出才折叠 JSON
      const node = rawNode(e, `${prefix}-raw-${e.uuid || i}`);
      if (node) items.push(node);
    }
  });
  const subList = subs ?? deriveSubs(evts);
  subList.forEach((s) => {
    items.push({
      kind: "sub",
      key: `${prefix}-sub-${s.key}`,
      icon: <RobotOutlined />,
      title: s.subagent_type || "子代理",
      description: s.desc,
      footer: `${s.tool_uses} 工具 · ${(s.total_tokens / 1000).toFixed(1)}k tokens`,
      status: live ? "loading" : undefined,
    });
  });
  const last = items.length - 1;
  items.forEach((n, i) => {
    const isLast = i === last;
    n.blink = false;
    if (n.kind === "think") {
      // 思考中 = 转圈（status 压过灯泡图标）+ token 计数；结束回落灯泡
      if (live && isLast) {
        n.status = "loading";
        n.description = `思考中 · ~${thinkTokens ?? 0} tokens`;
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
  const [adopting, setAdopting] = useState(false);
  return (
    <div className="md-body">
      {splitPlanBlocks(text).map((b, i) => {
        if (b.kind === "ask") {
          return (
            <div className="rc-ask" key={i}>
              <div className="rc-ask-title">{b.ask?.question ?? "正在整理问题…"}</div>
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
              <span className="rc-plan-title">{isDoc ? "📄 需求文档" : "📋 方案"}</span>
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
                  {isDoc ? "采用为文档" : "采用此方案"}
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
  const [sec, setSec] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSec((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="rc-load">
      <span />
      <span />
      <span />
      {sec >= 3 && <em className="rc-load-sec">已等待 {sec}s</em>}
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
      const [r, d, t] = await Promise.all([
        api<any>(`/api/requirements/${reqId}`),
        api<any[]>(`/api/requirements/${reqId}/docs`),
        api<any[]>(`/api/dev-tasks?requirement_id=${reqId}`),
      ]);
      setReq(r);
      setDocs(d);
      setTasks(t);
    } catch (e: any) {
      setDenied(e.message);
      return;
    }
    api<any[]>(`/api/requirements/${reqId}/plan-versions`).then(setPlanVers).catch(() => setPlanVers([]));
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
                setErr(evt.error || "执行失败");
                setBusy(false);
                busyRef.current = false;
              }
            },
            chatMode,
          );
        } catch {
          if (ac.signal.aborted) return; // 组件卸载，安静退出
        }
        if (ac.signal.aborted) return;
        failures += 1;
        if (failures >= 2 && streamingAcc === "") setErr("与服务的连接中断，正在重连…");
        await new Promise((r) => setTimeout(r, 2000));
      }
    };
    run();
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId, chatMode, streamNonce]);

  const send = async (text: string) => {
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
        body: JSON.stringify({ content: text, mode: chatMode, team_id: teamId, agent_id: agentId }),
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
      message.success("会话已清除");
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

  const finalizeDoc = async (doc: any) => {
    try {
      await api(`/api/requirements/${reqId}/docs/${doc.id}/finalize`, { method: "POST" });
      message.success(`v${doc.version} 已定稿`);
      // 定稿即移交：自己不是开发的话这一刻就失去详情访问，直接回需求池，
      // 否则留在页面上只会看到 403 提示
      try {
        await api(`/api/requirements/${reqId}`);
      } catch {
        message.info("需求已移交开发认领，你作为需求提出人已留痕");
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
      message.success(`v${doc.version} 已撤回定稿`);
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
    message.success(d.created ? `已采用为方案 v${d.version}` : `方案 v${d.version} 已是该内容，未重复成版`);
    const list = await api<any[]>(`/api/requirements/${reqId}/plan-versions`);
    setPlanVers(list);
    const v = list.find((x) => x.id === d.id);
    if (v) setPreview({ type: "plan", data: v });
    // 采用后追问：按此方案执行（定稿入任务池）还是继续聊
    if (v && !v.is_final) {
      setAsk({
        title: `方案 v${v.version} 已采用，接下来怎么走？`,
        options: [
          {
            label: "按此方案执行",
            desc: "定稿并创建开发任务，进入任务池",
            run: () => finalizePlan(v),
          },
          { label: "继续聊聊", desc: "不执行，继续和 AI 讨论方案", run: () => {} },
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
      d.created ? `已生成需求文档 v${d.version}，可在预览区设为定稿` : `文档 v${d.version} 已是该内容，未重复生成`,
    );
    const list = await api<any[]>(`/api/requirements/${reqId}/docs`);
    setDocs(list);
    const doc = list.find((x) => x.id === d.id);
    if (doc) setPreview({ type: "doc", data: doc });
  };

  const finalizePlan = async (v: any) => {
    try {
      await api(`/api/requirements/${reqId}/plan-versions/${v.id}/finalize`, { method: "POST" });
      message.success(`方案 v${v.version} 已定稿，任务已入任务池`);
      await reload();
      setPreview({ type: "plan", data: { ...v, is_final: true } });
    } catch (e: any) {
      message.error(e.message);
    }
  };

  if (denied)
    return (
      <Page title="需求详情" desc="被认领的需求只有认领人本人能打开">
        <div className="page-card" style={{ padding: 48, textAlign: "center" }}>
          <Typography.Title level={5} style={{ marginBottom: 8 }}>
            {denied}
          </Typography.Title>
          <Typography.Text type="secondary">认领之后详情对其他人关闭；需要跟进请联系认领人或管理员。</Typography.Text>
          <div style={{ marginTop: 16 }}>
            <Button onClick={() => nav("/reqs")}>返回需求池</Button>
          </div>
        </div>
      </Page>
    );
  if (!req) return <Spin style={{ marginTop: 80, width: "100%" }} />;
  const st = STATUS[req.status] || ["default", req.status];
  const chatable = req.status === "claimed";
  // 阶段：需求文档未定稿 → 需求讨论（AI 可改文档）；已定稿 → 方案讨论；方案定稿 → 开发阶段
  const planFinal = ["confirmed", "archived", "online"].includes(req.status);
  const examples = EXAMPLES[chatMode ?? "plan"];
  // 池中却认领不了 = 角色不对：文档已定稿该开发上，没定稿该产品上
  const claimHint = docs.some((d) => d.is_final)
    ? "需求已定稿，等待开发认领承接方案"
    : "需求未定稿，等待产品认领完善需求";

  /** 附件卡片的图标（按文件名后缀）。 */
  const fileIcon = (name: string): "markdown" | "word" | "default" =>
    /\.(md|markdown)$/i.test(name) ? "markdown" : /\.docx?$/i.test(name) ? "word" : "default";

  // 只有最后一条 AI 回复里的提问可点：历史提问已答过，再点会把旧答案重发一遍
  const lastAssistantId = [...msgs].reverse().find((m) => m.role === "assistant")?.id;
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
              chatable && !busy && !streaming && m.id === lastAssistantId
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
          live={!!(busy || streaming)}
          subs={subs}
          thinkTokens={thinkTokens}
        />
      ),
    });
  }

  const previewDoc = preview?.type === "doc" ? preview.data : null;
  const previewPlan = preview?.type === "plan" ? preview.data : null;

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
            <span>需求对话</span>
            <span className="mono" style={{ fontSize: 13, color: "rgba(0,0,0,0.45)" }}>
              {req.code}
            </span>
            <Tag color={st[0]}>{st[1]}</Tag>
          </Space>
        }
        subTitle={req.title}
        extra={
          <Space>
            {!chatOpen && (
              <Button size="small" onClick={() => setChatOpen(true)}>
                💬 AI 对话
              </Button>
            )}
            <Link to="/reqs">返回需求池</Link>
          </Space>
        }
        bodyStyle={{ padding: 12 }}
      >
        <Row gutter={12}>
          {/* 左：文件列表（需求文档多版本 + 方案版本）+ 任务池 + 需求信息 */}
          <Col xs={24} lg={5}>
            <div className="panel rc-files">
              <h4>
                需求文档{" "}
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
                        message.success(up.is_final ? "已上传（首版即定稿，需求已移交开发认领）" : "已上传新版本");
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
                      上传
                    </Button>
                  </Upload>
                )}
              </h4>
              {docs.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  暂无文档，支持 .md / .txt / .docx
                </Typography.Text>
              )}
              {docs.map((d) => (
                <div
                  key={d.id}
                  className={`rc-file${previewDoc?.id === d.id ? " on" : ""}`}
                  onClick={() => setPreview({ type: "doc", data: d })}
                  title="点击在右侧预览"
                >
                  <span className="rc-file-name">{d.is_final && <span className="ver-final">🎯 </span>}{d.filename}</span>
                  <span className="rc-file-ver">v{d.version}</span>
                </div>
              ))}
              {planVers.length > 0 && (
                <>
                  <h4 style={{ marginTop: 12 }}>方案版本</h4>
                  {planVers.map((v) => (
                    <div
                      key={v.id}
                      className={`rc-file${previewPlan?.id === v.id ? " on" : ""}`}
                      onClick={() => setPreview({ type: "plan", data: v })}
                      title="点击在右侧预览"
                    >
                      <span className="rc-file-name">{v.is_final && <span className="ver-final">🎯 </span>}方案快照</span>
                      <span className="rc-file-ver">v{v.version} · {(v.created_at || "").slice(5, 10)}</span>
                    </div>
                  ))}
                </>
              )}
            </div>

            {req.status === "confirmed" && (
              <div className="panel">
                <h4>任务池</h4>
                {tasks.map((t) => (
                  <div key={t.id} className="task-pill" title={t.scope || undefined}>
                    <span className="mono">{t.code}</span>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {t.title || "—"}
                    </span>
                    <Tag
                      color={t.status === "failed" ? "error" : t.status === "mr_created" ? "success" : "processing"}
                      style={{ marginInlineEnd: 0 }}
                    >
                      {TASK_STATUS[t.status] || t.status}
                    </Tag>
                    {t.mr_url ? (
                      <a href={t.mr_url} target="_blank" rel="noreferrer" className="mono">
                        MR !{t.mr_iid}
                      </a>
                    ) : null}
                    <Link to={`/tasks/${t.id}`}>详情</Link>
                  </div>
                ))}
              </div>
            )}

            <div className="panel">
              <h4>需求信息</h4>
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="描述">{req.description || "—"}</Descriptions.Item>
                <Descriptions.Item label="项目">{req.project_name || `#${req.project_id}`}</Descriptions.Item>
                <Descriptions.Item label="认领人">{req.claimant_name || "—"}</Descriptions.Item>
                <Descriptions.Item label="需求提出人">{req.proposer_name || "—"}</Descriptions.Item>
                <Descriptions.Item label="创建">
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
                      {previewDoc.is_final && <Tag color="success">🎯 定稿</Tag>}
                    </span>
                    {!previewDoc.is_final && !planFinal && (
                      <Button size="small" type="primary" onClick={() => finalizeDoc(previewDoc)}>
                        设为定稿版本
                      </Button>
                    )}
                    {previewDoc.is_final && !planFinal && (
                      <Popconfirm
                        title="撤回定稿后回到需求讨论，确定撤回？"
                        onConfirm={() => unfinalizeDoc(previewDoc)}
                      >
                        <Button size="small">撤回定稿</Button>
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
                      📋 方案快照
                      <Tag style={{ marginInlineStart: 8 }}>v{previewPlan.version}</Tag>
                      {previewPlan.is_final && <Tag color="success">🎯 定稿</Tag>}
                    </span>
                    {chatMode === "plan" && !previewPlan.is_final && (
                      <Button size="small" type="primary" onClick={() => finalizePlan(previewPlan)}>
                        定稿此版并入任务池
                      </Button>
                    )}
                  </div>
                  <div className="rc-preview-body">
                    <div className="md-body">
                      <Markdown>{previewPlan.content}</Markdown>
                    </div>
                  </div>
                </>
              ) : req.status === "pool" ? (
                <div className="rc-preview-empty">
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    {req.title}
                  </Typography.Title>
                  <Typography.Text type="secondary">{req.description || "（无描述）"}</Typography.Text>
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
                      认领此需求
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
                        还没有需求文档
                        <br />
                        右侧 💬 与 AI 讨论需求，AI 给出文档后点回复里的「采用为文档」生成版本
                      </>
                    ) : (
                      <>
                        还没有方案版本
                        <br />
                        右侧 💬 与 AI 对话，AI 给出方案后点回复里的「采用此方案」生成版本
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
          aria-label="调整需求对话与 AI 对话的宽度"
          aria-valuenow={Math.round(dockW)}
          aria-valuemin={DOCK_MIN}
          tabIndex={0}
          title="拖动调整宽度，双击复位"
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
            <span>💬 AI 对话</span>
            {chatMode && (
              // 阶段由文档/方案是否定稿决定，另一侧不可用即当前阶段
              <Segmented
                size="small"
                value={chatMode}
                options={[
                  { label: "✍️ 需求讨论", value: "requirement", disabled: chatMode !== "requirement" },
                  { label: "📋 方案讨论", value: "plan", disabled: chatMode !== "plan" },
                ]}
              />
            )}
            <Tag color={st[0]}>{st[1]}</Tag>
            {(busy || streaming) && <i className="sess-live" />}
            {chatMode && (
              <Popconfirm
                title="清除当前会话？"
                description="聊天记录会清空，下一句将开启全新会话；文档与方案不受影响。"
                okText="清除"
                cancelText="取消"
                onConfirm={clearSession}
              >
                <Button size="small" type="text" icon={<ClearOutlined />} title="清除会话" />
              </Popconfirm>
            )}
            <button
              className="rc-dock-toggle"
              onClick={() => setChatOpen(false)}
              title="收起对话栏"
            >
              »
            </button>
          </div>
        <div className="chat-box">
          {(ctxDoc || ctxPlan) && req.status !== "pool" && (
            <div className="rc-filecards">
              <span className="rc-filecards-label">已引入</span>
              {ctxDoc && (
                <FileCard
                  size="small"
                  name={ctxDoc.filename}
                  byte={ctxDoc.size}
                  description={`v${ctxDoc.version}${ctxDoc.is_final ? " · 定稿" : ""}`}
                  icon={fileIcon(ctxDoc.filename)}
                  onClick={() => setPreview({ type: "doc", data: ctxDoc })}
                />
              )}
              {ctxPlan && (
                <FileCard
                  size="small"
                  name={`方案 v${ctxPlan.version}.md`}
                  description={ctxPlan.is_final ? "定稿" : "快照"}
                  icon="markdown"
                  onClick={() => setPreview({ type: "plan", data: ctxPlan })}
                />
              )}
            </div>
          )}
          {req.status === "pool" ? (
            <div className="chat-empty">
              <Typography.Title level={5} style={{ margin: 0 }}>
                {req.title}
              </Typography.Title>
              <Typography.Text type="secondary">认领后与 AI 对话制定执行方案</Typography.Text>
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
                  认领此需求
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
                    title="试试这些"
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
              <Sender
                style={{ marginTop: 8 }}
                loading={busy}
                onCancel={stop}
                value={input}
                onChange={setInput}
                placeholder={
                  chatMode === "requirement"
                    ? "和 AI 讨论需求（AI 可给出完整文档，点「采用为文档」落版本）"
                    : "和 AI 讨论执行方案（plan 模式，多轮可续聊）"
                }
                onSubmit={(t) => {
                  send(t);
                  setInput("");
                }}
                footer={
                  <div className="rc-sender-foot">
                    <div className="rc-sender-tip">团队 / 专家可随时切换，下一轮对话生效；留空用项目/研发组默认</div>
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
                        placeholder="团队：默认"
                      />
                      <AmSelect
                        kind="agent"
                        teamId={teamId ?? undefined}
                        style={{ flex: 1, minWidth: 0 }}
                        prefix={<RobotOutlined />}
                        value={agentId}
                        onChange={(v) => setAgentId(v ?? null)}
                        placeholder="专家：团队默认"
                      />
                    </div>
                  </div>
                }
              />
            </>
          ) : (
            <div className="chat-empty">
              <Typography.Title level={5} style={{ margin: 0 }}>
                方案已定稿 · 开发阶段
              </Typography.Title>
              <Typography.Text type="secondary">
                需求与方案不再支持讨论和修改；任务已入池，左侧「任务池」与「开发任务」页可跟踪进度
              </Typography.Text>
            </div>
          )}
        </div>
        </aside>
      )}
      </div>
    </div>
  );
}
