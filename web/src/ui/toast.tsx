import { useSyncExternalStore, type ReactNode } from "react";
import {
  AnimatedToastStack,
  type AnimatedToast,
  type ToastStatus,
} from "../components/motion/animated-toast-stack";

/**
 * 模块级 toast：让任意页面直接 import { toast } 使用（antd message 的替代），
 * <Toaster/> 在 Shell 里挂一次。数组不可变替换，满足 useSyncExternalStore 的快照约定。
 */
let items: AnimatedToast[] = [];
let seed = 0;
const listeners = new Set<() => void>();
const timers = new Map<string, number>();

const emit = () => listeners.forEach((l) => l());

function dismiss(id: string) {
  const timer = timers.get(id);
  if (timer) window.clearTimeout(timer);
  timers.delete(id);
  items = items.filter((t) => t.id !== id);
  emit();
}

function show(
  status: ToastStatus,
  title: ReactNode,
  description?: ReactNode,
  duration = 4200,
) {
  const id = `toast-${Date.now()}-${seed++}`;
  items = [...items, { id, title, description, status, duration }];
  emit();
  if (duration > 0) timers.set(id, window.setTimeout(() => dismiss(id), duration));
  return id;
}

export const toast = {
  success: (title: ReactNode, description?: ReactNode) =>
    show("success", title, description),
  error: (title: ReactNode, description?: ReactNode) =>
    show("error", title, description),
  info: (title: ReactNode, description?: ReactNode) =>
    show("info", title, description),
  warning: (title: ReactNode, description?: ReactNode) =>
    show("warning", title, description),
  dismiss,
};

export function Toaster() {
  const toasts = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => items,
  );
  return (
    <AnimatedToastStack
      toasts={toasts}
      onDismiss={dismiss}
      position="bottom-right"
      fixed
    />
  );
}