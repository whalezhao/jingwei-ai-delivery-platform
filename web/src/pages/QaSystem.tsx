import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Plus, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import Page from "../Page";
import { api } from "../api";
import type { MsgKey } from "../i18n";
import {
  dur,
  pct,
  qa,
  type QaBatch,
  type QaEnv,
  type QaFeature,
  type QaModelConfig,
  type QaPreflight,
  type QaSystem,
  type QaVar,
} from "../qa";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import MiniTable from "../ui/MiniTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import SearchSelect from "../ui/SearchSelect";
import Textarea from "../ui/Textarea";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Drawer } from "../components/motion/drawer";
import { Input } from "../components/motion/input";
import type { TableColumn } from "../components/motion/table";
import { Switch } from "../components/motion/switch";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/motion/tabs";

const BATCH_ST: Record<string, [BadgeTone, MsgKey]> = {
  draft: ["neutral", "qa.status.draft"],
  queued: ["neutral", "qa.status.queued"],
  running: ["info", "qa.status.running"],
  completed: ["success", "qa.status.completed"],
  completed_with_failures: ["warning", "qa.status.completed_with_failures"],
  failed: ["danger", "qa.status.failed"],
  cancelled: ["neutral", "qa.status.cancelled"],
};

const TRIGGER: Record<string, MsgKey> = {
  manual: "qa.trigger.manual",
  regression: "qa.trigger.regression",
  rerun: "qa.trigger.rerun",
  requirement: "qa.trigger.requirement",
};


function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const pair = BATCH_ST[status];
  return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : status}</Badge>;
}

