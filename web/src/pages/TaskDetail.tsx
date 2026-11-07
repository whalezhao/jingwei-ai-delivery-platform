import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bot, Check, ChevronRight, Lightbulb, LoaderCircle, X } from "lucide-react";
import { Button } from "../components/motion/button/index";
import Badge from "../ui/Badge";
import { Chain, type ChainItem } from "../ui/Chain";
import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import { rawNode } from "../eventNodes";
import { api } from "../api";
import i18n, { type MsgKey } from "../i18n";
import Page from "../Page";
import DocPreview from "../components/DocPreview";
import { STATUS, type TaskEvent } from "./Tasks";
import {
  actionRow,
  chainExpandSetter,
  chainExpandedKeys,
  openStream,
  thinkBlock,
  toolArg,
  toolMeta,
} from "./RequirementChat";

/**
 * 任务详情页：定稿需求 + 定稿方案 + AI 实时工作进展。
 * 进展 = 本地 Chain 思考链，按事件的 parent 字段分「主 Agent / 子 Agent」泳道
 * （子 agent 由主 agent 在执行期用 Task 工具自开，parent 即派生它的 tool_use id）。
 * 数据 = SSE 实时推（按本任务的 runner 任务号过滤 dev_raw）+ 3s 轮询兜底对账。
 */

const LIVE_STATUSES = ["queued", "running", "pushed"];

interface Detail {
  task: any;
  requirement: any;
  project: any;
  final_doc: { id: number; filename: string; version: number } | null;
  final_plan: { version: number; content: string } | null;
  tasks: any[];
  runner_task_ids: number[];
}

const KIND_TITLE: Record<string, MsgKey> = {
  text: "taskDetail.kindOutput",
  result: "taskDetail.kindResult",
};

function evtBody(e: TaskEvent & { parent?: string }): string {
  if (e.kind === "tool_use") return e.input || "";
  if (e.kind === "tool_result" || e.kind === "result") return e.content || e.text || "";
  return e.text || "";
}

/** 泳道事件 → 思考链节点（与需求对话页同款）：
 *  每次思考是可折叠链节点；tool_use/tool_result 就地配对，按工具类型出动作行
 *  （命令/文件/搜索…）；原生 status（loading 转圈 / success ✓ / error ✗ / abort ⊖），
 *  思考结束回落灯泡图标；任务失败时遗留 loading 标 abort。 */
function laneItems(
  evts: (TaskEvent & { parent?: string })[],
  laneLive: boolean,
  failed: boolean,
): ChainItem[] {
  type ChainNode = ChainItem & { kind: "think" | "tool" | "raw"; toolName?: string; arg?: string };
  const items: ChainNode[] = [];
  for (const e of evts) {
    if (e.kind === "thinking") {
      items.push({
        kind: "think",
        key: `k${e.seq}`,
        icon: <Lightbulb size={14} />,
        title: i18n.t("taskDetail.thinking"),
        collapsible: true,
        content: thinkBlock(e.text || ""),
      });
    } else if (e.kind === "tool_use") {
      const m = toolMeta(e.name);
      const arg = toolArg(e.name, e.input || "");
      items.push({
        kind: "tool",
        key: `t${e.seq}`,
        title: m.label,
        status: "loading",
        collapsible: true,
        toolName: e.name,
        arg,
        content: actionRow(m.icon, arg || "…"),
      });
    } else if (e.kind === "tool_result") {
      const it = [...items].reverse().find((n) => n.kind === "tool" && n.status === "loading");
      if (it) {
        const m = toolMeta(it.toolName);
        const line = (e.content || "").split("\n")[0].slice(0, 100);
        it.status = e.is_error ? "error" : "success";
        it.content = (
          <>
            {actionRow(m.icon, it.arg || "…")}
            {actionRow(
              e.is_error ? <X size={14} /> : <Check size={14} />,
              line || i18n.t("events.noOutput"),
              e.is_error ? "err" : "ok",
              e.is_error ? i18n.t("taskDetail.execError") : i18n.t("taskDetail.done"),
            )}
            {e.is_error && <pre className="tc-body tc-err">{e.content}</pre>}
          </>
        );
      } else {
        items.push({
          kind: "tool",
          key: `k${e.seq}`,
          title: i18n.t("taskDetail.toolResult"),
          status: e.is_error ? "error" : "success",
          collapsible: true,
          content: <pre className={`tc-body${e.is_error ? " tc-err" : ""}`}>{e.content}</pre>,
        });
      }
    } else if (e.kind === "raw") {
      // 原始事件统一交给 eventNodes：认得出的给一行可读节点，认不出的才折叠原始 JSON
      const node = rawNode((e as any).event || {}, `k${e.seq}`);
      if (node) items.push(node as ChainNode);
    } else {
      const body = evtBody(e);
      items.push({
        kind: "tool",
        key: `k${e.seq}`,
        title: KIND_TITLE[e.kind] ? i18n.t(KIND_TITLE[e.kind]) : e.kind,
        status: e.is_error ? "error" : "success",
        collapsible: !!body,
        content: body ? <pre className="tc-body">{body.slice(0, 4000)}</pre> : undefined,
      });
    }
  }
  const last = items.length - 1;
  items.forEach((n, i) => {
    const isLast = i === last;
    n.blink = false;
    if (n.kind === "think") {
      if (laneLive && isLast) {
        n.status = "loading";
        n.blink = true;
      } else {
        n.status = undefined;
      }
    } else if (n.status === "loading" && !laneLive) {
      n.status = failed ? "abort" : "success";
    }
    if (laneLive && isLast && n.status === "loading") n.blink = true;
  });
  return items;
}

