import { useEffect, useState } from "react";
import { api } from "./api";

/** agent-memory 启用态：一次请求全站共享，集成中心保存后 refresh 通知已挂载页面。 */
let cache: boolean | null = null;
let inflight: Promise<boolean> | null = null;
const listeners = new Set<(v: boolean) => void>();

function load(): Promise<boolean> {
  inflight ??= api<{ enabled: boolean }>("/api/am/status")
    .then((s) => s.enabled)
    .catch(() => false)
    .then((v) => {
      cache = v;
      inflight = null;
      return v;
    });
  return inflight;
}

export function refreshAmEnabled() {
  cache = null;
  inflight = null;
  load().then((v) => listeners.forEach((fn) => fn(v)));
}

/** null = 还在加载；调用方据此先隐藏，避免入口闪一下又消失。 */
export function useAmEnabled(): boolean | null {
  const [v, setV] = useState<boolean | null>(cache);
  useEffect(() => {
    listeners.add(setV);
    if (cache == null) load().then(setV);
    return () => {
      listeners.delete(setV);
    };
  }, []);
  return v;
}