/** 功能列表：新建/编辑/删除，点进用例页。 */
function FeaturesTab({ system, requirements }: { system: QaSystem; requirements: any[] }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [rows, setRows] = useState<QaFeature[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [editing, setEditing] = useState<Partial<QaFeature> | null>(null);
  const form = useForm({
    name: "",
    requirement_id: null as number | null,
    description: "",
  });

  const load = async () => {
    try {
      const [fs, cs] = await Promise.all([
        qa<QaFeature[]>(`/api/systems/${system.id}/features`),
        qa<Record<string, number>>(`/api/systems/${system.id}/features/case-counts`),
      ]);
      setRows(fs);
      setCounts(cs);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [system.id]);

  const open = (f?: QaFeature) => {
    setEditing(f ?? { system_id: system.id });
    form.reset({
      name: f?.name ?? "",
      requirement_id: f?.requirement_id ?? null,
      description: f?.description ?? "",
    });
  };

  const save = async () => {
    if (!form.validate({ name: { required: true, message: t("common.required") } })) return;
    const body = {
      name: form.values.name,
      requirement_id: form.values.requirement_id,
      description: form.values.description || null,
    };
    try {
      if (editing?.id) {
        await qa(`/api/features/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        await qa(`/api/systems/${system.id}/features`, {
          method: "POST",
          body: JSON.stringify(body),
        });
      }
      toast.success(t("qa.common.saved"));
      setEditing(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (f: QaFeature) => {
    const ok = await confirm({
      title: t("qa.feature.deleteConfirm"),
      confirmText: t("qa.common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await qa(`/api/features/${f.id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const reqTitle = (id: number | null) =>
    id == null ? "—" : requirements.find((r) => r.id === id)?.title || `#${id}`;

  const columns: DataTableColumn<QaFeature>[] = [
    {
      title: t("qa.common.name"),
      dataIndex: "name",
      render: (v, r) => (
        <button
          type="button"
          onClick={() => nav(`/systems/${system.id}/qa/features/${r.id}`)}
          className="w-full truncate text-left text-primary hover:underline"
        >
          {v}
        </button>
      ),
    },
    {
      title: t("qa.feature.requirement"),
      dataIndex: "requirement_id",
      width: 220,
      render: (v) =>
        v == null ? "—" : <Badge tone="info" className="max-w-full truncate">{reqTitle(v)}</Badge>,
    },
    {
      title: t("qa.feature.caseCount"),
      width: 90,
      render: (_, r) => counts[r.id] ?? 0,
    },
    {
      title: t("qa.common.description"),
      dataIndex: "description",
      render: (v) => <span className="truncate">{v || "—"}</span>,
    },
    {
      title: t("qa.common.actions"),
      width: 200,
      render: (_, r) => (
        <div className="flex items-center gap-2 text-[12px]">
          <button
            type="button"
            onClick={() => nav(`/systems/${system.id}/qa/features/${r.id}`)}
            className="whitespace-nowrap text-primary hover:underline"
          >
            {t("qa.feature.cases")}
          </button>
          <button
            type="button"
            onClick={() => open(r)}
            className="whitespace-nowrap text-primary hover:underline"
          >
            {t("qa.common.edit")}
          </button>
          <button
            type="button"
            onClick={() => remove(r)}
            className="whitespace-nowrap text-destructive hover:underline"
          >
            {t("qa.common.delete")}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button size="sm" className="rounded-lg" onClick={() => open()}>
          <Plus className="size-4" />
          {t("qa.feature.new")}
        </Button>
        <Button variant="outline" size="sm" className="rounded-lg" onClick={load}>
          <RefreshCw className="size-4" />
          {t("qa.common.refresh")}
        </Button>
      </div>
      <DataTable<QaFeature>
        rowKey="id"
        dataSource={rows}
        columns={columns}
        emptyText={t("common.noData")}
      />

      <CenterMorphModal
        open={!!editing}
        onOpenChange={(next) => {
          if (!next) setEditing(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={editing?.id ? t("qa.feature.editTitle") : t("qa.feature.new")}
          className="max-w-[520px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {editing?.id ? t("qa.feature.editTitle") : t("qa.feature.new")}
            </h2>
            <div className="mt-5 flex flex-col gap-4">
              <Input
                label={t("qa.common.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                maxLength={128}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("qa.feature.requirement")}>
                <SearchSelect
                  value={form.values.requirement_id}
                  onChange={(v) => form.set("requirement_id", v == null ? null : Number(v))}
                  clearable
                  placeholder={t("qa.feature.reqNone")}
                  options={requirements.map((r) => ({
                    value: r.id,
                    label: `${r.code} ${r.title}`,
                  }))}
                />
              </Field>
              <Textarea
                label={t("qa.common.description")}
                rows={3}
                value={form.values.description}
                onChange={(v) => form.set("description", v)}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setEditing(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={save}>
                {t("common.save")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </div>
  );
}

/** 执行记录：批次列表。 */
function BatchesTab({ system }: { system: QaSystem }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [rows, setRows] = useState<QaBatch[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const d = await qa<{ items: QaBatch[] }>(`/api/batches?system_id=${system.id}`);
      setRows(d.items);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [system.id]);

  const columns: DataTableColumn<QaBatch>[] = [
    {
      title: t("qa.batch.colName"),
      dataIndex: "name",
      render: (v, r) => (
        <button
          type="button"
          onClick={() => nav(`/qa/batches/${r.id}`)}
          className="w-full truncate text-left text-primary hover:underline"
        >
          {v}
        </button>
      ),
    },
    {
      title: t("qa.batch.trigger"),
      dataIndex: "trigger",
      width: 110,
      render: (v) => <Badge>{TRIGGER[v] ? t(TRIGGER[v]) : v}</Badge>,
    },
    {
      title: t("qa.batch.status"),
      dataIndex: "status",
      width: 130,
      render: (v) => <StatusBadge status={v} />,
    },
    {
      title: t("qa.batch.counts"),
      width: 180,
      render: (_, r) => (
        <span className="font-mono text-[12px]">
          {r.runs} run · {r.passed}/{r.total} · {t("qa.batch.failedN", { n: r.failed })}
        </span>
      ),
    },
    {
      title: t("qa.batch.environment"),
      dataIndex: "environment_name",
      width: 120,
      render: (v) => <span className="truncate">{v || t("qa.env.default")}</span>,
    },
    {
      title: t("qa.batch.created"),
      dataIndex: "created_at",
      width: 170,
      render: (v) => (
        <span className="font-mono text-[12px]">{(v || "").slice(0, 19).replace("T", " ")}</span>
      ),
    },
    {
      title: t("qa.batch.duration"),
      dataIndex: "duration_ms",
      width: 100,
      render: (v) => dur(v),
    },
    {
      title: t("qa.common.actions"),
      width: 100,
      render: (_, r) => (
        <Link to={`/qa/batches/${r.id}`} className="text-primary">
          {t("qa.batch.detail")}
        </Link>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-3">
        <Button variant="outline" size="sm" className="rounded-lg" onClick={load} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          {t("qa.common.refresh")}
        </Button>
      </div>
      <DataTable<QaBatch>
        rowKey="id"
        dataSource={rows}
        columns={columns}
        loading={loading}
        emptyText={t("common.noData")}
      />
    </div>
  );
}

/** 统计：指标卡 + 趋势/失败/不稳定/成本表，不做图表。 */
function StatsTab({ system }: { system: QaSystem }) {
  const { t } = useTranslation();
  const [overview, setOverview] = useState<any>(null);
  const [trend, setTrend] = useState<any[]>([]);
  const [failures, setFailures] = useState<any[]>([]);
  const [flaky, setFlaky] = useState<any[]>([]);
  const [cost, setCost] = useState<any>(null);

  const load = async () => {
    const q = `system_id=${system.id}`;
    try {
      const [o, tr, f, fl, c] = await Promise.all([
        qa(`/api/stats/overview?${q}`),
        qa<any[]>(`/api/stats/trend?${q}&days=30`),
        qa<any[]>(`/api/stats/failures?${q}&days=30`),
        qa<any[]>(`/api/stats/flaky?${q}`),
        qa(`/api/stats/cost?${q}&days=30`),
      ]);
      setOverview(o);
      setTrend(tr);
      setFailures(f);
      setFlaky(fl);
      setCost(c);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [system.id]);

  const kpis: [string, string | number][] = [
    [t("qa.stats.runs"), overview?.runs ?? 0],
    [t("qa.stats.cases"), overview?.sample_size ?? 0],
    [t("qa.stats.passRate"), pct(overview?.final_pass_rate)],
    [t("qa.stats.avgDuration"), dur(overview?.avg_duration_ms)],
  ];

  const trendCols: TableColumn<any>[] = [
    { key: "bucket", header: t("qa.stats.day"), cell: (r) => (r.bucket || "").slice(0, 10) },
    { key: "runs", header: t("qa.stats.runs") },
    { key: "total", header: t("qa.stats.cases") },
    { key: "passed", header: t("qa.stats.passed") },
    { key: "failed", header: t("qa.stats.failed") },
    { key: "pass_rate", header: t("qa.stats.passRate"), cell: (r) => pct(r.pass_rate) },
  ];
  const failureCols: TableColumn<any>[] = [
    { key: "case_name", header: t("qa.stats.caseName"), cell: (r) => <span className="block truncate">{r.case_name}</span> },
    { key: "feature_name", header: t("qa.stats.feature"), width: "120px", cell: (r) => <span className="block truncate">{r.feature_name}</span> },
    { key: "failures", header: t("qa.stats.failed"), width: "70px" },
  ];
  const flakyCols: TableColumn<any>[] = [
    { key: "case_name", header: t("qa.stats.caseName"), cell: (r) => <span className="block truncate">{r.case_name}</span> },
    { key: "runs", header: t("qa.stats.runs"), width: "70px" },
    { key: "failed", header: t("qa.stats.failed"), width: "70px" },
    { key: "flakiness", header: t("qa.stats.flakiness"), width: "90px", cell: (r) => pct(r.flakiness) },
  ];
  const costCols: TableColumn<any>[] = [
    { key: "model", header: t("qa.stats.model"), cell: (r) => r.model || "—" },
    { key: "runs", header: t("qa.stats.runs"), width: "80px" },
    { key: "total_tokens", header: t("qa.stats.tokens"), width: "140px" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-4 rounded-xl border border-border bg-card p-4 lg:grid-cols-4">
        {kpis.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <div className="truncate text-xs text-muted-foreground">{label}</div>
            <div className="mt-0.5 text-[22px] font-semibold tabular-nums leading-tight text-foreground">
              {value}
            </div>
          </div>
        ))}
      </div>
      <section>
        <h3 className="mb-2 mt-0 text-[13px] font-semibold text-foreground">{t("qa.stats.trend")}</h3>
        <MiniTable
          data={trend}
          columns={trendCols}
          rowKey={(r) => r.bucket || ""}
          emptyText={t("common.noData")}
        />
      </section>
      <div className="grid gap-4 lg:grid-cols-2">
        <section>
          <h3 className="mb-2 mt-0 text-[13px] font-semibold text-foreground">{t("qa.stats.failures")}</h3>
          <MiniTable data={failures} columns={failureCols} rowKey="test_case_id" emptyText={t("common.noData")} />
        </section>
        <section>
          <h3 className="mb-2 mt-0 text-[13px] font-semibold text-foreground">{t("qa.stats.flaky")}</h3>
          <MiniTable data={flaky} columns={flakyCols} rowKey="test_case_id" emptyText={t("common.noData")} />
        </section>
      </div>
      <section>
        <h3 className="mb-2 mt-0 text-[13px] font-semibold text-foreground">{t("qa.stats.cost")}</h3>
        <MiniTable
          data={(cost?.by_model || []) as any[]}
          columns={costCols}
          rowKey={(r) => r.model || "unknown"}
          emptyText={t("common.noData")}
        />
      </section>
    </div>
  );
}

type VarRow = QaVar & { uid: string };

// 变量行没有稳定 id（新增行 id 为空），用模块级序号作 key
let varUid = 0;
const nextUid = () => `var-${++varUid}`;

/** 配置：环境（含变量）+ 模型配置 + 预检。 */
function ConfigTab({ system }: { system: QaSystem }) {
  const { t } = useTranslation();
  const [envs, setEnvs] = useState<QaEnv[]>([]);
  const [envEditing, setEnvEditing] = useState<Partial<QaEnv> | null>(null);
  const envForm = useForm({ name: "", base_url: "" });
  const [varEnv, setVarEnv] = useState<QaEnv | null>(null);
  const [varRows, setVarRows] = useState<VarRow[]>([]);
  const [cleared, setCleared] = useState<string[]>([]);
  const [model, setModel] = useState<QaModelConfig | null>(null);
  const [preflight, setPreflight] = useState<QaPreflight | null>(null);
  const [testing, setTesting] = useState(false);
  const modelForm = useForm({
    model_name: "",
    base_url: "",
    api_key: "",
    reasoning: "",
    token_quota: "",
    clear_api_key: false,
    init_config_json: "",
  });
  
  const load = async () => {
    try {
      const [es, mc] = await Promise.all([
        qa<QaEnv[]>(`/api/systems/${system.id}/environments`),
        qa<QaModelConfig>(`/api/systems/${system.id}/model-config`),
      ]);
      setEnvs(es);
      setModel(mc);
      modelForm.reset({
        model_name: mc.model_name || "",
        base_url: mc.base_url || "",
        api_key: "",
        init_config_json: mc.init_config_json || "",
        reasoning:
          mc.reasoning_enabled == null ? "" : mc.reasoning_enabled ? "true" : "false",
        token_quota: mc.token_quota == null ? "" : String(mc.token_quota),
        clear_api_key: false,
      });
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [system.id]);

  const openEnv = (e?: QaEnv) => {
    setEnvEditing(e ?? { system_id: system.id });
    envForm.reset({ name: e?.name ?? "", base_url: e?.base_url ?? "" });
  };

  const saveEnv = async () => {
    if (!envEditing?.id && !envForm.values.name.trim()) return;
    const body = { name: envForm.values.name, base_url: envForm.values.base_url || null };
    try {
      if (envEditing?.id) {
        await qa(`/api/environments/${envEditing.id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
      } else {
        await qa(`/api/systems/${system.id}/environments`, {
          method: "POST",
          body: JSON.stringify(body),
        });
      }
      toast.success(t("qa.common.saved"));
      setEnvEditing(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const removeEnv = async (e: QaEnv) => {
    const ok = await confirm({
      title: t("qa.env.deleteConfirm"),
      confirmText: t("qa.common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await qa(`/api/environments/${e.id}`, { method: "DELETE" });
      load();
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  const openVars = (e: QaEnv) => {
    setVarEnv(e);
    setVarRows(e.variables.map((v) => ({ ...v, value: "", uid: nextUid() })));
    setCleared([]);
  };

  const saveVars = async () => {
    if (!varEnv) return;
    try {
      await qa(`/api/environments/${varEnv.id}/variables`, {
        method: "PUT",
        body: JSON.stringify({
          variables: [
            ...varRows
              .filter((v) => v.key.trim())
              .map((v) => ({
                key: v.key.trim(),
                value: v.value || null,
                is_secret: v.is_secret,
                description: v.description,
              })),
            ...cleared.map((key) => ({ key, clear: true })),
          ],
        }),
      });
      toast.success(t("qa.common.saved"));
      setVarEnv(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const patchVar = (uid: string, patch: Partial<VarRow>) =>
    setVarRows((rs) => rs.map((x) => (x.uid === uid ? { ...x, ...patch } : x)));

  const saveModel = async () => {
    const v = modelForm.values;
    const body: any = {
      model_name: v.model_name || null,
      base_url: v.base_url || null,
      init_config_json: v.init_config_json || null,
    };
    if (v.api_key) body.api_key = v.api_key;
    if (v.clear_api_key) body.clear_api_key = true;
    if (v.reasoning === "") body.clear_reasoning = true;
    else body.reasoning_enabled = v.reasoning === "true";
    if (v.token_quota === "") body.clear_token_quota = true;
    else body.token_quota = Number(v.token_quota);
    try {
      const mc = await qa<QaModelConfig>(`/api/systems/${system.id}/model-config`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setModel(mc);
      modelForm.set("api_key", "");
      modelForm.set("clear_api_key", false);
      toast.success(t("qa.common.saved"));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const testModel = async () => {
    setTesting(true);
    try {
      const r = await qa<{ ok: boolean; message: string; latency_ms: number }>(
        `/api/systems/${system.id}/model-config/test`,
        { method: "POST", body: JSON.stringify({}) },
      );
      r.ok
        ? toast.success(`${r.message}（${r.latency_ms}ms）`)
        : toast.error(r.message);
      load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setTesting(false);
    }
  };

  const runPreflight = async () => {
    try {
      setPreflight(await qa<QaPreflight>(`/api/systems/${system.id}/preflight`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const envColumns: DataTableColumn<QaEnv>[] = [
    {
      title: t("qa.common.name"),
      dataIndex: "name",
      render: (v, r) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate">{v}</span>
          {r.is_default && <Badge tone="info">{t("qa.env.default")}</Badge>}
        </span>
      ),
    },
    {
      title: t("qa.env.baseUrl"),
      dataIndex: "base_url",
      render: (v) => <span className="truncate font-mono text-[12px]">{v || "—"}</span>,
    },
    {
      title: t("qa.env.vars"),
      width: 90,
      render: (_, r) => r.variables.length,
    },
    {
      title: t("qa.common.actions"),
      width: 220,
      render: (_, r) => (
        <div className="flex items-center gap-2 text-[12px]">
          <button
            type="button"
            onClick={() => openVars(r)}
            className="whitespace-nowrap text-primary hover:underline"
          >
            {t("qa.env.vars")}
          </button>
          <button
            type="button"
            onClick={() => openEnv(r)}
            className="whitespace-nowrap text-primary hover:underline"
          >
            {t("qa.common.edit")}
          </button>
          {!r.is_default && (
            <button
              type="button"
              onClick={() => removeEnv(r)}
              className="whitespace-nowrap text-destructive hover:underline"
            >
              {t("qa.common.delete")}
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button size="sm" className="rounded-lg" onClick={() => openEnv()}>
            <Plus className="size-4" />
            {t("qa.env.new")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={load}>
            <RefreshCw className="size-4" />
            {t("qa.common.refresh")}
          </Button>
        </div>
        <DataTable<QaEnv>
          rowKey="id"
          dataSource={envs}
          columns={envColumns}
          emptyText={t("common.noData")}
        />
      </section>

      <section className="rounded-xl border border-border bg-card p-4">
        <h3 className="m-0 mb-3 text-[15px] font-semibold text-foreground">{t("qa.model.title")}</h3>
        {model && !model.configured && (
          <div className="mb-3 rounded-lg bg-warning/10 px-3 py-2 text-[13px] text-warning">
            {t("qa.model.missing", { fields: model.missing.join("、") })}
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-3">
          <Input
            label={t("qa.model.modelName")}
            value={modelForm.values.model_name}
            onChange={(v) => modelForm.set("model_name", v)}
            placeholder="gpt-4o / qwen-vl-max"
            classNames={{ field: "rounded-lg" }}
          />
          <Input
            label={t("qa.model.baseUrl")}
            value={modelForm.values.base_url}
            onChange={(v) => modelForm.set("base_url", v)}
            placeholder="https://api.example.com/v1"
            classNames={{ field: "rounded-lg" }}
          />
          <Input
            type="password"
            label={t("qa.model.apiKey")}
            value={modelForm.values.api_key}
            onChange={(v) => modelForm.set("api_key", v)}
            placeholder={model?.api_key_set ? t("qa.model.apiKeySet") : ""}
            autoComplete="new-password"
            classNames={{ field: "rounded-lg" }}
          />
          <Field label={t("qa.model.reasoning")}>
            <SearchSelect
              value={modelForm.values.reasoning}
              onChange={(v) => modelForm.set("reasoning", v == null ? "" : String(v))}
              options={[
                { value: "", label: t("qa.model.reasoningFollow") },
                { value: "true", label: t("qa.common.on") },
                { value: "false", label: t("qa.common.off") },
              ]}
            />
          </Field>
          <Input
            type="number"
            label={t("qa.model.tokenQuota")}
            value={modelForm.values.token_quota}
            onChange={(v) => modelForm.set("token_quota", v)}
            placeholder={t("qa.model.quotaNone")}
            classNames={{ field: "rounded-lg" }}
          />
          <Field label={t("qa.model.clearApiKey")}>
            <span className="flex h-9 items-center">
              <Switch
                checked={!!modelForm.values.clear_api_key}
                onCheckedChange={(v) => modelForm.set("clear_api_key", v)}
              />
            </span>
          </Field>
        </div>
        <Textarea
          label={t("qa.model.initConfig")}
          rows={2}
          className="mt-3 [&_textarea]:font-mono [&_textarea]:text-[12px]"
          value={modelForm.values.init_config_json}
          onChange={(v) => modelForm.set("init_config_json", v)}
          placeholder='{"temperature": 0}'
        />
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button size="sm" className="rounded-lg" onClick={saveModel}>
            {t("qa.common.save")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={testModel} disabled={testing}>
            {t("qa.model.testConnection")}
          </Button>
          <span className="text-xs text-muted-foreground">
            {t("qa.model.monthUsed", { n: model?.month_tokens_used ?? 0 })}
          </span>
        </div>
      </section>

      <section className="rounded-xl border border-border bg-card p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" className="rounded-lg" onClick={runPreflight}>
            {t("qa.preflight.run")}
          </Button>
          {preflight && (
            <Badge tone={preflight.can_run ? "success" : "danger"}>
              {preflight.can_run ? t("qa.preflight.canRun") : t("qa.preflight.cannotRun")}
            </Badge>
          )}
        </div>
        {preflight?.checks.map((c) => (
          <div
            key={c.key}
            className={`mb-2 rounded-lg px-3 py-2 text-[13px] last:mb-0 ${
              c.ok
                ? "bg-success/10 text-success"
                : c.severity === "error"
                  ? "bg-danger/10 text-danger"
                  : "bg-warning/10 text-warning"
            }`}
          >
            {c.message}
          </div>
        ))}
      </section>

      <CenterMorphModal
        open={!!envEditing}
        onOpenChange={(next) => {
          if (!next) setEnvEditing(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={envEditing?.id ? t("qa.env.editTitle") : t("qa.env.new")}
          className="max-w-[480px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {envEditing?.id ? t("qa.env.editTitle") : t("qa.env.new")}
            </h2>
            <div className="mt-5 flex flex-col gap-4">
              <Input
                label={t("qa.common.name")}
                value={envForm.values.name}
                onChange={(v) => envForm.set("name", v)}
                disabled={!!envEditing?.is_default}
                maxLength={64}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("qa.env.baseUrl")}
                value={envForm.values.base_url}
                onChange={(v) => envForm.set("base_url", v)}
                placeholder="https://test.example.com"
                classNames={{ field: "rounded-lg" }}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setEnvEditing(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={saveEnv}>
                {t("common.save")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>

      <Drawer
        open={!!varEnv}
        onOpenChange={(next) => {
          if (!next) setVarEnv(null);
        }}
        ariaLabel={`${t("qa.env.vars")} · ${varEnv?.name || ""}`}
        className="w-[min(760px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("qa.env.vars")} · {varEnv?.name || ""}
            </h3>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() =>
                  setVarRows((rs) => [
                    ...rs,
                    {
                      id: "",
                      key: "",
                      value: "",
                      is_secret: false,
                      value_set: false,
                      description: null,
                      uid: nextUid(),
                    },
                  ])
                }
              >
                {t("qa.env.addVar")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={saveVars}>
                {t("common.save")}
              </Button>
              <button
                type="button"
                aria-label={t("common.close")}
                onClick={() => setVarEnv(null)}
                className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="mb-1 grid grid-cols-[170px_1fr_64px_48px] items-center gap-2 text-xs text-muted-foreground">
              <span>{t("qa.env.varKey")}</span>
              <span>{t("qa.env.varValue")}</span>
              <span>{t("qa.env.varSecret")}</span>
              <span />
            </div>
            {varRows.map((r) => (
              <div key={r.uid} className="mb-2 grid grid-cols-[170px_1fr_64px_48px] items-center gap-2">
                <Input
                  value={r.key}
                  onChange={(v) => patchVar(r.uid, { key: v })}
                  disabled={!!r.id}
                  classNames={{ field: "rounded-lg font-mono text-[12px]" }}
                />
                <Input
                  type="password"
                  value={r.id && r.is_secret ? "" : r.value || ""}
                  onChange={(v) => patchVar(r.uid, { value: v })}
                  placeholder={r.id && r.is_secret && r.value_set ? t("qa.env.varSet") : ""}
                  classNames={{ field: "rounded-lg" }}
                />
                <span className="flex justify-center">
                  <Switch
                    checked={r.is_secret}
                    onCheckedChange={(v) => patchVar(r.uid, { is_secret: v })}
                  />
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (r.id) setCleared((cs) => [...cs, r.key]);
                    setVarRows((rs) => rs.filter((x) => x.uid !== r.uid));
                  }}
                  className="text-[12px] text-destructive hover:underline"
                >
                  {t("qa.common.delete")}
                </button>
              </div>
            ))}
            <div className="mt-2 text-xs text-muted-foreground">{t("qa.env.secretHint")}</div>
          </div>
        </div>
      </Drawer>
    </div>
  );
}

export default function QaSystemPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const systemId = Number(id);
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "features";
  const [system, setSystem] = useState<QaSystem | null>(null);
  const [requirements, setRequirements] = useState<any[]>([]);

  useEffect(() => {
    qa<QaSystem>(`/api/systems/${systemId}`).then(setSystem).catch((e) => toast.error(e.message));
    api<any[]>(`/api/requirements?system_id=${systemId}`)
      .then(setRequirements)
      .catch(() => setRequirements([]));
  }, [systemId]);

  if (!system) return null;

  return (
    <Page
      title={`${t("qa.title")} · ${system.name}`}
      desc={system.path}
      actions={
        <Link to="/systems" className="text-[13px] text-primary hover:underline">
          {t("qa.backSystems")}
        </Link>
      }
    >
      <Tabs
        value={tab}
        onValueChange={(k) => setSp({ tab: k }, { replace: true })}
        variant="segment"
      >
        <TabsList className="mb-3">
          <TabsTrigger value="features">{t("qa.tabs.features")}</TabsTrigger>
          <TabsTrigger value="batches">{t("qa.tabs.execution")}</TabsTrigger>
          <TabsTrigger value="stats">{t("qa.tabs.stats")}</TabsTrigger>
          <TabsTrigger value="config">{t("qa.tabs.config")}</TabsTrigger>
        </TabsList>
        <TabsContent value="features">
          <FeaturesTab system={system} requirements={requirements} />
        </TabsContent>
        <TabsContent value="batches">
          <BatchesTab system={system} />
        </TabsContent>
        <TabsContent value="stats">
          <StatsTab system={system} />
        </TabsContent>
        <TabsContent value="config">
          <ConfigTab system={system} />
        </TabsContent>
      </Tabs>
    </Page>
  );
}