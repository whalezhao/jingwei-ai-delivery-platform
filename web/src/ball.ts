/**
 * 侧栏表情球的情绪总线。
 *
 * base：页面 / AI 的持续状态（展开该页面期间一直生效）；
 * flash：一次性反馈（成功、错误、点击…），到时自动回到 base。
 * 球自己还管悬停、失焦、久坐发呆，优先级高于这里。
 */

import { useSyncExternalStore } from "react";

type Listener = () => void;

const listeners = new Set<Listener>();
let base = "02";
let flash: { id: string; until: number; at: number } | null = null;
let timer: number | undefined;
let snapshot = "02";

function compute() {
  return flash && flash.until > Date.now() ? flash.id : base;
}

function bump() {
  snapshot = compute();
  listeners.forEach((l) => l());
}

/** 设置持续状态；同一状态重复设置不会触发通知。 */
export function setBallBase(id: string) {
  if (base === id) return;
  base = id;
  bump();
}

/** 短暂反馈；同一情绪 4 秒内重复触发会忽略，避免轮询失败刷屏。 */
export function flashBall(id: string, ms = 2200) {
  const now = Date.now();
  if (flash && flash.id === id && now - flash.at < 4000) return;
  flash = { id, until: now + ms, at: now };
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    flash = null;
    bump();
  }, ms);
  bump();
}

const SEARCH_TOOLS = new Set(["Grep", "Glob", "Read", "WebSearch", "WebFetch"]);

type AgentEvent = {
  kind?: string;
  subtype?: string;
  name?: string;
  is_error?: boolean;
  event?: { kind?: string; subtype?: string; name?: string; is_error?: boolean };
};

/** Claude 过程事件 → 表情：归一化块看 kind，system 流事件（task_started /
 *  api_retry / permission_denied …）只有 subtype。null = 这类事件不改表情。 */
export function ballForAgentEvent(raw: AgentEvent): string | null {
  const e = raw.kind ? raw : raw.event || raw;
  const sub = raw.subtype || e.subtype || "";
  if (sub === "thinking_tokens") return "30"; // 思考中
  if (sub === "task_started" || sub === "task_progress") return "32"; // 干活中
  if (sub === "api_retry") return "36"; // 联网重试
  if (sub === "permission_denied") return "38"; // 被拒绝
  if (sub.startsWith("hook_")) return "32"; // hook 执行也是过程动作
  switch (e.kind) {
    case "thinking":
    case "thinking_delta":
      return "30";
    case "text":
    case "text_delta":
      return "39"; // 输出回复
    case "tool_use":
      return SEARCH_TOOLS.has(e.name || "") ? "40" : "32";
    case "tool_result":
      return e.is_error ? "34" : "32";
    case "result":
      return e.is_error ? "34" : "33";
  }
  return null; // usage 与未知系统事件不改表情
}

export function getBallEmotion() {
  return snapshot;
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useBallEmotion() {
  return useSyncExternalStore(subscribe, getBallEmotion, getBallEmotion);
}
