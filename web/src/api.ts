/** API 客户端：token 存 localStorage，401 一律踢回登录页。 */

import i18n from "./i18n";

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

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  if (token.get()) headers["Authorization"] = `Bearer ${token.get()}`;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401 && !path.startsWith("/api/auth/login")) {
    token.clear();
    goLogin();
    throw new ApiError(401, i18n.t("errors.notLoggedIn"));
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      msg = d.detail || msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(r.status, msg);
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