/** 单条泳道的思考链：活跃末节点自动展开（用户手动点过的以手动为准） */
function LaneChain({ items, live }: { items: ChainItem[]; live: boolean }) {
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const lastKey = items[items.length - 1]?.key;
  const expandedKeys = chainExpandedKeys(manual, live ? lastKey : undefined);
  const onExpand = chainExpandSetter(setManual, expandedKeys);
  return <Chain items={items} expandedKeys={expandedKeys} onExpand={onExpand} />;
}

export default function TaskDetail() {
  const { t } = useTranslation();
  const { id } = useParams();
  const taskId = Number(id);
  const [d, setD] = useState<Detail | null>(null);
  // 详情含需求文档/方案正文：非认领人后端 403，页面只给提示（别停在「加载中」）
  const [denied, setDenied] = useState("");
  const [events, setEvents] = useState<(TaskEvent & { parent?: string })[]>([]);
  const seenRef = useRef<Set<number>>(new Set());
  // 会话树选中态：main = 主会话，否则为子会话 key（= 派生它的 Task tool_use id）
  const [sel, setSel] = useState("main");
  const [treeOpen, setTreeOpen] = useState(true);
  
  const reload = async () => {
    try {
      const detail = await api<Detail>(`/api/dev-tasks/${taskId}/detail`);
      setD(detail);
      return detail;
    } catch (e: any) {
      if (e.status === 403) setDenied(e.message);
      throw e;
    }
  };

  useEffect(() => {
    if (denied) return; // 403 已经定论，别再每 5 秒锤一次
    reload().catch(() => {});
    const timer = setInterval(() => reload().catch(() => {}), 5000);
    return () => clearInterval(timer);
  }, [taskId, denied]);

  useEffect(() => {
    if (denied) return;
    const load = async () => {
      const rows = await api<(TaskEvent & { parent?: string })[]>(`/api/dev-tasks/${taskId}/events`).catch(() => []);
      rows.forEach((r) => seenRef.current.add(r.seq));
      setEvents(rows);
    };
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [taskId, denied]);

  // SSE：需求级实时流里过滤本任务的过程事件（dev_raw.task_id = RunnerTask 号）
  useEffect(() => {
    if (!d?.requirement || !LIVE_STATUSES.includes(d.task.status)) return;
    const ac = new AbortController();
    const ids = new Set(d.runner_task_ids);
    (async () => {
      try {
        await openStream(
          d.requirement.id,
          ac.signal,
          (evt) => {
            if (evt.type === "dev_raw" && ids.has(evt.task_id)) {
              // 归一化块自带 kind；system 事件只有 type/subtype，补上 raw 才走原始事件分支
              const e = evt.event || {};
              const kind = e.kind || "raw";
              setEvents((list) => [
                ...list,
                { seq: -(Date.now() + list.length), ...e, kind, event: kind === "raw" ? e : undefined },
              ]);
            }
          },
        );
      } catch {
        /* 断线就算了，3s 轮询会兜底对账 */
      }
    })();
    return () => ac.abort();
  }, [d?.requirement?.id, d?.task?.status, d?.runner_task_ids?.join(",")]);

  // 按主/子 agent 分泳道：parent 为空 = 主会话；否则归到派生它的 Task tool_use 名下
  const lanes = useMemo(() => {
    const main: (TaskEvent & { parent?: string })[] = [];
    const subs = new Map<string, (TaskEvent & { parent?: string })[]>();
    const labels = new Map<string, string>();
    for (const e of events) {
      const parent = (e as any).parent || "";
      if (!parent) {
        main.push(e);
        if (e.kind === "tool_use" && ["Task", "Agent"].includes(e.name || "")) {
          try {
            const input = JSON.parse(e.input || "{}");
            labels.set((e as any).id || "", input.description || input.subagent_type || "");
          } catch {
            /* input 截断过的坏 JSON：标签退化成 id 后缀 */
          }
        }
      } else {
        if (!subs.has(parent)) subs.set(parent, []);
        subs.get(parent)!.push(e);
      }
    }
    const subLanes = [...subs.entries()].map(([parent, evts]) => ({
      key: parent,
      label: labels.get(parent) || i18n.t("taskDetail.subAgentLabel", { id: parent.slice(-6) }),
      evts,
    }));
    return { main, subLanes };
  }, [events, i18n.language]);

  if (denied)
    return (
      <Page title={t("taskDetail.pageTitle")} desc={t("taskDetail.descDenied")}>
        <div className="page-card" style={{ padding: 48, textAlign: "center" }}>
          <h5 className="rc-empty-title" style={{ marginBottom: 8 }}>
            {denied}
          </h5>
          <div className="muted">{t("taskDetail.deniedBody")}</div>
          <div style={{ marginTop: 16 }}>
            <Link to="/tasks">{t("taskDetail.backToTasks")}</Link>
          </div>
        </div>
      </Page>
    );
  if (!d) return <Page title={t("taskDetail.pageTitle")} desc={t("taskDetail.loading")} />;
  const { task, requirement: req, final_doc, final_plan } = d;
  const st = STATUS[task.status];
  const live = LIVE_STATUSES.includes(task.status);

  // 会话当前是否活跃：任务进行中，且该泳道的末事件正是全局末事件（同一对象引用）
  const lastEvt = events[events.length - 1];
  const laneActive = (evts: (TaskEvent & { parent?: string })[]) =>
    live && evts.length > 0 && evts[evts.length - 1] === lastEvt;

  const laneBlock = (title: React.ReactNode, evts: (TaskEvent & { parent?: string })[], laneLive: boolean) => (
    <div className="td-lane">
      <div className="td-lane-head">
        {title}
        <span className="td-lane-count">{t("taskDetail.eventsCount", { n: evts.length })}</span>
      </div>
      {evts.length ? (
        <LaneChain items={laneItems(evts, laneLive, task.status === "failed")} live={laneLive} />
      ) : (
        <span className="muted text-xs">{t("taskDetail.noEvents")}</span>
      )}
    </div>
  );

  return (
    <Page
      title={t("taskDetail.titleWithCode", { code: task.code })}
      desc={
        <span>
          <Link to="/tasks">← {t("taskDetail.backToTasks")}</Link> · {t("taskDetail.requirement")}{" "}
          <Link to={`/reqs/${req.id}`}>{req.code}</Link>《{req.title}》
        </span>
      }
    >
      <div className="td-shell">
      <div className="td-main">
      <div className="page-card">
        <div className="td-head">
          <Badge tone={st?.[0] ?? "neutral"}>{st ? t(st[1]) : task.status}</Badge>
          <span className="td-title">{task.title || task.code}</span>
          {task.runner_name && <Badge tone="info">⚡ {task.runner_name}</Badge>}
          {lanes.subLanes.length > 0 && (
            <Badge tone="violet">{t("taskDetail.mainAndSubs", { n: lanes.subLanes.length })}</Badge>
          )}
          {task.mr_url ? (
            <a href={task.mr_url} target="_blank" rel="noreferrer" className="mono">
              MR !{task.mr_iid}
            </a>
          ) : task.mr_iid ? (
            <span className="mono">MR !{task.mr_iid}</span>
          ) : null}
          <Link to={`/reqs/${req.id}`}>{t("taskDetail.requirement")}</Link>
        </div>
        <div className="td-meta">
          {task.branch && <span className="mono">{t("taskDetail.branch", { branch: task.branch })}</span>}
          <span>{t("taskDetail.project", { name: d.project?.name || `#${req.system_id}` })}</span>
          <span>{t("taskDetail.created", { time: (task.created_at || "").slice(0, 19) })}</span>
        </div>
        {task.scope && (
          <div className="td-scope">
            <b>{t("taskDetail.scope")}</b>
            {task.scope}
          </div>
        )}
        {task.error && (
          <div className="td-scope" style={{ background: "rgba(194,57,52,0.07)", color: "var(--danger)" }}>
            <b>{t("taskDetail.error")}</b>
            {task.error}
          </div>
        )}
      </div>

      <div className="td-docs">
        <div className="page-card td-doc">
          <h4>
            {t("taskDetail.finalPlan")}
            {final_plan && final_plan.version > 0 ? t("taskDetail.version", { version: final_plan.version }) : ""}
          </h4>
          {final_plan ? (
            <div className="md-body td-evt-wrap">
              <Markdown>{final_plan.content}</Markdown>
            </div>
          ) : (
            <div className="muted py-6 text-center text-xs">{t("taskDetail.noPlan")}</div>
          )}
        </div>

        <div className="page-card td-doc">
          <h4>{t("taskDetail.finalDoc")}</h4>
          {final_doc ? (
            <>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                🎯 {final_doc.filename}（v{final_doc.version}）
              </div>
              <div className="td-evt-wrap">
                <DocPreview reqId={req.id} doc={final_doc} />
              </div>
            </>
          ) : (
            <>
              <span className="muted text-xs">{t("taskDetail.noDoc")}</span>
              <div className="md-body td-evt-wrap" style={{ marginTop: 6 }}>
                {req.description || t("taskDetail.noDescription")}
              </div>
            </>
          )}
        </div>

        {d.tasks.length > 1 && (
          <div className="page-card td-full">
            <h4>{t("taskDetail.siblingTasks", { n: d.tasks.length })}</h4>
            {d.tasks.map((tk) => {
              const sib = STATUS[tk.status];
              return (
              <div key={tk.id} className={`td-pill${tk.id === task.id ? " self" : ""}`} title={tk.scope || undefined}>
                {tk.id === task.id && <span>📍</span>}
                <span className="mono">{tk.code}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {tk.title || "—"}
                </span>
                <Badge tone={sib?.[0] ?? "neutral"}>{sib ? t(sib[1]) : tk.status}</Badge>
                {tk.id !== task.id && <Link to={`/tasks/${tk.id}`}>{t("taskDetail.detail")}</Link>}
              </div>
              );
            })}
          </div>
        )}
      </div>

      </div>

      <aside className="rc-dock">
          <div className="rc-dock-head">
            <span className="rc-dock-title">
              <Bot size={15} className="text-primary" />
              {t("taskDetail.progressTitle")}
              {live && t("taskDetail.liveSuffix")}
            </span>
            {live && <i className="sess-live" />}
          </div>
          {events.length ? (
            <div className="td-evt-wrap">
              {/* Claude 式会话树：主会话行可折叠，子会话 L 形连线挂载，点击切换下方思考链 */}
              <div className="sess-tree">
                <div
                  className={`sess-row main${sel === "main" ? " on" : ""}`}
                  onClick={() => setSel("main")}
                >
                  <span
                    className={`sess-caret${treeOpen ? " open" : ""}${lanes.subLanes.length ? "" : " leaf"}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setTreeOpen(!treeOpen);
                    }}
                  >
                    <ChevronRight size={14} />
                  </span>
                  <span className="sess-ico">🤖</span>
                  <span className="sess-name" title={task.title || task.code}>
                    {task.title || t("taskDetail.mainSession")}
                  </span>
                  <span className="sess-count">{lanes.main.length}</span>
                  {laneActive(lanes.main) && <i className="sess-live" />}
                </div>
                {treeOpen &&
                  lanes.subLanes.map((s) => (
                    <div key={s.key} className="sess-branch">
                      <div
                        className={`sess-row sub${sel === s.key ? " on" : ""}`}
                        onClick={() => setSel(s.key)}
                      >
                        <span className="sess-ico">🧩</span>
                        <span className="sess-name" title={s.label}>
                          {s.label}
                        </span>
                        <span className="sess-count">{s.evts.length}</span>
                        {laneActive(s.evts) && <i className="sess-live" />}
                      </div>
                    </div>
                  ))}
              </div>
              {sel === "main"
                ? laneBlock(
                    <span>
                      🤖 <b>{t("taskDetail.mainAgent")}</b>
                    </span>,
                    lanes.main,
                    live
                  )
                : laneBlock(
                    <span>
                      🧩 <b>{lanes.subLanes.find((s) => s.key === sel)?.label}</b>{" "}
                      <Badge className="ml-1">{t("taskDetail.subAgent")}</Badge>
                    </span>,
                    lanes.subLanes.find((s) => s.key === sel)?.evts || [],
                    live
                  )}
            </div>
          ) : (
            <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
              {live && <LoaderCircle size={14} className="animate-spin" />}
              {live ? t("taskDetail.waiting") : t("taskDetail.noTrace")}
            </div>
          )}
        </aside>
      </div>
    </Page>
  );
}
