/** QA（aster 测试后台）客户端：独立 origin，带同一个 vega_token，401 回登录页。 */

import i18n from "./i18n";
import { goLogin, token } from "./api";

const BASE = (
  import.meta.env.VITE_QA_API_BASE || (import.meta.env.DEV ? "http://localhost:8100" : "")
).replace(/\/+$/, "");

export class QaError extends Error {
  status: number;
  constructor(status: number, msg: string) {
    super(msg);
    this.status = status;
  }
}

export async function qa<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  if (token.get()) headers["Authorization"] = `Bearer ${token.get()}`;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  let r: Response;
  try {
    r = await fetch(BASE + path, { ...opts, headers });
  } catch {
    throw new QaError(0, i18n.t("qa.unreachable"));
  }
  if (r.status === 401) {
    token.clear();
    goLogin();
    throw new QaError(401, i18n.t("errors.notLoggedIn"));
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      msg = d.detail || msg;
    } catch {
      /* ignore */
    }
    throw new QaError(r.status, msg);
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

/** 读 SSE：后端发 `event: delta|done|error` + 一行 JSON。 */
export async function qaStream(
  path: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token.get()) headers["Authorization"] = `Bearer ${token.get()}`;
  const r = await fetch(BASE + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (r.status === 401) {
    token.clear();
    goLogin();
    throw new QaError(401, i18n.t("errors.notLoggedIn"));
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      msg = (await r.json()).detail || msg;
    } catch {
      /* ignore */
    }
    throw new QaError(r.status, msg);
  }
  const reader = r.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          /* 残缺帧直接丢，下一帧还能用 */
        }
      }
      cut = buffer.indexOf("\n\n");
    }
  }
}

export interface QaSystem {
  id: number;
  group_id: number;
  name: string;
  path: string;
  has_model_config: boolean;
  model_health: {
    status: string;
    model_name: string | null;
    message: string | null;
    latency_ms: number | null;
    source: string;
    checked_at: string | null;
  } | null;
}

export interface QaFeature {
  id: string;
  system_id: number;
  name: string;
  description: string | null;
  requirement_id: number | null;
  stage: string;
  released_at: string | null;
}

export interface QaCase {
  id: string;
  feature_id: string;
  name: string;
  description: string | null;
  yaml_content: string;
  priority: string | null;
  defect_ids: string | null;
  stage_override: string | null;
  is_active: boolean;
  effective_stage: string;
  system_id: number | null;
  version_no: number | null;
}

export interface QaCaseVersion {
  id: string;
  version_no: number;
  name: string;
  yaml_content: string;
  priority: string | null;
  is_active: boolean;
  change_source: string;
  change_note: string | null;
  created_at: string;
}

export interface QaVar {
  id: string;
  key: string;
  value: string | null;
  is_secret: boolean;
  value_set: boolean;
  description: string | null;
}

export interface QaEnv {
  id: string;
  project_id: number;
  name: string;
  base_url: string | null;
  is_default: boolean;
  variables: QaVar[];
}

export interface QaModelConfig {
  configured: boolean;
  missing: string[];
  model_name: string | null;
  model_family: string | null;
  base_url: string | null;
  init_config_json: string | null;
  api_key_set: boolean;
  reasoning_enabled: boolean | null;
  token_quota: number | null;
  month_tokens_used: number;
  model_health: QaSystem["model_health"];
}

export interface QaBatch {
  id: string;
  task_id: string | null;
  task_name: string | null;
  task_type: string | null;
  task_status: string | null;
  task_feature_count: number;
  task_case_count: number;
  ai_status: string | null;
  ai_verdict: string | null;
  system_id: number;
  name: string;
  trigger: string;
  environment_name: string | null;
  pinned: boolean;
  parent_batch_id: string | null;
  created_at: string;
  created_by_name: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number;
  runs: number;
  total: number;
  passed: number;
  failed: number;
  excluded_runs: number;
  status: string;
  features: { feature_id: string; name: string; stage: string; total: number; passed: number; failed: number }[];
}

export interface QaTask {
  id: string;
  system_id: number;
  system_name: string | null;
  group_id: number | null;
  requirement_id: number | null;
  requirement_code: string | null;
  requirement_title: string | null;
  name: string;
  type: string;
  description: string | null;
  status: string;
  retry_count: number;
  environment_name: string | null;
  case_selection_mode: string;
  created_by: number | null;
  created_by_name: string | null;
  created_at: string | null;
  feature_count: number;
  case_count: number;
  last_batch_id: string | null;
  last_batch_status: string | null;
  last_run_at: string | null;
}

export interface QaTaskList {
  items: QaTask[];
  total: number;
}

export interface QaRunItem {
  id: string;
  system_id: number | null;
  system_name: string | null;
  status: string;
  total: number | null;
  passed: number | null;
  failed: number | null;
  duration_ms: number | null;
  model_name: string | null;
  failure_kind: string | null;
  environment_name: string | null;
  report_available: boolean;
  started_at: string | null;
  shard_index: number | null;
  shard_count: number | null;
}

export interface QaBatchDetail extends QaBatch {
  run_items: QaRunItem[];
}

export interface QaSummary {
  requirement_id: number;
  status: string;
  task_id: string | null;
  batch_id: string | null;
  total: number;
  passed: number;
  failed: number;
  not_run: number;
  running: number;
  pass_rate: number | null;
  started_at: string | null;
  ended_at: string | null;
  updated_at: string | null;
}

/** 需求卡里的执行记录（run 维度） */
export interface QaTestRun {
  run_id: string;
  batch_id: string;
  task_id: string;
  name: string | null;
  trigger: string | null;
  environment_name: string | null;
  status: string;
  total: number;
  passed: number;
  failed: number;
  created_at: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface QaWorkerStatus {
  workers: {
    id: string;
    hostname: string | null;
    pid: number | null;
    slots: number;
    started_at: string | null;
    heartbeat_at: string | null;
    age_seconds: number;
    active_runs: number;
  }[];
  stale_workers: number;
  queued_runs: number;
  active_runs: number;
  max_concurrent_runs: number;
  worker_stale_seconds: number;
  avg_duration_ms: number | null;
  oldest_queue_seconds: number | null;
}

export interface QaPreflight {
  checks: {
    key: string;
    ok: boolean;
    severity: string;
    message: string;
    overridable: boolean;
    system_id: number | null;
    system_name: string | null;
  }[];
  can_run: boolean;
  environment_name: string;
}

/** 百分比展示：0.8734 → 87.3%；null → — */
export const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

/** 毫秒 → 1m 23s / 12.4s */
export const dur = (ms: number | null | undefined) => {
  if (ms == null || ms <= 0) return "—";
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  return `${m}m ${Math.round((ms % 60_000) / 1000)}s`;
};