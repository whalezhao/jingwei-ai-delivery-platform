import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button, Empty, Spin, Tag, Typography } from "antd";
import { BulbOutlined, CheckOutlined, CloseOutlined, RightOutlined } from "@ant-design/icons";
import Markdown from "react-markdown";
import ThoughtChain from "@ant-design/x/es/thought-chain";
import type { ThoughtChainItemType } from "@ant-design/x/es/thought-chain";
import { api } from "../api";
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
 * 进展 = Ant Design X ThoughtChain，按事件的 parent 字段分「主 Agent / 子 Agent」泳道
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

const KIND_TITLE: Record<string, string> = {
  text: "输出",
  result: "完成",
};

function evtBody(e: TaskEvent & { parent?: string }): string {
  if (e.kind === "tool_use") return e.input || "";
  if (e.kind === "tool_result" || e.kind === "result") return e.content || e.text || "";
  return e.text || (e.kind === "raw" ? JSON.stringify((e as any).event || {}).slice(0, 2000) : "");
}

/** 泳道事件 → ThoughtChain 节点（与需求对话页同款）：
 *  每次思考是可折叠链节点；tool_use/tool_result 就地配对，按工具类型出动作行
 *  （命令/文件/搜索…）；原生 status（loading 转圈 / success ✓ / error ✗ / abort ⊖），
 *  思考结束回落灯泡图标；任务失败时遗留 loading 标 abort。 */
