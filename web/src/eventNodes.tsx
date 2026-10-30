import { FileCard, Sources } from "@ant-design/x";
import type { ThoughtChainItemType } from "@ant-design/x/es/thought-chain";
import {
  ApiOutlined,
  BulbOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  CompressOutlined,
  ExclamationCircleOutlined,
  FileDoneOutlined,
  InfoOutlined,
  LockOutlined,
  NotificationOutlined,
  RobotOutlined,
  StopOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  WarningOutlined,
} from "@ant-design/icons";

/**
 * claude stream-json 原始事件 → 思考链节点。
 *
 * 归一化事件（thinking / tool_use / tool_result）由调用方各自处理，这里只管
 * `kind: "raw"` 的 system 事件和顶层事件（tool_progress / tool_use_summary …）。
 * 子类型清单对齐 @anthropic-ai/claude-agent-sdk 的 SDKMessage 定义：认不出的
 * 子类型也给一行可读节点，绝不把整段 JSON 直接铺给用户。
 */
export type ChainNode = ThoughtChainItemType & {
  kind: "think" | "tool" | "sub" | "raw";
  toolName?: string;
  arg?: string;
};

const pre = (s: unknown, max = 2000) => (
  <pre className="tc-body">{String(s ?? "").slice(0, max) || "（无输出）"}</pre>
);

