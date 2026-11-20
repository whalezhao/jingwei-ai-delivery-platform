import {
  Fragment,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Database,
  Focus,
  Play,
  Plus,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import Page from "../Page";
import { qa, qaStream, type QaCase, type QaCaseVersion, type QaFeature } from "../qa";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import MiniTable from "../ui/MiniTable";
import Badge from "../ui/Badge";
import SearchSelect from "../ui/SearchSelect";
import Textarea from "../ui/Textarea";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Drawer } from "../components/motion/drawer";
import { Input } from "../components/motion/input";
import type { TableColumn } from "../components/motion/table";
import { Switch } from "../components/motion/switch";

const DEFAULT_YAML = "cases:\n  - name: 用例名\n    steps:\n      - aiAssert: 页面正常\n";

type Step = { case: string; case_index: number; index: number; kind: string; text: string };

/** 步骤按 case 分组：一份 YAML 里可以有多个 case，编号要在各自 case 内数。 */

/** 步骤与 YAML 行一一对应的着色：同一条 case 用同一个色，方便左右对照 */
const STEP_TONES = [
  { cell: "bg-sky-50", line: "#7dd3fc" },
  { cell: "bg-violet-50", line: "#c4b5fd" },
  { cell: "bg-amber-50", line: "#fbbf24" },
  { cell: "bg-emerald-50", line: "#6ee7b7" },
  { cell: "bg-rose-50", line: "#fda4af" },
  { cell: "bg-cyan-50", line: "#67e8f9" },
  { cell: "bg-orange-50", line: "#fdba74" },
  { cell: "bg-lime-50", line: "#bef264" },
];

type YamlRow = {
  step?: Step;
  tone?: number;
  /** 这条 case 的下标：左侧用来画「一条 case 一个气泡」 */
  caseIndex?: number;
  /** 这一块里有 `- name:` 时，左侧显示同一条 case 名，左右才对得上 */
  caseName?: string;
  lines: { no: number; text: string }[];
};

/**
 * 把 YAML 按步骤切开，供左右两栏并排对照。
 * 不引 YAML 库：步骤清单已经由后端按 `- kind:` 的顺序给出，这里只按同一顺序找起始行，
 * 行数对不上（用户正在手改、格式不规范）就退回整块展示，不做猜测。
 */
