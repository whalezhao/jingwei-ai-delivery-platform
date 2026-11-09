import { useEffect, useState } from "react";
import { api } from "./api";

/** AI 情绪球启用态：和 am.ts 同款，一次请求全站共享，集成中心保存后 refresh 通知。 */
let cache: boolean | null = null;
let inflight: Promise<boolean> | null = null;
const listeners = new Set<(v: boolean) => void>();

function load(): Promise<boolean> {
  inflight ??= api<{ enabled: boolean }>("/api/integrations/ai-ball")
    .then((s) => s.enabled)
    .catch(() => true) // 后端不可达时不隐藏，避免网络抖一下球就消失
    .then((v) => {
      cache = v;
      inflight = null;
      return v;
    });
  return inflight;
}

export function refreshAiBallEnabled() {
  cache = null;
  inflight = null;
  load().then((v) => listeners.forEach((fn) => fn(v)));
}

/** null = 还在加载；调用方先不渲染，避免关掉后又闪一下。 */
export function useAiBallEnabled(): boolean | null {
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
