/** API 客户端：token 存 localStorage，401 一律踢回登录页。 */

import i18n from "./i18n";
import { flashBall } from "./ball";

const KEY = "vega_token";

export const token = {
  get: () => localStorage.getItem(KEY) || "",
  set: (t: string) => localStorage.setItem(KEY, t),
  clear: () => localStorage.removeItem(KEY),
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, msg: string) {
    super(msg);
    this.status = status;
  }
}

export function goLogin() {
  if ((window as any).VEGA_HASH_ROUTER) location.hash = "#/login";
  else location.assign("/login");
}

/** 通知 App 重估登录态（token 不是 React state，得靠事件触发重渲染）。 */
export const AUTH_EVENT = "vega-auth";
export const pingAuth = () => window.dispatchEvent(new Event(AUTH_EVENT));

/** 请求结果 → 表情球反馈（情绪 ID 对照见 ball.ts / grok-ball README）。 */
function flashHttp(status: number) {
  if (status === 401 || status === 403) flashBall("38", 2600);
  else if (status === 404) flashBall("11", 2200);
  else if (status === 422) flashBall("20", 2200);
  else if (status === 400 || status === 409) flashBall("18", 2200);
  else if (status >= 500) flashBall("21", 2600);
  else flashBall("34", 2200);
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  if (token.get()) headers["Authorization"] = `Bearer ${token.get()}`;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  let r: Response;
  try {
    r = await fetch(path, { ...opts, headers });
  } catch (e) {
    flashBall("17", 2600); // 网络层失败：慌张
    throw e;
  }
  if (r.status === 401 && !path.startsWith("/api/auth/login")) {
    flashBall("38", 2600);
    token.clear();
    goLogin();
    throw new ApiError(401, i18n.t("errors.notLoggedIn"));
  }
  if (!r.ok) {
    flashHttp(r.status);
    let msg = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      msg = d.detail || msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(r.status, msg);
  }
  // AI 会话的 POST 是发消息，后续状态由 SSE 驱动，别让「开心」盖住「思考中」
  const method = (opts.method || "GET").toUpperCase();
  if (!path.includes("/plan/")) {
    if (method === "POST") flashBall("10", 1800); // 新建/提交：开心
    else if (method !== "GET" && method !== "HEAD") flashBall("19", 1800); // 修改/删除：满意
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export const isAdmin = () => localStorage.getItem("vega_admin") === "1";

export async function login(username: string, password: string) {
  const d = await api<any>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  token.set(d.token);
  localStorage.setItem("vega_admin", d.is_admin ? "1" : "0");
  localStorage.setItem("vega_me", String(d.person_id));
  localStorage.setItem("vega_user", username);
  pingAuth();
  return d;
}