function yamlRows(yaml: string, steps: Step[]): YamlRow[] {
  const lines = yaml.replace(/\n+$/, "").split("\n");
  const caseStarts: number[] = [];
  lines.forEach((line, i) => {
    if (/^\s*-\s+name\s*:/.test(line)) caseStarts.push(i);
  });
  const starts: number[] = [];
  let cur = 0;
  for (const s of steps) {
    const re = new RegExp(`^\\s*-\\s+${s.kind}\\s*:`);
    while (cur < lines.length && !re.test(lines[cur])) cur++;
    if (cur >= lines.length) break;
    starts.push(cur);
    cur++;
  }
  const all = () => [
    { lines: lines.map((text, i) => ({ no: i + 1, text })) },
  ];
  if (starts.length !== steps.length) return all();
  const rows: YamlRow[] = [];
  const push = (from: number, to: number, step?: Step, tone?: number) => {
    if (to > from) {
      const block = lines.slice(from, to);
      const nameLine = block.find((l) => /^\s*-\s+name\s*:/.test(l));
      rows.push({
        step,
        tone,
        caseName: nameLine
          ? nameLine
              .replace(/^\s*-\s+name\s*:\s*/, "")
              .replace(/^["']|["']$/g, "")
              .trim()
          : undefined,
        lines: block.map((text, k) => ({ no: from + k + 1, text })),
      });
    }
  };
  // 行边界 = 每条 case 的 `- name:` + 每一步的起始行：左侧的 case 名才会和
  // YAML 里那一行对齐，而不是挂在整段前置内容的顶上
  const bounds = [...new Set([0, ...caseStarts, ...starts, lines.length])].sort(
    (a, b) => a - b,
  );
  const stepAt = new Map(starts.map((start, i) => [start, i]));
  for (let k = 0; k < bounds.length - 1; k++) {
    const from = bounds[k];
    const to = bounds[k + 1];
    const si = stepAt.get(from);
    push(from, to, si === undefined ? undefined : steps[si], si);
  }
  // case 名那一行也归属到它下面的 case，气泡才不会断
  let caseNo = -1;
  for (const row of rows) {
    if (row.caseName) caseNo += 1;
    row.caseIndex = row.step?.case_index ?? (caseNo >= 0 ? caseNo : undefined);
  }
  return rows;
}

/** 缓存/深度定位只对 aiAct 生效；aiWaitFor 的 options 是另一套（超时等） */
const CACHE_KIND = "aiAct";
const TIMEOUT_KIND = "aiWaitFor";

type StepOption = {
  cache?: boolean;
  deep?: boolean;
  /** aiWaitFor 的 options.timeoutMs；空 = 不写这个键 */
  timeoutMs?: number | null;
  /** aiAct 的规划力度；balance 是框架默认，不写这个键 */
  effort?: "fast" | "balance" | "deepThink";
  /** 单步上下文：给这一步的额外事实/约束；空 = 删掉 */
  context?: string;
  /** aiAssert 的失败文案（步骤级键，不在 options 里） */
  message?: string;
};

const EFFORT_KINDS = new Set(["aiAct"]);
const CONTEXT_KINDS = new Set(["aiAct", "aiAssert", "aiWaitFor"]);
const MESSAGE_KINDS = new Set(["aiAssert"]);

/**
 * 这一步要写进 YAML 的东西：
 * - opts：options 下的键（value 为 null 表示删掉）
 * - stepKeys：与 prompt 同级的键（目前只有 aiAssert 的 message）
 */
function optionSpec(
  kind: string,
  o: StepOption,
): { opts: Record<string, string | null>; stepKeys: Record<string, string | null> } | null {
  const opts: Record<string, string | null> = {};
  const stepKeys: Record<string, string | null> = {};
  if (kind === CACHE_KIND) {
    if (o.cache !== undefined) opts.cacheable = o.cache ? "true" : null;
    if (o.deep !== undefined) opts.deepLocate = o.deep ? "true" : null;
  }
  if (kind === TIMEOUT_KIND && o.timeoutMs !== undefined) {
    opts.timeoutMs = o.timeoutMs == null ? null : String(o.timeoutMs);
  }
  if (EFFORT_KINDS.has(kind) && o.effort !== undefined) {
    // balance 是默认值：选它等于不要这个键
    opts.effort = o.effort === "balance" ? null : o.effort;
  }
  if (CONTEXT_KINDS.has(kind) && o.context !== undefined) {
    const text = o.context.trim();
    opts.context = text ? JSON.stringify(text) : null;
  }
  if (MESSAGE_KINDS.has(kind) && o.message !== undefined) {
    const text = o.message.trim();
    stepKeys.message = text ? JSON.stringify(text) : null;
  }
  if (!Object.keys(opts).length && !Object.keys(stepKeys).length) return null;
  return { opts, stepKeys };
}

/** 在一个步骤块里合并 options / 同级键：字符串简写会展开成块写法，空了整段删掉 */
function mergeBlockOptions(
  block: string[],
  spec: { opts: Record<string, string | null>; stepKeys: Record<string, string | null> },
) {
  const { opts, stepKeys } = spec;
  const first = block[0];
  const indent = (first.match(/^\s*/) as RegExpMatchArray)[0].length;
  const childIndent = " ".repeat(indent + 4);
  const shorthand = /^\s*-\s*\w+\s*:\s*\S/.test(first);
  const entries = Object.entries(opts).filter(([, v]) => v !== null);
  const stepEntries = Object.entries(stepKeys).filter(([, v]) => v !== null);

  if (shorthand) {
    const m = first.match(/^(\s*-\s*\w+\s*:)\s*(.*)$/);
    if (!m) return;
    if (!entries.length && !stepEntries.length) return; // 本身没有这些键，没什么可删
    const next = [`${m[1]}`, `${childIndent}prompt: ${m[2]}`];
    next.push(...stepEntries.map(([k, v]) => `${childIndent}${k}: ${v}`));
    if (entries.length) {
      next.push(
        `${childIndent}options:`,
        ...entries.map(([k, v]) => `${childIndent}  ${k}: ${v}`),
      );
    }
    block.splice(0, 1, ...next);
    return;
  }

  // 与 prompt 同级的键（aiAssert 的 message）
  for (const [key, value] of Object.entries(stepKeys)) {
    const exact = block.findIndex((l) =>
      new RegExp(`^\\s{${indent + 4}}${key}\\s*:`).test(l),
    );
    if (value === null) {
      if (exact >= 0) block.splice(exact, 1);
    } else if (exact >= 0) {
      block[exact] = `${childIndent}${key}: ${value}`;
    } else {
      const optIdx = block.findIndex((l) => /^\s+options\s*:\s*$/.test(l));
      block.splice(optIdx >= 0 ? optIdx : block.length, 0, `${childIndent}${key}: ${value}`);
    }
  }

  const oi = block.findIndex((l) => /^\s+options\s*:\s*$/.test(l));
  if (oi >= 0) {
    const oIndent = (block[oi].match(/^\s*/) as RegExpMatchArray)[0].length;
    let j = oi + 1;
    while (
      j < block.length &&
      (block[j].trim() === "" ||
        (block[j].match(/^\s*/) as RegExpMatchArray)[0].length > oIndent)
    ) {
      j++;
    }
    const children = block.slice(oi + 1, j);
    const childKeyIndent =
      (children.find((l) => l.trim())?.match(/^\s*/) as RegExpMatchArray | null)?.[0]
        .length ?? oIndent + 2;
    for (const [key, value] of Object.entries(opts)) {
      const idx = children.findIndex((l) => new RegExp(`^\\s*${key}\\s*:`).test(l));
      if (value === null) {
        if (idx >= 0) children.splice(idx, 1);
      } else if (idx >= 0) {
        children[idx] = `${" ".repeat(childKeyIndent)}${key}: ${value}`;
      } else {
        children.push(`${" ".repeat(childKeyIndent)}${key}: ${value}`);
      }
    }
    if (!children.some((l) => l.trim())) {
      block.splice(oi, j - oi); // 空了就别留一个光秃秃的 options:
    } else {
      block.splice(oi + 1, j - oi - 1, ...children);
    }
    return;
  }
  if (!entries.length) return;
  block.push(
    `${childIndent}options:`,
    ...entries.map(([k, v]) => `${childIndent}  ${k}: ${v}`),
  );
}

/**
 * 把单步选项实时画进 YAML 文本（右侧预览用）。
 * 真正执行时后端会用 YAML 解析器再写一次，这里只保证操作后立刻看得见结果。
 */
function previewStepOptions(
  yaml: string,
  steps: Step[],
  opts: Record<string, StepOption>,
): string {
  if (!Object.keys(opts).length) return yaml;
  const lines = yaml.replace(/\n+$/, "").split("\n");
  const starts: number[] = [];
  let cur = 0;
  for (const s of steps) {
    const re = new RegExp(`^\\s*-\\s+${s.kind}\\s*:`);
    while (cur < lines.length && !re.test(lines[cur])) cur++;
    if (cur >= lines.length) break;
    starts.push(cur);
    cur++;
  }
  if (starts.length !== steps.length) return yaml;
  const caseStarts = lines.flatMap((l, i) => (/^\s*-\s+name\s*:/.test(l) ? [i] : []));
  for (let i = starts.length - 1; i >= 0; i--) {
    const step = steps[i];
    const o = opts[`${step.case_index}:${step.index}`];
    if (!o) continue;
    const spec = optionSpec(step.kind, o);
    if (!spec) continue;
    const start = starts[i];
    const nextCase = caseStarts.find((c) => c > start) ?? lines.length;
    const end = Math.min(starts[i + 1] ?? lines.length, nextCase);
    const block = lines.slice(start, end);
    while (block.length > 1 && !block[block.length - 1].trim()) block.pop();
    mergeBlockOptions(block, spec);
    lines.splice(start, end - start, ...block);
  }
  return lines.join("\n");
}

/**
 * 步骤类型的标签与解释。名字和语义来自执行器 @midscene/core 的节点定义
 * （test-runner-nodes.mjs 的 method/description），不是自己编的。
 */
const NODE_LABELS: Record<string, [string, string]> = {
  aiAct: ["操作", "用自然语言描述目标，AI 自己决定并执行若干步"],
  aiTap: ["点击", "找到描述的元并点击一次"],
  aiInput: ["输入", "找到输入框并填入内容"],
  aiAssert: ["断言", "校验页面状态成立；不成立即判定这一步失败"],
  aiWaitFor: ["等待", "轮询页面，直到条件成立为止（可设超时）"],
  aiQuery: ["提取", "从当前页面提取结构化数据，不改变页面"],
  aiLocate: ["定位", "只定位元素并记录结果，不做任何操作"],
  aiScroll: ["滚动", "滚动页面或指定区域"],
  aiKeyboardPress: ["按键", "按下一个键或组合键"],
  aiLongPress: ["长按", "长按某个元素"],
  aiPinch: ["缩放", "双指放大或缩小"],
  aiDragAndDrop: ["拖拽", "把源元素拖到目标位置"],
  evaluateJavaScript: ["脚本", "执行一段 JavaScript 并记录返回值"],
  sleep: ["等待", "固定等待若干毫秒"],
  recordToReport: ["记录", "往测试报告里补文字或截图"],
  setAIContext: ["上下文", "设置后续调用共享的 AI 指导"],
  runGherkinScenario: ["场景", "执行一段 Gherkin 场景"],
  gotoUrl: ["打开", "打开指定地址"],
  callActionInActionSpace: ["动作", "调用平台或自定义动作"],
};

const emptyForm = {
  name: "",
  priority: "P1" as string | null,
  description: "" as string,
  yaml_content: DEFAULT_YAML,
  is_active: true,
};

export default function QaFeaturePage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { id, fid } = useParams();
  const systemId = Number(id);
  const [feature, setFeature] = useState<QaFeature | null>(null);
  const [cases, setCases] = useState<QaCase[]>([]);
  // undefined = 抽屉关闭；null = 新建；string = 编辑的用例 id
  const [editingId, setEditingId] = useState<string | null | undefined>(undefined);
  const form = useForm(emptyForm);
  const [errors, setErrors] = useState<string[]>([]);
  const [aiOpen, setAiOpen] = useState(false);
  /** 面板宽度：居中布局，拖右下角手柄时两边对称变宽 */
  const [aiWidth, setAiWidth] = useState(1100);
  /** 四块区域的尺寸：对话区高、左右分栏比例、输入框高（都可拖） */
  const [aiChatH, setAiChatH] = useState(220);
  const [aiSplit, setAiSplit] = useState(0.42);
  const [aiPromptH, setAiPromptH] = useState(96);
  const aiResize = useRef<{
    kind: "chat" | "split" | "prompt";
    x: number;
    y: number;
    v: number;
    /** split 用：滚动容器的左边界与宽度（grid 会被长行撑宽，不能拿它当基准） */
    left: number;
    w: number;
  } | null>(null);
  const startResize =
    (kind: "chat" | "split" | "prompt") => (e: ReactPointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const el = e.currentTarget as HTMLElement;
      el.setPointerCapture(e.pointerId);
      aiResize.current = {
        kind,
        x: e.clientX,
        y: e.clientY,
        v: kind === "split" ? aiSplit : kind === "chat" ? aiChatH : aiPromptH,
        ...(() => {
          const scroller = el.closest(".thin-scroll")?.getBoundingClientRect();
          return { left: scroller?.left ?? 0, w: scroller?.width ?? 800 };
        })(),
      };
    };
  const moveResize = (e: ReactPointerEvent) => {
    const d = aiResize.current;
    if (!d) return;
    if (d.kind === "split") {
      // 中间 28px 是连线通道，不算在两端比例里；基准用滚动容器，长行撑宽也不会失控
      const usable = Math.max(1, d.w - 28);
      setAiSplit(Math.min(0.72, Math.max(0.28, (e.clientX - d.left - 14) / usable)));
    } else if (d.kind === "chat") {
      setAiChatH(Math.min(420, Math.max(96, d.v + (e.clientY - d.y))));
    } else {
      setAiPromptH(Math.min(260, Math.max(64, d.v - (e.clientY - d.y))));
    }
  };
  const endResize = (e: ReactPointerEvent) => {
    aiResize.current = null;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  };
  const keyResize =
    (kind: "chat" | "split" | "prompt") => (e: ReactKeyboardEvent) => {
      const vertical = kind !== "split";
      const ok = vertical
        ? e.key === "ArrowUp" || e.key === "ArrowDown"
        : e.key === "ArrowLeft" || e.key === "ArrowRight";
      if (!ok) return;
      e.preventDefault();
      const down = e.key === "ArrowDown" || e.key === "ArrowRight";
      const delta = down ? 1 : -1;
      if (kind === "split") {
        setAiSplit((v) => Math.min(0.72, Math.max(0.28, v + delta * 0.03)));
      } else if (kind === "chat") {
        setAiChatH((v) => Math.min(420, Math.max(96, v + delta * 24)));
      } else {
        setAiPromptH((v) => Math.min(260, Math.max(64, v - delta * 24)));
      }
    };
  /** 单步执行选项：默认走缓存、不开深度定位；只在用户点过时记一条 */
  const [stepOpts, setStepOpts] = useState<Record<string, StepOption>>({});
  const stepOpt = (s: Step): StepOption => stepOpts[`${s.case_index}:${s.index}`] || {};
  /** 展开着参数面板的步骤（`case_index:index`） */
  const [openOpts, setOpenOpts] = useState<string | null>(null);
  const setStepField = (s: Step, key: "effort" | "context" | "message", value: string) => {
    const k = `${s.case_index}:${s.index}`;
    setStepOpts((prev) => ({ ...prev, [k]: { ...prev[k], [key]: value } }));
  };
  const setStepOpt = (s: Step, key: "cache" | "deep", value: boolean) => {
    const k = `${s.case_index}:${s.index}`;
    setStepOpts((prev) => ({ ...prev, [k]: { ...prev[k], [key]: value } }));
  };
  const setStepTimeout = (s: Step, raw: string) => {
    const k = `${s.case_index}:${s.index}`;
    const text = raw.trim();
    const value = text === "" ? null : Math.max(1000, Number(text) || 15000);
    setStepOpts((prev) => ({ ...prev, [k]: { ...prev[k], timeoutMs: value } }));
  };
  const aiDrag = useRef<{ x: number; w: number } | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiInput, setAiInput] = useState("");
  const [aiMessages, setAiMessages] = useState<
    { role: "user" | "assistant"; content: string }[]
  >([]);
  const [aiYaml, setAiYaml] = useState<string | null>(null);
  // 非空表示这次是「修改已有用例」：载入它的 YAML，采用后回到编辑抽屉覆盖保存
  const [aiCase, setAiCase] = useState<QaCase | null>(null);
  const [aiYamlErrors, setAiYamlErrors] = useState<string[]>([]);
  const [aiDiff, setAiDiff] = useState<string | null>(null);
  const [aiSteps, setAiSteps] = useState<Step[]>([]);
  const [aiRunId, setAiRunId] = useState<string | null>(null);
  const [trialBusy, setTrialBusy] = useState(false);
  const [trial, setTrial] = useState<any>(null);
  const trialTimer = useRef<number | null>(null);
  // null = 还在查；false = 系统没配模型，生成必然失败，先把话说在前面
  const [modelReady, setModelReady] = useState<boolean | null>(null);
  const aiScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    aiScrollRef.current?.scrollTo({ top: aiScrollRef.current.scrollHeight });
  }, [aiMessages]);
  // 关掉弹窗后别再轮询试跑结果
  useEffect(() => () => stopTrial(), []);
  const [versionFor, setVersionFor] = useState<QaCase | null>(null);
  const [versions, setVersions] = useState<QaCaseVersion[]>([]);
  const [running, setRunning] = useState(false);

  const load = async () => {
    try {
      const [f, cs] = await Promise.all([
        qa<QaFeature>(`/api/features/${fid}`),
        qa<QaCase[]>(`/api/features/${fid}/cases`),
      ]);
      setFeature(f);
      setCases(cs);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [fid]);

  const open = (c?: QaCase) => {
    setEditingId(c ? c.id : null);
    setErrors([]);
    form.reset(
      c
        ? {
            name: c.name,
            priority: c.priority,
            description: c.description ?? "",
            yaml_content: c.yaml_content,
            is_active: c.is_active,
          }
        : { ...emptyForm },
    );
  };

  const validate = async () => {
    try {
      const r = await qa<{ ok: boolean; errors: string[]; case_count: number }>(
        "/api/cases/validate",
        { method: "POST", body: JSON.stringify({ yaml_content: form.values.yaml_content || "" }) },
      );
      setErrors(r.errors);
      if (r.ok) toast.success(t("qa.case.valid", { n: r.case_count }));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const save = async () => {
    if (!form.validate({ name: { required: true, message: t("common.required") } })) return;
    const body = { ...form.values, description: form.values.description || null };
    try {
      if (editingId) {
        await qa(`/api/cases/${editingId}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        await qa(`/api/features/${fid}/cases`, { method: "POST", body: JSON.stringify(body) });
      }
      toast.success(t("qa.common.saved"));
      setEditingId(undefined);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (c: QaCase) => {
    const ok = await confirm({
      title: t("qa.case.deleteConfirm"),
      confirmText: t("qa.common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await qa(`/api/cases/${c.id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const openAi = async (target?: QaCase) => {
    setAiCase(target ?? null);
    setAiOpen(true);
    setAiMessages([]);
    setAiInput("");
    // 修改已有用例时把它的 YAML 作为 current_yaml 交出去，模型在原文上改
    setAiYaml(target?.yaml_content ?? null);
    setAiYamlErrors([]);
    setAiDiff(null);
    setAiSteps(target?.steps ?? []);
    setAiRunId(null);
    setTrial(null);
    setModelReady(null);
    try {
      const system = await qa<{ has_model_config: boolean }>(`/api/systems/${systemId}`);
      setModelReady(system.has_model_config);
    } catch {
      // 状态查不到不拦，真没配时发送会回同样的 400
      setModelReady(true);
    }
  };

  const aiSend = async (opts?: { mode?: "rewrite" | "append"; text?: string }) => {
    const text = (opts?.text ?? aiInput).trim();
    if (!text || aiBusy) return;
    const mode = opts?.mode || "rewrite";
    const history = [...aiMessages, { role: "user" as const, content: text }];
    setAiMessages([...history, { role: "assistant", content: "" }]);
    setAiInput("");
    setAiBusy(true);
    try {
      await qaStream(
        `/api/features/${fid}/ai/case-draft/stream`,
        {
          messages: history,
          current_yaml: aiYaml,
          mode,
          run_id: mode === "append" ? aiRunId : null,
        },
        (event, data) => {
          if (event === "delta") {
            setAiMessages((msgs) => {
              const next = [...msgs];
              const last = next[next.length - 1];
              next[next.length - 1] = { ...last, content: last.content + (data.text || "") };
              return next;
            });
          } else if (event === "done") {
            // yaml 为 null = 这轮没给 YAML，保留右边已有的
            if (data.yaml != null) {
              setAiYaml(data.yaml);
              setAiYamlErrors(data.yaml_errors || []);
              setAiSteps(Array.isArray(data.steps) ? data.steps : []);
              setAiDiff(data.diff || null);
            }
            if (data.lock_violated) toast.error(t("qa.case.aiLocked"));
            setAiMessages((msgs) => {
              const next = [...msgs];
              next[next.length - 1] = {
                role: "assistant",
                content: data.reply || next[next.length - 1].content,
              };
              return next;
            });
          } else if (event === "error") {
            toast.error(data.message || t("qa.case.aiNoYaml"));
            setAiMessages((msgs) => {
              const next = [...msgs];
              next[next.length - 1] = { role: "assistant", content: data.message || "" };
              return next;
            });
          }
        },
      );
    } catch (e: any) {
      toast.error(e.message);
      setAiMessages((msgs) => msgs.slice(0, -1));
    } finally {
      setAiBusy(false);
    }
  };

  const stopTrial = () => {
    if (trialTimer.current) window.clearInterval(trialTimer.current);
    trialTimer.current = null;
  };

  // 试跑到「第 caseIndex 条 case 的第 N 步」：case 顺序执行，
// 前面的整条跑完，后面的不跑（执行器就是这么跑的）
  const trialRun = async (limit: number, caseIndex: number) => {
    if (!aiYaml || trialBusy) return;
    setTrialBusy(true);
    setTrial({ status: "queued" });
    try {
      const r = await qa<{ run_id: string; steps: number }>(
        `/api/features/${fid}/ai/trial-run`,
        {
          method: "POST",
          body: JSON.stringify({
            yaml_content: aiYaml,
            step_limit: Math.max(1, limit),
            case_index: caseIndex,
            step_options: Object.entries(stepOpts).map(([k, v]) => {
              const [ci, st] = k.split(":").map(Number);
              return {
                case_index: ci,
                step: st,
                cache: v.cache,
                deep_locate: v.deep,
                timeout_ms: v.timeoutMs,
              };
            }),
          }),
        },
      );
      setAiRunId(r.run_id);
      stopTrial();
      trialTimer.current = window.setInterval(async () => {
        try {
          const run = await qa<any>(`/api/runs/${r.run_id}`);
          setTrial(run);
          if (["succeeded", "failed", "cancelled", "timed_out"].includes(run.status)) {
            stopTrial();
          }
        } catch {
          stopTrial();
        }
      }, 2000);
    } catch (e: any) {
      toast.error(e.message);
      setTrial(null);
    } finally {
      setTrialBusy(false);
    }
  };

  /** 试跑有没有失败：整体 success 也可能有用例/步骤失败 */
  const trialHasFailure = (run: any): boolean =>
    !!run &&
    ((run.failed ?? 0) > 0 ||
      ((run.step_results as any[] | undefined) ?? []).some(
        (r) => r.status && r.status !== "success",
      ));

  const trialText = (run: any): string => {
    if (!run) return "";
    if (["queued", "preparing", "running", "parsing"].includes(run.status)) {
      return t("qa.case.aiTrialRunning");
    }
    if (run.status === "succeeded") {
      // 执行器跑完就报 succeeded，用例本身可能还有失败步骤：以步骤结果为准
      const bad = ((run.step_results as any[] | undefined) ?? []).find(
        (r) => r.status && r.status !== "success",
      );
      if (!bad) {
        return trialHasFailure(run)
          ? t("qa.case.aiTrialFailed")
          : t("qa.case.aiTrialPassed");
      }
      const step = aiSteps.find(
        (x) => x.case_index === bad.case_index && x.index === bad.step,
      );
      return `${t("qa.case.aiTrialFailed")}${step ? `：${step.text}` : ""}${
        bad.reason ? ` — ${bad.reason}` : ""
      }`;
    }
    if (["cancelled", "timed_out"].includes(run.status)) {
      return t("qa.case.aiTrialInterrupted");
    }
    const step = (run.failure_steps || [])
      .flatMap((item: any) => item.steps || [])
      .find((s: any) => s.status === "failed" || s.reason);
    const detail = step ? `：${step.action || ""}${step.reason ? ` — ${step.reason}` : ""}` : "";
    return `${t("qa.case.aiTrialFailed")}${detail}`;
  };

  /** 最近一次试跑里这一步的结果；没跑到或没试跑就是 undefined */
  const stepResult = (s: Step): { status?: string; reason?: string } | undefined =>
    (trial?.step_results as any[] | undefined)?.find(
      (r) => r.case_index === s.case_index && r.step === s.index,
    );

  const aiContinue = () => {
    if (aiBusy) return;
    aiSend({ mode: "append", text: t("qa.case.aiContinuePrompt") });
  };

  const aiAdopt = () => {
    if (!aiYaml) return;
    setAiOpen(false);
    setErrors(aiYamlErrors);
    if (aiCase) {
      // 修改已有用例：回到编辑抽屉，保存时 PATCH 这一条
      setEditingId(aiCase.id);
      form.reset({
        name: aiCase.name,
        priority: aiCase.priority,
        description: aiCase.description ?? "",
        yaml_content: aiYaml,
        is_active: aiCase.is_active,
      });
      return;
    }
    setEditingId(null);
    form.reset({
      ...emptyForm,
      name: aiMessages.find((m) => m.role === "user")?.content.slice(0, 40) || feature?.name || "",
      yaml_content: aiYaml,
    });
  };

  const openVersions = async (c: QaCase) => {
    setVersionFor(c);
    try {
      setVersions(await qa<QaCaseVersion[]>(`/api/cases/${c.id}/versions`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const rollback = async (c: QaCase, v: QaCaseVersion) => {
    const ok = await confirm({
      title: t("qa.case.rollbackConfirm", { n: v.version_no }),
      confirmText: t("qa.case.rollback"),
    });
    if (!ok) return;
    try {
      await qa(`/api/cases/${c.id}/versions/${v.id}/rollback`, { method: "POST" });
      toast.success(t("qa.case.rolledBack", { n: v.version_no }));
      setVersionFor(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const runFeature = async () => {
    if (!feature) return;
    const ok = await confirm({
      title: t("qa.feature.runConfirm"),
      confirmText: t("qa.feature.run"),
    });
    if (!ok) return;
    setRunning(true);
    try {
      await qa(`/api/systems/${systemId}/tasks`, {
        method: "POST",
        body: JSON.stringify({
          name: feature.name,
          type: "feature_regression",
          feature_ids: [feature.id],
          run_now: true,
        }),
      });
      toast.success(t("qa.feature.started"));
      nav(`/systems/${systemId}/qa?tab=batches`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setRunning(false);
    }
  };

  const columns: DataTableColumn<QaCase>[] = [
    {
      title: t("qa.common.name"),
      dataIndex: "name",
      render: (v, r) => (
        <button
          type="button"
          onClick={() => open(r)}
          className="w-full truncate text-left text-primary hover:underline"
        >
          {v}
        </button>
      ),
    },
    {
      title: t("qa.case.priority"),
      dataIndex: "priority",
      width: 90,
      render: (v) => (v ? <Badge>{v}</Badge> : "—"),
    },
    {
      title: t("qa.case.active"),
      dataIndex: "is_active",
      width: 80,
      render: (v) =>
        v ? <Badge tone="success">{t("qa.common.on")}</Badge> : <Badge>{t("qa.common.off")}</Badge>,
    },
    {
      title: t("qa.case.version"),
      dataIndex: "version_no",
      width: 70,
      render: (v) => (v ? `v${v}` : "—"),
    },
    {
      title: t("qa.common.actions"),
      width: 260,
      render: (_, r) => (
        <div className="flex items-center gap-2 text-[12px]">
          <button type="button" onClick={() => open(r)} className="text-primary hover:underline">
            {t("qa.common.edit")}
          </button>
          <button
            type="button"
            onClick={() => openAi(r)}
            className="text-primary hover:underline"
          >
            {t("qa.case.aiModify")}
          </button>
          <button
            type="button"
            onClick={() => openVersions(r)}
            className="text-primary hover:underline"
          >
            {t("qa.case.versions")}
          </button>
          <button
            type="button"
            onClick={() => remove(r)}
            className="text-destructive hover:underline"
          >
            {t("qa.common.delete")}
          </button>
        </div>
      ),
    },
  ];

  const versionCols: TableColumn<QaCaseVersion>[] = [
    { key: "version_no", header: "#", width: "60px", cell: (r) => `v${r.version_no}` },
    {
      key: "name",
      header: t("qa.common.name"),
      cell: (r) => <span className="block truncate">{r.name}</span>,
    },
    { key: "change_source", header: t("qa.case.changeSource"), width: "110px" },
    {
      key: "created_at",
      header: t("qa.batch.created"),
      width: "170px",
      cell: (r) => (
        <span className="font-mono text-[12px]">
          {(r.created_at || "").slice(0, 19).replace("T", " ")}
        </span>
      ),
    },
    {
      key: "rollback",
      header: "",
      width: "70px",
      cell: (r) => (
        <button
          type="button"
          onClick={() => versionFor && rollback(versionFor, r)}
          className="text-[12px] text-primary hover:underline"
        >
          {t("qa.case.rollback")}
        </button>
      ),
    },
  ];


  return (
    <Page
      title={`${t("qa.feature.cases")} · ${feature?.name || ""}`}
      desc={feature?.description || undefined}
      actions={
        <>
          <Link to={`/systems/${systemId}/qa`} className="text-[13px] text-primary hover:underline">
            {t("qa.backFeatures")}
          </Link>
          <Button size="sm" className="rounded-lg" onClick={() => open()}>
            <Plus className="size-4" />
            {t("qa.case.new")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={() => openAi()}>
            <Sparkles className="size-4" />
            {t("qa.case.aiGen")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={runFeature} disabled={running}>
            <Zap className="size-4" />
            {t("qa.feature.run")}
          </Button>
        </>
      }
    >
      <div className="mb-3">
        <Button variant="outline" size="sm" className="rounded-lg" onClick={load}>
          <RefreshCw className="size-4" />
          {t("qa.common.refresh")}
        </Button>
      </div>
      <DataTable<QaCase> rowKey="id" dataSource={cases} columns={columns} emptyText={t("common.noData")} />

      <Drawer
        open={editingId !== undefined}
        onOpenChange={(next) => {
          if (!next) setEditingId(undefined);
        }}
        ariaLabel={editingId ? t("qa.case.editTitle") : t("qa.case.new")}
        className="w-[min(900px,96vw)] max-w-[96vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {editingId ? t("qa.case.editTitle") : t("qa.case.new")}
            </h3>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="outline" size="sm" className="rounded-lg" onClick={validate}>
                {t("qa.case.validate")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={save}>
                {t("common.save")}
              </Button>
              <button
                type="button"
                aria-label={t("common.close")}
                onClick={() => setEditingId(undefined)}
                className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_110px_auto] sm:items-end">
              <Input
                label={t("qa.common.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                maxLength={128}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("qa.case.priority")}>
                <SearchSelect
                  value={form.values.priority}
                  onChange={(v) => form.set("priority", v == null ? null : String(v))}
                  options={["P0", "P1", "P2", "P3"].map((p) => ({ value: p, label: p }))}
                />
              </Field>
              <Field label={t("qa.case.active")}>
                <span className="flex h-9 items-center">
                  <Switch
                    checked={!!form.values.is_active}
                    onCheckedChange={(v) => form.set("is_active", v)}
                  />
                </span>
              </Field>
            </div>
            <Input
              label={t("qa.common.description")}
              value={form.values.description}
              onChange={(v) => form.set("description", v)}
              classNames={{ field: "rounded-lg" }}
              className="mt-4"
            />
            <Textarea
              label="YAML"
              rows={20}
              spellCheck={false}
              value={form.values.yaml_content}
              onChange={(v) => form.set("yaml_content", v)}
              className="mt-4 [&_textarea]:font-mono [&_textarea]:text-[12px]"
            />
            {errors.length > 0 && (
              <div className="mt-3 rounded-lg bg-danger/10 px-3 py-2 text-[13px] text-danger">
                <p className="m-0 font-medium">{t("qa.case.yamlErrors")}</p>
                <ul className="m-0 mt-1 list-disc pl-5">
                  {errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </Drawer>

      <CenterMorphModal
        open={aiOpen}
        onOpenChange={(next) => {
          if (!next) setAiOpen(false);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={aiCase ? t("qa.case.aiModify") : t("qa.case.aiGen")}
                    className="max-w-[94vw] min-w-[380px]"
          style={{ width: aiWidth }}
        >
          <div className="flex h-[76vh] max-h-[76vh] flex-col p-6">
            {/* 宽度手柄：面板是居中的，往右拖 dx 时宽度 +2dx，右边缘正好跟住鼠标 */}
            <span
              role="separator"
              aria-orientation="vertical"
              aria-label={t("qa.case.aiResize")}
              tabIndex={0}
              className="absolute bottom-1.5 right-1.5 z-20 size-3.5 cursor-nwse-resize touch-none rounded-sm bg-foreground/20 transition-colors hover:bg-foreground/40"
              title={t("qa.case.aiResize")}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.currentTarget.setPointerCapture(e.pointerId);
                aiDrag.current = { x: e.clientX, w: aiWidth };
              }}
              onPointerMove={(e) => {
                if (!aiDrag.current) return;
                const next = aiDrag.current.w + (e.clientX - aiDrag.current.x) * 2;
                setAiWidth(Math.min(1400, Math.max(380, next)));
              }}
              onPointerUp={(e) => {
                aiDrag.current = null;
                e.currentTarget.releasePointerCapture(e.pointerId);
              }}
              onKeyDown={(e) => {
                // 键盘用户同样能调宽度：每次 40px
                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                e.preventDefault();
                const step = e.key === "ArrowRight" ? 40 : -40;
                setAiWidth((w) => Math.min(1400, Math.max(380, w + step)));
              }}
            />
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {aiCase ? t("qa.case.aiModify") : t("qa.case.aiGen")}
            </h2>
            {modelReady === false ? (
              <div className="mt-4 rounded-lg bg-warning/10 px-3 py-3 text-[13px] leading-relaxed text-foreground">
                <p className="m-0">{t("qa.case.aiModelMissing")}</p>
                <Link
                  to={`/systems/${systemId}/qa?tab=config`}
                  onClick={() => setAiOpen(false)}
                  className="mt-2 inline-block font-medium text-primary hover:underline"
                >
                  {t("qa.case.aiGoConfig")}
                </Link>
              </div>
            ) : (
              <>
                <div
                  ref={aiScrollRef}
                  className="thin-scroll mt-4 flex shrink-0 flex-col gap-2 overflow-y-auto"
                  style={{ height: aiChatH }}
                >
                  {aiMessages.length === 0 ? (
                    <p className="m-0 text-[13px] text-muted-foreground">
                      {t("qa.case.aiPrompt")}
                    </p>
                  ) : (
                    aiMessages.map((m, i) => (
                      <div
                        key={i}
                        className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
                      >
                        <div
                          className={`max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-[13px] leading-relaxed text-foreground ${
                            m.role === "user" ? "bg-primary/10" : "border border-border bg-card"
                          }`}
                        >
                          {m.content ||
                            (aiBusy && i === aiMessages.length - 1 ? "…" : "")}
                        </div>
                      </div>
                    ))
                  )}
                </div>
                {/* 对话区高度 */}
                <div
                  role="separator"
                  aria-orientation="horizontal"
                  aria-label={t("qa.case.aiResizeChat")}
                  tabIndex={0}
                  className="mt-2 h-1.5 shrink-0 cursor-row-resize touch-none rounded bg-foreground/10 transition-colors hover:bg-foreground/25"
                  onPointerDown={startResize("chat")}
                  onPointerMove={moveResize}
                  onPointerUp={endResize}
                  onKeyDown={keyResize("chat")}
                />
                {aiYaml ? (
                  <div className="mt-2 flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border">
                    <div className="thin-scroll min-h-0 flex-1 overflow-auto">
                      <div
                        className="grid"
                        style={{
                          gridTemplateColumns: `minmax(0,${aiSplit}fr) 28px minmax(0,${1 - aiSplit}fr)`,
                        }}
                      >
                        {/* 表头：和内容同一个 grid，列宽永远不会和下面错位 */}
                        <div className="sticky left-0 top-0 z-30 flex items-center gap-2 border-b border-border bg-muted/50 px-3 py-1.5">
                          <span className="shrink-0 text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                            {t("qa.case.aiSteps")}
                          </span>
                          {trial ? (
                            <span
                              className={`truncate text-[12px] ${
                                trial.status === "succeeded" && !trialHasFailure(trial)
                                  ? "text-success"
                                  : ["queued", "preparing", "running", "parsing"].includes(
                                        trial.status,
                                      )
                                    ? "text-muted-foreground"
                                    : "text-danger"
                              }`}
                              title={trialText(trial)}
                            >
                              {trialText(trial)}
                            </span>
                          ) : null}
                        </div>
                        <div className="sticky top-0 z-30 flex items-center justify-center border-b border-border bg-muted/50">
                          {/* 分栏手柄：就压在两栏的分界线上，拖动改左右比例 */}
                          <span
                            role="separator"
                            aria-orientation="vertical"
                            aria-label={t("qa.case.aiResizeSplit")}
                            tabIndex={0}
                            className="h-4 w-2 cursor-col-resize touch-none rounded-full bg-foreground/25 transition-colors hover:bg-foreground/45"
                            onPointerDown={startResize("split")}
                            onPointerMove={moveResize}
                            onPointerUp={endResize}
                            onKeyDown={keyResize("split")}
                          />
                        </div>
                        <div className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-border bg-muted/50 px-3 py-1.5">
                          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                            YAML
                          </span>
                          <div className="flex items-center gap-3">
                            {aiRunId ? (
                              <button
                                type="button"
                                onClick={aiContinue}
                                disabled={aiBusy}
                                className="text-[12px] font-medium text-primary hover:underline disabled:opacity-50"
                              >
                                {t("qa.case.aiContinue")}
                              </button>
                            ) : null}
                            <button
                              type="button"
                              onClick={aiAdopt}
                              className="text-[12px] font-medium text-primary hover:underline"
                            >
                              {t("qa.case.aiAdopt")}
                            </button>
                          </div>
                        </div>
                        {(() => {
                          const rows = yamlRows(
                            previewStepOptions(aiYaml, aiSteps, stepOpts),
                            aiSteps,
                          );
                          return rows.flatMap((row, ri) => {
                            const step = row.step;
                            // 每一步换一个浅色，颜色只用来左右对照，不代表状态
                            const tone =
                              step && row.tone != null
                                ? STEP_TONES[row.tone % STEP_TONES.length]
                                : null;
                            const cacheIdx = row.lines.findIndex((x) =>
                              /^\s*cacheable\s*:/.test(x.text),
                            );
                            const deepIdx = row.lines.findIndex((x) =>
                              /^\s*deepLocate\s*:/.test(x.text),
                            );
                            const cacheOn =
                              cacheIdx >= 0 &&
                              /^\s*cacheable\s*:\s*true/.test(row.lines[cacheIdx].text);
                            const deepOn =
                              deepIdx >= 0 &&
                              /^\s*deepLocate\s*:\s*true/.test(row.lines[deepIdx].text);
                            // 缓存/深度定位只对 aiAct；aiWaitFor 用超时输入
                            const canOpt = !!step && step.kind === CACHE_KIND;
                            const canTimeout = !!step && step.kind === TIMEOUT_KIND;
                            const opt = step ? stepOpt(step) : {};
                            // 一条 case 一个气泡：首尾行圆角，其余行连成一块
                            const inCase = row.caseIndex !== undefined;
                            // 气泡上沿画在「带 case 名那一块」的第一行、下沿画在最后一行：
                            // 每个 YAML 行都单独渲染一组格子，整块都加圆角就会冒出第二个
                            const caseStartsRow = inCase && !!row.caseName;
                            const caseEndsRow =
                              inCase &&
                              (ri === rows.length - 1 ||
                                rows[ri + 1].caseIndex !== row.caseIndex);
                            const blockH = row.lines.length * 20;
                            const iconBtn = (key: "cache" | "deep", on: boolean) => (
                              <button
                                type="button"
                                aria-pressed={on}
                                title={
                                  key === "cache"
                                    ? t("qa.case.aiCacheTip")
                                    : t("qa.case.aiDeepLocateTip")
                                }
                                onClick={() => step && setStepOpt(step, key, !on)}
                                className={`shrink-0 rounded p-0.5 transition-colors ${
                                  on
                                    ? "bg-primary/10 text-primary"
                                    : "text-muted-foreground/40 hover:text-muted-foreground"
                                }`}
                              >
                                {key === "cache" ? (
                                  <Database className="size-3.5" />
                                ) : (
                                  <Focus className="size-3.5" />
                                )}
                              </button>
                            );
                            const stepKey = step ? `${step.case_index}:${step.index}` : "";
                            const cells = row.lines.map((l, li) => {
                              const first = li === 0;
                              const last = li === row.lines.length - 1;
                              const caseStart = caseStartsRow && first;
                              const caseEnd = caseEndsRow && last;
                              const res = step && first ? stepResult(step) : undefined;
                              const ok = res?.status === "success";
                              return (
                                <Fragment key={l.no}>
                                  <div
                                    className={`sticky left-0 z-10 ${
                                      row.caseName && first ? "h-7" : "h-5"
                                    } pl-2 ${
                                      inCase ? "bg-[var(--accent-soft)]" : "bg-card"
                                    } ${inCase ? "border-x border-border" : ""} ${
                                      caseStart ? "rounded-t-lg border-t" : ""
                                    } ${caseEnd ? "rounded-b-lg border-b" : ""}`}
                                  >
                                    {row.caseName && first ? (
                                      <div
                                        className="flex h-7 items-center gap-2 px-2"
                                        title={t("qa.case.aiCaseTitle", {
                                          n: (row.caseIndex ?? 0) + 1,
                                          name: row.caseName,
                                        })}
                                      >
                                        <span className="shrink-0 rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-primary">
                                          {t("qa.case.aiCaseBadge", { n: (row.caseIndex ?? 0) + 1 })}
                                        </span>
                                        <span className="min-w-0 truncate text-[13.5px] font-semibold tracking-[-0.01em] text-foreground">
                                          {row.caseName}
                                        </span>
                                      </div>
                                    ) : null}
                                    {step ? (
                                      <div
                                        // 气泡里不再给每一步单独圆角，否则看起来像套了一层
                                        className={`flex h-5 w-full min-w-0 items-center gap-2 px-2 ${
                                          tone?.cell || ""
                                        }`}
                                      >
                                        {first ? (
                                          <>
                                            {canOpt || canTimeout || step.kind === "aiAssert" ? (
                                              <button
                                                type="button"
                                                title="这一步的参数：规划力度 / 失败文案 / 上下文"
                                                aria-expanded={openOpts === stepKey}
                                                onClick={() =>
                                                  setOpenOpts((cur) =>
                                                    cur === stepKey ? null : stepKey,
                                                  )
                                                }
                                                className={`shrink-0 rounded p-0.5 transition-colors ${
                                                  openOpts === stepKey
                                                    ? "bg-primary/10 text-primary"
                                                    : "text-muted-foreground/40 hover:text-muted-foreground"
                                                }`}
                                              >
                                                <SlidersHorizontal className="size-3.5" />
                                              </button>
                                            ) : null}
                                            {NODE_LABELS[step.kind] ? (
                                              <span
                                                className="shrink-0 rounded bg-foreground/[0.06] px-1 text-[10px] leading-4 text-muted-foreground"
                                                title={`${step.kind} · ${NODE_LABELS[step.kind][1]}`}
                                              >
                                                {NODE_LABELS[step.kind][0]}
                                              </span>
                                            ) : null}
                                            <span
                                              className="min-w-0 flex-1 truncate text-[12px]"
                                              title={step.text}
                                            >
                                              {step.text || step.kind}
                                            </span>
                                            {res ? (
                                              <span
                                                className={`shrink-0 text-[11px] font-semibold ${
                                                  ok ? "text-success" : "text-danger"
                                                }`}
                                                title={
                                                  res.reason ||
                                                  (ok
                                                    ? t("qa.case.aiTrialPassed")
                                                    : t("qa.case.aiTrialFailed"))
                                                }
                                              >
                                                {ok ? "✓" : "✗"}
                                              </span>
                                            ) : null}
                                            {canTimeout ? (
                                              <input
                                                type="number"
                                                min={1000}
                                                step={1000}
                                                placeholder="15000"
                                                value={opt.timeoutMs ?? ""}
                                                onChange={(e) =>
                                                  setStepTimeout(step, e.target.value)
                                                }
                                                title={t("qa.case.aiTimeoutTip")}
                                                className="h-4 w-14 shrink-0 rounded border border-border bg-card px-1 text-right font-mono text-[10px] outline-none focus-visible:border-primary"
                                              />
                                            ) : null}
                                            {/* 图标固定在这一行，靠曲线连到对应的 key 行：
                                                左（深度）连下面那行、右（缓存）连上面那行，两条线就不会交叉 */}
                                            {canOpt ? iconBtn("deep", deepOn) : null}

                                            {canOpt ? iconBtn("cache", cacheOn) : null}
                                            <button
                                              type="button"
                                              onClick={() =>
                                                trialRun(step.index + 1, step.case_index)
                                              }
                                              disabled={trialBusy}
                                              title={t("qa.case.aiTrialHere")}
                                              className="grid size-5 shrink-0 place-items-center rounded text-primary hover:bg-primary/10 disabled:opacity-50"
                                            >
                                              <Play className="size-3.5" />
                                            </button>
                                            <span
                                              className="grid size-5 shrink-0 place-items-center rounded-full text-[10px] font-semibold text-foreground"
                                              style={{ background: tone?.line }}
                                              title={t("qa.case.aiStepNo", { n: step.index + 1 })}
                                            >
                                              {step.index + 1}
                                            </span>
                                          </>
                                        ) : null}
                                      </div>
                                    ) : null}
                                  </div>
                                  <div className="relative z-20 flex h-5 items-center">
                                    {step && first ? (
                                      <>
                                        {/* 到步骤自身那一行：水平短线 */}
                                        <span
                                          className="h-px w-full"
                                          style={{ background: tone?.line }}
                                        />
                                        {/* 到 cacheable / deepLocate 行：曲线 */}
                                        {canOpt && blockH > 20 ? (
                                          <svg
                                            className="pointer-events-none absolute left-0 top-0 overflow-visible"
                                            width={28}
                                            height={blockH}
                                            aria-hidden
                                          >
                                            {/* 各自从自己的 icon 正下方出发 */}
                                            {cacheIdx >= 0 ? (
                                              <path
                                                d={`M-73,20 C-73,${
                                                  cacheIdx * 20 + 4
                                                } -30,${cacheIdx * 20 + 10} 28,${
                                                  cacheIdx * 20 + 10
                                                }`}
                                                fill="none"
                                                stroke={tone?.line}
                                                strokeWidth={1}
                                              />
                                            ) : null}
                                            {deepIdx >= 0 ? (
                                              <path
                                                d={`M-99,20 C-99,${
                                                  deepIdx * 20 + 4
                                                } -40,${deepIdx * 20 + 10} 28,${
                                                  deepIdx * 20 + 10
                                                }`}
                                                fill="none"
                                                stroke={tone?.line}
                                                strokeWidth={1}
                                              />
                                            ) : null}
                                          </svg>
                                        ) : null}
                                      </>
                                    ) : null}
                                  </div>
                                  <div
                                    className={`flex h-5 items-center gap-2 px-2 font-mono text-[11.5px] ${
                                      tone ? tone.cell : ""
                                    } ${tone && first ? "rounded-t-md" : ""} ${
                                      tone && last ? "rounded-b-md" : ""
                                    }`}
                                  >
                                    <span className="w-6 shrink-0 select-none text-right text-muted-foreground/60">
                                      {l.no}
                                    </span>
                                    <span className="whitespace-pre">{l.text}</span>
                                  </div>
                                </Fragment>
                              );
                            });
                            if (step && openOpts === stepKey) {
                              cells.push(
                                <div
                                  key={`opt-${stepKey}`}
                                  style={{ gridColumn: "1 / 2" }}
                                  className="flex flex-wrap items-center gap-2 border-y border-border bg-card px-3 py-2 text-[11px]"
                                >
                                  {step.kind === "aiAct" ? (
                                    <span className="flex items-center gap-1.5 text-muted-foreground">
                                      规划力度
                                      <span className="flex overflow-hidden rounded border border-border">
                                        {(
                                          [
                                            ["fast", "快速", "最省时间，适合简单页面"],
                                            ["balance", "平衡", "默认：速度与准确度折中"],
                                            ["deepThink", "深想", "多花 token，复杂步骤更稳"],
                                          ] as const
                                        ).map(([value, label, hint]) => (
                                          <button
                                            key={value}
                                            type="button"
                                            title={hint}
                                            onClick={() => setStepField(step, "effort", value)}
                                            className={`px-1.5 py-0.5 transition-colors ${
                                              (opt.effort ?? "balance") === value
                                                ? "bg-primary/10 text-primary"
                                                : "text-muted-foreground hover:bg-muted/60"
                                            }`}
                                          >
                                            {label}
                                          </button>
                                        ))}
                                      </span>
                                    </span>
                                  ) : null}
                                  {step.kind === "aiAssert" ? (
                                    <input
                                      value={opt.message ?? ""}
                                      onChange={(e) =>
                                        setStepField(step, "message", e.target.value)
                                      }
                                      placeholder="失败文案：断言不成立时，报告里显示这句话"
                                      className="min-w-52 flex-1 rounded border border-border bg-background px-2 py-1 outline-none focus-visible:border-primary"
                                    />
                                  ) : null}
                                  <input
                                    value={opt.context ?? ""}
                                    onChange={(e) =>
                                      setStepField(step, "context", e.target.value)
                                    }
                                    placeholder="上下文：给这一步的额外事实或约束（例如「按钮在右侧抽屉底部」）"
                                    className="min-w-64 flex-[2] rounded border border-border bg-background px-2 py-1 outline-none focus-visible:border-primary"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => setOpenOpts(null)}
                                    className="ml-auto shrink-0 text-muted-foreground hover:text-foreground"
                                  >
                                    收起
                                  </button>
                                </div>,
                                <div key={`opt-gap-a-${stepKey}`} />,
                                <div key={`opt-gap-b-${stepKey}`} />,
                              );
                            }
                            return cells;
                          });
                        })()}
                      </div>
                    </div>
                    {aiDiff ? (
                      <details className="border-t border-border">
                        <summary className="cursor-pointer px-3 py-1.5 text-[12px] text-muted-foreground">
                          {t("qa.case.aiDiff")}
                        </summary>
                        <pre className="m-0 max-h-40 overflow-auto border-t border-border p-3 font-mono text-[11px] leading-relaxed">
                          {aiDiff}
                        </pre>
                      </details>
                    ) : null}
                    {aiYamlErrors.length > 0 && (
                      <p className="m-0 border-t border-border px-3 py-2 text-[12px] text-danger">
                        {aiYamlErrors.join("；")}
                      </p>
                    )}
                  </div>
                ) : null}
                {/* 输入框高度 */}
                <div
                  role="separator"
                  aria-orientation="horizontal"
                  aria-label={t("qa.case.aiResizePrompt")}
                  tabIndex={0}
                  className="mt-2 h-1.5 shrink-0 cursor-row-resize touch-none rounded bg-foreground/10 transition-colors hover:bg-foreground/25"
                  onPointerDown={startResize("prompt")}
                  onPointerMove={moveResize}
                  onPointerUp={endResize}
                  onKeyDown={keyResize("prompt")}
                />
                <div className="shrink-0" style={{ height: aiPromptH }}>
                  <Textarea
                    className="h-full [&>textarea]:h-full [&>textarea]:resize-none"
                    rows={3}
                    value={aiInput}
                    onChange={setAiInput}
                    placeholder={t("qa.case.aiPrompt")}
                  />
                </div>
                <div className="mt-4 flex justify-end gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="rounded-lg"
                    onClick={() => setAiOpen(false)}
                  >
                    {t("common.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    className="rounded-lg"
                    onClick={() => aiSend()}
                    disabled={aiBusy || !aiInput.trim()}
                  >
                    {aiBusy ? t("qa.case.aiSending") : t("qa.case.aiSend")}
                  </Button>
                </div>
              </>
            )}
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>

      <Drawer
        open={!!versionFor}
        onOpenChange={(next) => {
          if (!next) setVersionFor(null);
        }}
        ariaLabel={`${t("qa.case.versions")} · ${versionFor?.name || ""}`}
        className="w-[min(680px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("qa.case.versions")} · {versionFor?.name || ""}
            </h3>
            <button
              type="button"
              aria-label={t("common.close")}
              onClick={() => setVersionFor(null)}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <MiniTable
              data={versions}
              columns={versionCols}
              rowKey="id"
              rowHeight={40}
              emptyText={t("common.noData")}
            />
          </div>
        </div>
      </Drawer>
    </Page>
  );
}