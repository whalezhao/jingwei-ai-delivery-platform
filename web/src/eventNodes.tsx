import {
  Bot,
  Check,
  CircleAlert,
  CircleStop,
  Clock,
  FileCheck2,
  Info,
  Lightbulb,
  Lock,
  Megaphone,
  Minimize2,
  Plug,
  RefreshCw,
  TriangleAlert,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import i18n from "./i18n";
import { FileChip, SourceChips, type ChainItem } from "./ui/Chain";

/**
 * claude stream-json 原始事件 → 思考链节点。
 *
 * 归一化事件（thinking / tool_use / tool_result）由调用方各自处理，这里只管
 * `kind: "raw"` 的 system 事件和顶层事件（tool_progress / tool_use_summary …）。
 * 子类型清单对齐 @anthropic-ai/claude-agent-sdk 的 SDKMessage 定义：认不出的
 * 子类型也给一行可读节点，绝不把整段 JSON 直接铺给用户。
 */
export type ChainNode = ChainItem & {
  kind: "think" | "tool" | "sub" | "raw";
  toolName?: string;
  arg?: string;
};

const pre = (s: unknown, max = 2000) => (
  <pre className="tc-body">{String(s ?? "").slice(0, max) || i18n.t("events.noOutput")}</pre>
);

/** 一行即可说明、没有后续内容的事件：直接给标题 + 描述，不带正文。 */
const PLAIN: Record<string, [React.ReactNode, (ev: any) => string, string?]> = {
  status: [
    <Minimize2 size={14} />,
    (ev) =>
      i18n.t(
        ev.status === "compacting"
          ? "events.statusCompacting"
          : ev.status === "requesting"
            ? "events.statusRequesting"
            : "events.statusChanged",
      ),
    "loading",
  ],
  api_retry: [
    <RefreshCw size={14} />,
    (ev) =>
      i18n.t("events.apiRetry", { attempt: ev.attempt ?? "?", max: ev.max_retries ?? "?" }) +
      (ev.error ? ` · ${ev.error}` : ""),
    "loading",
  ],
  control_request_progress: [
    <Clock size={14} />,
    (ev) =>
      i18n.t("events.progress", { status: ev.status ?? "" }) +
      (ev.attempt ? " " + i18n.t("events.attemptNth", { n: ev.attempt }) : ""),
    "loading",
  ],
  permission_denied: [(<Lock size={14} />), (ev) => i18n.t("events.permissionDenied", { tool: ev.tool_name ?? "" }), "error"],
  notification: [(<Megaphone size={14} />), (ev) => String(ev.text ?? "").slice(0, 200)],
  informational: [(<Info size={14} />), (ev) => String(ev.content ?? "").slice(0, 200)],
  local_command_output: [(<Wrench size={14} />), () => i18n.t("events.localCommandOutput")],
  mirror_error: [
    <TriangleAlert size={14} />,
    (ev) => i18n.t("events.mirrorError", { error: String(ev.error ?? "").slice(0, 160) }),
    "error",
  ],
  worker_shutting_down: [
    <CircleStop size={14} />,
    (ev) => i18n.t("events.workerShuttingDown", { reason: ev.reason ?? "" }),
    "abort",
  ],
  session_state_changed: [(<Plug size={14} />), (ev) => i18n.t("events.sessionState", { state: ev.state ?? "" })],
  prompt_suggestion: [
    <Lightbulb size={14} />,
    (ev) => i18n.t("events.promptSuggestion", { suggestion: String(ev.suggestion ?? "").slice(0, 120) }),
  ],
  conversation_reset: [(<RefreshCw size={14} />), () => i18n.t("events.conversationReset")],
  active_goal: [
    <Check size={14} />,
    (ev) => i18n.t("events.activeGoal", { condition: ev.value?.condition ?? i18n.t("events.cleared") }),
  ],
  rate_limit_event: [
    <Clock size={14} />,
    (ev) => i18n.t("events.rateLimit", { status: ev.rate_limit_info?.status ?? "" }),
  ],
  auth_status: [
    <Lock size={14} />,
    (ev) =>
      i18n.t("events.authStatus", {
        status: ev.error ? String(ev.error).slice(0, 120) : i18n.t("events.authOngoing"),
      }),
  ],
  tool_progress: [(<Clock size={14} />), (ev) => i18n.t("events.toolProgress", { tool: ev.tool_name ?? "" }), "loading"],
  tool_use_summary: [
    <Lightbulb size={14} />,
    (ev) => i18n.t("events.toolSummary", { summary: String(ev.summary ?? "").slice(0, 200) }),
  ],
  commands_changed: [
    <Wrench size={14} />,
    (ev) => i18n.t("events.commandsChanged", { n: Array.isArray(ev.commands) ? ev.commands.length : 0 }),
  ],
  background_tasks_changed: [
    (<Bot size={14} />),
    (ev) => i18n.t("events.backgroundTasks", { n: Array.isArray(ev.tasks) ? ev.tasks.length : 0 }),
  ],
  plugin_install: [
    <Zap size={14} />,
    (ev) => i18n.t("events.pluginInstall", { name: ev.name ?? "", status: ev.status ?? "" }),
  ],
  elicitation_complete: [
    <Check size={14} />,
    (ev) => i18n.t("events.elicitationComplete", { server: ev.mcp_server_name ?? "" }),
  ],
};

/** 只有描述、没有正文（或正文默认折叠）的几类，单独写，字段名各不相同。 */
function detailNode(ev: any, key: string): ChainNode | null {
  const sub: string = ev.subtype || ev.type || "";
  switch (sub) {
    // init 是会话元信息（工具清单/模型/工作目录），对执行过程没有信息量
    case "init":
    // 逐 token 计量与钩子进度属于噪声：上游已过滤，兜底再挡一次
    case "thinking_tokens":
    case "hook_progress":
      return null;

    // 一句话进度（上游自带人话 detail），有 detail 才有信息量
    case "task_summary":
      return ev.detail
        ? { kind: "raw", key, icon: <Lightbulb size={14} />, title: ev.detail }
        : null;

    case "vcs_state_changed":
      return {
        kind: "raw",
        key,
        icon: <Check size={14} />,
        title: ev.kind === "commit" ? i18n.t("events.vcsCommit") : i18n.t("events.vcsChanged"),
        description: ev.branch,
      };

    case "task_progress":
      return {
        kind: "raw",
        key,
        icon: <Bot size={14} />,
        title: i18n.t("events.subagentProgress"),
        description: ev.description,
        footer: ev.usage
          ? i18n.t("events.usage", {
              tools: ev.usage.tool_uses ?? 0,
              tokens: ((ev.usage.total_tokens ?? 0) / 1000).toFixed(1),
            })
          : undefined,
      };

    case "post_turn_summary":
      return {
        kind: "raw",
        key,
        icon: <Lightbulb size={14} />,
        title: i18n.t("events.turnSummary"),
        collapsible: true,
        content: pre(String(ev.status_detail || ""), 4000),
      };

    case "task_started":
      return {
        kind: "raw",
        key,
        icon: <Bot size={14} />,
        title: i18n.t("events.taskStarted", { type: ev.subagent_type || "agent" }),
        description: ev.description,
        status: ev.is_backgrounded ? undefined : "loading",
        footer: ev.task_id ? `task ${String(ev.task_id).slice(-8)}` : undefined,
      };

    case "task_updated": {
      const st = ev.patch?.status;
      return {
        kind: "raw",
        key,
        icon: <Bot size={14} />,
        title: i18n.t("events.taskUpdated"),
        description: ev.patch?.description || st,
        status: st === "failed" ? "error" : st === "completed" ? "success" : undefined,
        collapsible: !!ev.patch?.error,
        content: ev.patch?.error ? pre(ev.patch.error) : undefined,
      };
    }

    case "task_notification": {
      const ok = ev.status === "completed";
      return {
        kind: "raw",
        key,
        icon: ok ? <Check size={14} /> : <X size={14} />,
        title: i18n.t(ok ? "events.taskDone" : ev.status === "failed" ? "events.taskFailed" : "events.taskStopped"),
        description: ev.summary,
        status: ok ? "success" : ev.status === "failed" ? "error" : "abort",
        footer: ev.usage
          ? i18n.t("events.usage", {
              tools: ev.usage.tool_uses ?? 0,
              tokens: ((ev.usage.total_tokens ?? 0) / 1000).toFixed(1),
            })
          : undefined,
      };
    }

    case "hook_started":
      return {
        kind: "raw",
        key,
        icon: <Zap size={14} />,
        title: i18n.t("events.hook", { name: ev.hook_name || ev.hook_event || "" }),
        description: ev.hook_event,
        status: "loading",
      };

    case "hook_response": {
      const outcome = String(ev.outcome || "");
      return {
        kind: "raw",
        key,
        icon: <Zap size={14} />,
        title: i18n.t("events.hook", { name: ev.hook_name || ev.hook_event || "" }),
        description: outcome ? i18n.t("events.ended", { outcome }) : undefined,
        status: outcome === "success" ? "success" : outcome === "error" ? "error" : "abort",
        collapsible: !!(ev.stdout || ev.stderr || ev.output),
        content: pre(ev.output || ev.stderr || ev.stdout),
      };
    }

    // 工具调用在权限检查上等了约 4 秒仍无结论时上报 checking，同一 tool_use_id
    // 在等待结束后再报一条 done——done 只表示"不再等了"，不等于已授权
    case "permission_check_status": {
      const waiting = ev.status !== "done";
      return {
        kind: "raw",
        key,
        icon: waiting ? <Clock size={14} /> : <Check size={14} />,
        title: waiting ? i18n.t("events.permWaiting") : i18n.t("events.permDone"),
        description: ev.agent_id ? i18n.t("events.insideSubagent") : undefined,
        status: waiting ? "loading" : undefined,
      };
    }

    case "compact_boundary": {
      const m = ev.compact_metadata || {};
      const from = m.pre_tokens ? `${(m.pre_tokens / 1000).toFixed(0)}k` : "?";
      const to = m.post_tokens ? ` → ${(m.post_tokens / 1000).toFixed(0)}k` : "";
      return {
        kind: "raw",
        key,
        icon: <Minimize2 size={14} />,
        title: i18n.t("events.compact", {
          mode: m.trigger === "manual" ? i18n.t("events.manual") : i18n.t("events.auto"),
        }),
        description: `${from}${to} tokens`,
      };
    }

    case "files_persisted": {
      const files: { filename: string; file_id: string }[] = Array.isArray(ev.files) ? ev.files : [];
      const failed: { filename: string; error: string }[] = Array.isArray(ev.failed) ? ev.failed : [];
      return {
        kind: "raw",
        key,
        icon: <FileCheck2 size={14} />,
        title:
          i18n.t("events.filesSaved", { n: files.length }) +
          (failed.length ? i18n.t("events.filesFailed", { n: failed.length }) : ""),
        collapsible: failed.length > 0,
        content: failed.length ? pre(failed.map((f) => `${f.filename}: ${f.error}`).join("\n")) : undefined,
        description: files.length ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
            {files.slice(0, 8).map((f) => (
              <FileChip key={f.file_id || f.filename} name={f.filename} />
            ))}
          </div>
        ) : undefined,
      };
    }

    case "memory_recall": {
      const memories: { path: string; scope: string; content?: string }[] = Array.isArray(ev.memories)
        ? ev.memories
        : [];
      return {
        kind: "raw",
        key,
        icon: <Lightbulb size={14} />,
        title: i18n.t("events.memoryRecall", { n: memories.length }),
        description: memories.length ? (
          <SourceChips
            items={memories.slice(0, 8).map((m, i) => ({
              key: `${key}-${i}`,
              title: String(m.path || "").split("/").pop() || m.path,
              description: m.scope,
            }))}
          />
        ) : undefined,
      };
    }

    case "model_refusal_fallback": {
      const dir =
        ev.direction === "revert"
          ? i18n.t("events.refusalRevert")
          : ev.direction === "sticky"
            ? i18n.t("events.refusalSticky")
            : i18n.t("events.refusalRetry");
      return {
        kind: "raw",
        key,
        icon: <TriangleAlert size={14} />,
        title: i18n.t("events.refusalFallback", {
          dir,
          model: ev.fallback_model || i18n.t("events.fallbackModel"),
        }),
        description: `${ev.original_model || ""}${ev.api_refusal_category ? ` · ${ev.api_refusal_category}` : ""}`,
        collapsible: !!ev.content,
        content: pre(ev.content),
      };
    }

    case "model_refusal_no_fallback":
      return {
        kind: "raw",
        key,
        icon: <CircleAlert size={14} />,
        title: i18n.t("events.refusalNoFallback"),
        description: ev.api_refusal_category || ev.original_model,
        status: "error",
        collapsible: !!ev.content,
        content: pre(ev.content),
      };

    default: {
      const plain = PLAIN[sub];
      if (plain) {
        const [icon, text, status] = plain;
        const body = text(ev);
        return {
          kind: "raw",
          key,
          icon,
          title: body || sub,
          status: status as ChainNode["status"],
        };
      }
      // 认不出的事件：给一行可读标题，JSON 默认折叠，不占版面
      return {
        kind: "raw",
        key,
        icon: <Info size={14} />,
        title: i18n.t("events.unknown", { sub: sub || "?" }),
        collapsible: true,
        content: <pre className="tc-body">{JSON.stringify(ev).slice(0, 2000)}</pre>,
      };
    }
  }
}

/** 一条 raw 事件 → 节点；返回 null 表示这类事件不展示（纯噪声）。 */
export function rawNode(ev: any, key: string): ChainNode | null {
  if (!ev || typeof ev !== "object") return null;
  return detailNode(ev, key);
}