/** 一行即可说明、没有后续内容的事件：直接给标题 + 描述，不带正文。 */
const PLAIN: Record<string, [React.ReactNode, (ev: any) => string, string?]> = {
  status: [
    <CompressOutlined />,
    (ev) => (ev.status === "compacting" ? "正在压缩上下文" : ev.status === "requesting" ? "正在请求模型" : "会话状态变化"),
    "loading",
  ],
  api_retry: [
    <SyncOutlined />,
    (ev) => `接口重试 ${ev.attempt ?? "?"}/${ev.max_retries ?? "?"}${ev.error ? ` · ${ev.error}` : ""}`,
    "loading",
  ],
  control_request_progress: [
    <ClockCircleOutlined />,
    (ev) => `请求进行中 · ${ev.status ?? ""}${ev.attempt ? `（第 ${ev.attempt} 次）` : ""}`,
    "loading",
  ],
  permission_denied: [(<LockOutlined />), (ev) => `权限被拒 · ${ev.tool_name ?? ""}`, "error"],
  notification: [(<NotificationOutlined />), (ev) => String(ev.text ?? "").slice(0, 200)],
  informational: [(<InfoOutlined />), (ev) => String(ev.content ?? "").slice(0, 200)],
  local_command_output: [(<ToolOutlined />), () => "本地命令输出"],
  mirror_error: [(<WarningOutlined />), (ev) => `镜像错误 · ${String(ev.error ?? "").slice(0, 160)}`, "error"],
  worker_shutting_down: [(<StopOutlined />), (ev) => `执行器退出 · ${ev.reason ?? ""}`, "abort"],
  session_state_changed: [(<ApiOutlined />), (ev) => `会话状态 · ${ev.state ?? ""}`],
  prompt_suggestion: [(<BulbOutlined />), (ev) => `提示建议 · ${String(ev.suggestion ?? "").slice(0, 120)}`],
  conversation_reset: [(<SyncOutlined />), () => "会话已重置"],
  active_goal: [(<CheckOutlined />), (ev) => `持续目标 · ${ev.value?.condition ?? "已清除"}`],
  rate_limit_event: [(<ClockCircleOutlined />), (ev) => `限流提示 · ${ev.rate_limit_info?.status ?? ""}`],
  auth_status: [(<LockOutlined />), (ev) => `认证状态 · ${ev.error ? String(ev.error).slice(0, 120) : "进行中"}`],
  tool_progress: [(<ClockCircleOutlined />), (ev) => `工具运行中 · ${ev.tool_name ?? ""}`, "loading"],
  tool_use_summary: [(<BulbOutlined />), (ev) => `工具摘要 · ${String(ev.summary ?? "").slice(0, 200)}`],
  commands_changed: [(<ToolOutlined />), (ev) => `命令清单更新 · ${Array.isArray(ev.commands) ? ev.commands.length : 0} 条`],
  background_tasks_changed: [
    (<RobotOutlined />),
    (ev) => `后台任务 · ${Array.isArray(ev.tasks) ? ev.tasks.length : 0} 个`,
  ],
  plugin_install: [(<ThunderboltOutlined />), (ev) => `插件 ${ev.name ?? ""} · ${ev.status ?? ""}`],
  elicitation_complete: [(<CheckOutlined />), (ev) => `交互完成 · ${ev.mcp_server_name ?? ""}`],
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
        ? { kind: "raw", key, icon: <BulbOutlined />, title: ev.detail }
        : null;

    case "vcs_state_changed":
      return {
        kind: "raw",
        key,
        icon: <CheckOutlined />,
        title: ev.kind === "commit" ? "提交代码" : "Git 状态变化",
        description: ev.branch,
      };

    case "task_progress":
      return {
        kind: "raw",
        key,
        icon: <RobotOutlined />,
        title: "子代理进展",
        description: ev.description,
        footer: ev.usage
          ? `${ev.usage.tool_uses ?? 0} 工具 · ${((ev.usage.total_tokens ?? 0) / 1000).toFixed(1)}k tokens`
          : undefined,
      };

    case "post_turn_summary":
      return {
        kind: "raw",
        key,
        icon: <BulbOutlined />,
        title: "本轮总结",
        collapsible: true,
        content: pre(String(ev.status_detail || ""), 4000),
      };

    case "task_started":
      return {
        kind: "raw",
        key,
        icon: <RobotOutlined />,
        title: `子任务开始 · ${ev.subagent_type || "agent"}`,
        description: ev.description,
        status: ev.is_backgrounded ? undefined : "loading",
        footer: ev.task_id ? `task ${String(ev.task_id).slice(-8)}` : undefined,
      };

    case "task_updated": {
      const st = ev.patch?.status;
      return {
        kind: "raw",
        key,
        icon: <RobotOutlined />,
        title: "子任务状态变更",
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
        icon: ok ? <CheckOutlined /> : <CloseOutlined />,
        title: `子任务${ok ? "完成" : ev.status === "failed" ? "失败" : "停止"}`,
        description: ev.summary,
        status: ok ? "success" : ev.status === "failed" ? "error" : "abort",
        footer: ev.usage
          ? `${ev.usage.tool_uses ?? 0} 工具 · ${((ev.usage.total_tokens ?? 0) / 1000).toFixed(1)}k tokens`
          : undefined,
      };
    }

    case "hook_started":
      return {
        kind: "raw",
        key,
        icon: <ThunderboltOutlined />,
        title: `钩子 · ${ev.hook_name || ev.hook_event || ""}`,
        description: ev.hook_event,
        status: "loading",
      };

    case "hook_response": {
      const outcome = String(ev.outcome || "");
      return {
        kind: "raw",
        key,
        icon: <ThunderboltOutlined />,
        title: `钩子 · ${ev.hook_name || ev.hook_event || ""}`,
        description: outcome ? `结束 · ${outcome}` : undefined,
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
        icon: waiting ? <ClockCircleOutlined /> : <CheckOutlined />,
        title: waiting ? "等待权限确认" : "权限确认结束",
        description: ev.agent_id ? "子代理内调用" : undefined,
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
        icon: <CompressOutlined />,
        title: `上下文压缩 · ${m.trigger === "manual" ? "手动" : "自动"}`,
        description: `${from}${to} tokens`,
      };
    }

    case "files_persisted": {
      const files: { filename: string; file_id: string }[] = Array.isArray(ev.files) ? ev.files : [];
      const failed: { filename: string; error: string }[] = Array.isArray(ev.failed) ? ev.failed : [];
      return {
        kind: "raw",
        key,
        icon: <FileDoneOutlined />,
        title: `文件已保存 · ${files.length} 个${failed.length ? ` · ${failed.length} 个失败` : ""}`,
        collapsible: failed.length > 0,
        content: failed.length ? pre(failed.map((f) => `${f.filename}: ${f.error}`).join("\n")) : undefined,
        description: files.length ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
            {files.slice(0, 8).map((f) => (
              <FileCard key={f.file_id || f.filename} name={f.filename} size="small" />
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
        icon: <BulbOutlined />,
        title: `记忆召回 · ${memories.length} 条`,
        description: memories.length ? (
          <Sources
            inline
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
      const dir = ev.direction === "revert" ? "回退" : ev.direction === "sticky" ? "粘性切换" : "重试";
      return {
        kind: "raw",
        key,
        icon: <WarningOutlined />,
        title: `模型拒答，${dir}到 ${ev.fallback_model || "备用模型"}`,
        description: `${ev.original_model || ""}${ev.api_refusal_category ? ` · ${ev.api_refusal_category}` : ""}`,
        collapsible: !!ev.content,
        content: pre(ev.content),
      };
    }

    case "model_refusal_no_fallback":
      return {
        kind: "raw",
        key,
        icon: <ExclamationCircleOutlined />,
        title: "模型拒答（无备用模型）",
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
        icon: <InfoOutlined />,
        title: `未识别事件 · ${sub || "?"}`,
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