function laneItems(
  evts: (TaskEvent & { parent?: string })[],
  laneLive: boolean,
  failed: boolean,
): ThoughtChainItemType[] {
  type ChainNode = ThoughtChainItemType & { kind: "think" | "tool" | "raw"; toolName?: string; arg?: string };
  const items: ChainNode[] = [];
  for (const e of evts) {
    if (e.kind === "thinking") {
      items.push({
        kind: "think",
        key: `k${e.seq}`,
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
              e.is_error ? <CloseOutlined /> : <CheckOutlined />,
              line || "（无输出）",
              e.is_error ? "err" : "ok",
              e.is_error ? "执行出错" : "完成",
            )}
            {e.is_error && <pre className="tc-body tc-err">{e.content}</pre>}
          </>
        );
      } else {
        items.push({
          kind: "tool",
          key: `k${e.seq}`,
          title: "工具结果",
          status: e.is_error ? "error" : "success",
          collapsible: true,
          content: <pre className={`tc-body${e.is_error ? " tc-err" : ""}`}>{e.content}</pre>,
        });
      }
    } else if (e.kind === "raw") {
      // claude 的 system 事件：init 只是会话元信息（工作目录/工具清单），对人没信息量；
      // 其余几类是一行可读的进度或里程碑，不该把整段 JSON 铺给用户看
      const ev = (e as any).event || {};
      const sub = ev.subtype || "";
      if (sub === "init" || (sub === "task_summary" && !ev.detail)) continue;
      const node: ChainNode = { kind: "raw", key: `k${e.seq}`, icon: <RightOutlined />, title: "原始事件" };
      if (sub === "task_summary") {
        node.title = ev.detail;
      } else if (sub === "vcs_state_changed") {
        node.icon = <CheckOutlined />;
        node.title = ev.kind === "commit" ? "提交代码" : "Git 状态变化";
        node.description = ev.branch;
      } else if (sub === "task_progress") {
        node.title = "子代理进展";
        node.description = ev.description;
        if (ev.usage) {
          node.footer = `${ev.usage.tool_uses ?? 0} 工具 · ${((ev.usage.total_tokens ?? 0) / 1000).toFixed(1)}k tokens`;
        }
      } else if (sub === "post_turn_summary") {
        node.icon = <BulbOutlined />;
        node.title = "本轮总结";
        node.collapsible = true;
        node.content = <pre className="tc-body">{String(ev.status_detail || "").slice(0, 4000)}</pre>;
      } else {
        node.title = `原始事件 · ${sub || "?"}`;
        node.collapsible = true;
        node.content = <pre className="tc-body">{JSON.stringify(ev).slice(0, 2000)}</pre>;
      }
      items.push(node);
    } else {
      const body = evtBody(e);
      items.push({
        kind: "tool",
        key: `k${e.seq}`,
        title: KIND_TITLE[e.kind] || e.kind,
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
function LaneChain({ items, live }: { items: ThoughtChainItemType[]; live: boolean }) {
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const lastKey = items[items.length - 1]?.key;
  const expandedKeys = chainExpandedKeys(manual, live ? lastKey : undefined);
  const onExpand = chainExpandSetter(setManual, expandedKeys);
  return <ThoughtChain items={items} expandedKeys={expandedKeys} onExpand={onExpand} />;
}

export default function TaskDetail() {
  const { id } = useParams();
  const taskId = Number(id);
  const [d, setD] = useState<Detail | null>(null);
  const [events, setEvents] = useState<(TaskEvent & { parent?: string })[]>([]);
  const seenRef = useRef<Set<number>>(new Set());
  // 会话树选中态：main = 主会话，否则为子会话 key（= 派生它的 Task tool_use id）
  const [sel, setSel] = useState("main");
  const [treeOpen, setTreeOpen] = useState(true);
  // AI 工作进展：右侧常驻停靠栏（占位不遮挡，默认展开）
  const [progOpen, setProgOpen] = useState(true);

  const reload = async () => {
    const detail = await api<Detail>(`/api/dev-tasks/${taskId}/detail`);
    setD(detail);
    return detail;
  };

  useEffect(() => {
    reload().catch(() => {});
    const t = setInterval(() => reload().catch(() => {}), 5000);
    return () => clearInterval(t);
  }, [taskId]);

  useEffect(() => {
    const load = async () => {
      const rows = await api<(TaskEvent & { parent?: string })[]>(`/api/dev-tasks/${taskId}/events`).catch(() => []);
      rows.forEach((r) => seenRef.current.add(r.seq));
      setEvents(rows);
    };
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [taskId]);

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
              setEvents((list) => [...list, { seq: -(Date.now() + list.length), ...evt.event }]);
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
      label: labels.get(parent) || `子 Agent ${parent.slice(-6)}`,
      evts,
    }));
    return { main, subLanes };
  }, [events]);

  if (!d) return <Page title="任务详情" desc="加载中…" />;
  const { task, requirement: req, final_doc, final_plan } = d;
  const st = STATUS[task.status] || ["default", task.status];
  const live = LIVE_STATUSES.includes(task.status);

  // 会话当前是否活跃：任务进行中，且该泳道的末事件正是全局末事件（同一对象引用）
  const lastEvt = events[events.length - 1];
  const laneActive = (evts: (TaskEvent & { parent?: string })[]) =>
    live && evts.length > 0 && evts[evts.length - 1] === lastEvt;

  const laneBlock = (title: React.ReactNode, evts: (TaskEvent & { parent?: string })[], laneLive: boolean) => (
    <div className="td-lane">
      <div className="td-lane-head">
        {title}
        <span className="td-lane-count">{evts.length} 事件</span>
      </div>
      {evts.length ? (
        <LaneChain items={laneItems(evts, laneLive, task.status === "failed")} live={laneLive} />
      ) : (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          暂无事件
        </Typography.Text>
      )}
    </div>
  );

  return (
    <Page
      title={`任务详情 · ${task.code}`}
      desc={
        <span>
          <Link to="/tasks">← 返回任务池</Link> · 需求{" "}
          <Link to={`/reqs/${req.id}`}>{req.code}</Link>《{req.title}》
        </span>
      }
    >
      <div className="td-shell">
      <div className="td-main">
      <div className="page-card">
        <div className="td-head">
          <Tag color={st[0]}>{st[1]}</Tag>
          <span className="td-title">{task.title || task.code}</span>
          {task.runner_name && <Tag color="blue">⚡ {task.runner_name}</Tag>}
          {lanes.subLanes.length > 0 && (
            <Tag color="purple">🤖 主 + {lanes.subLanes.length} 子 Agent</Tag>
          )}
          {task.mr_url ? (
            <a href={task.mr_url} target="_blank" rel="noreferrer" className="mono">
              MR !{task.mr_iid}
            </a>
          ) : task.mr_iid ? (
            <span className="mono">MR !{task.mr_iid}</span>
          ) : null}
          <Link to={`/reqs/${req.id}`}>需求对话</Link>
          {!progOpen && (
            <Button size="small" onClick={() => setProgOpen(true)}>
              🤖 AI 进展
            </Button>
          )}
        </div>
        <div className="td-meta">
          {task.branch && <span className="mono">分支 {task.branch}</span>}
          <span>项目 {d.project?.name || `#${req.project_id}`}</span>
          <span>创建 {(task.created_at || "").slice(0, 19)}</span>
        </div>
        {task.scope && (
          <div className="td-scope">
            <b>本任务范围：</b>
            {task.scope}
          </div>
        )}
        {task.error && (
          <div className="td-scope" style={{ background: "#fff2f0", color: "#cf1322" }}>
            <b>错误：</b>
            {task.error}
          </div>
        )}
      </div>

      <div className="td-docs">
        <div className="page-card td-doc">
          <h4>
            定稿方案{final_plan && final_plan.version > 0 ? `（v${final_plan.version}）` : ""}
          </h4>
          {final_plan ? (
            <div className="md-body td-evt-wrap">
              <Markdown>{final_plan.content}</Markdown>
            </div>
          ) : (
            <Empty description="该需求尚未定稿方案" style={{ padding: "24px 0" }} />
          )}
        </div>

        <div className="page-card td-doc">
          <h4>定稿需求</h4>
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
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                无定稿文档，以下为需求描述：
              </Typography.Text>
              <div className="md-body td-evt-wrap" style={{ marginTop: 6 }}>
                {req.description || "（无描述）"}
              </div>
            </>
          )}
        </div>

        {d.tasks.length > 1 && (
          <div className="page-card td-full">
            <h4>同需求任务（{d.tasks.length} 个）</h4>
            {d.tasks.map((t) => (
              <div key={t.id} className={`td-pill${t.id === task.id ? " self" : ""}`} title={t.scope || undefined}>
                {t.id === task.id && <span>📍</span>}
                <span className="mono">{t.code}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.title || "—"}
                </span>
                <Tag
                  color={(STATUS[t.status] || ["default", t.status])[0]}
                  style={{ marginInlineEnd: 0 }}
                >
                  {(STATUS[t.status] || ["default", t.status])[1]}
                </Tag>
                {t.id !== task.id && <Link to={`/tasks/${t.id}`}>详情</Link>}
              </div>
            ))}
          </div>
        )}
      </div>

      </div>

      {progOpen && (
        <aside className="rc-dock">
          <div className="rc-dock-head">
            <span>🤖 AI 工作进展{live && "（实时）"}</span>
            {live && <i className="sess-live" />}
            <button
              className="rc-dock-toggle"
              onClick={() => setProgOpen(false)}
              title="收起进展栏"
            >
              »
            </button>
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
                    <RightOutlined />
                  </span>
                  <span className="sess-ico">🤖</span>
                  <span className="sess-name" title={task.title || task.code}>
                    {task.title || "主会话"}
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
                      🤖 <b>主 Agent</b>
                    </span>,
                    lanes.main,
                    live
                  )
                : laneBlock(
                    <span>
                      🧩 <b>{lanes.subLanes.find((s) => s.key === sel)?.label}</b>{" "}
                      <Tag style={{ marginInlineStart: 4 }}>子 Agent</Tag>
                    </span>,
                    lanes.subLanes.find((s) => s.key === sel)?.evts || [],
                    live
                  )}
            </div>
          ) : (
            <Empty
              description={
                live ? (
                  <span>
                    <Spin size="small" /> 等待 runner 认领/回传过程…
                  </span>
                ) : (
                  "暂无过程留痕（任务未执行或 runner 未回传）"
                )
              }
              style={{ padding: "32px 0" }}
            />
          )}
        </aside>
      )}
      </div>
    </Page>
  );
}
