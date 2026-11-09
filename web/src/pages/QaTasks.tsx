import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, RefreshCw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import Page from "../Page";
import { useGroup } from "../group";
import { qa, type QaFeature, type QaTask, type QaTaskList } from "../qa";
import type { MsgKey } from "../i18n";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import SearchSelect from "../ui/SearchSelect";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Input } from "../components/motion/input";
import {
  MultiSelect,
  MultiSelectContent,
  MultiSelectEmpty,
  MultiSelectGroup,
  MultiSelectInput,
  MultiSelectItem,
  MultiSelectLabel,
  MultiSelectList,
  MultiSelectTrigger,
  MultiSelectValue,
} from "../components/motion/multi-select";
import { Switch } from "../components/motion/switch";

const TYPES = ["case_run", "feature_regression", "requirement_test", "adhoc"] as const;
const CREATE_TYPES = ["case_run", "feature_regression", "adhoc"] as const;

type FeatureOption = QaFeature & { system_name: string; can_manage: boolean };

const PAGE_SIZE = 20;

const STATUS: Record<string, [BadgeTone, MsgKey]> = {
  draft: ["neutral", "qa.status.draft"],
  queued: ["neutral", "qa.status.queued"],
  running: ["info", "qa.status.running"],
  completed: ["success", "qa.status.completed"],
  completed_with_failures: ["warning", "qa.status.completed_with_failures"],
  failed: ["danger", "qa.status.failed"],
  cancelled: ["neutral", "qa.status.cancelled"],
};

const when = (v: string | null) => (v ? v.slice(0, 19).replace("T", " ") : "—");

// 与后端 qa.app.environments.DEFAULT_ENVIRONMENT 一致；空环境名也会落到它
const DEFAULT_ENV = "默认";

const emptyForm = {
  name: "",
  type: "case_run",
  feature_ids: [] as string[],
  environment_name: DEFAULT_ENV,
  run_now: false,
};

export default function QaTasks() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { groupId, projectId } = useGroup();
  const [rows, setRows] = useState<QaTask[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [type, setType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [features, setFeatures] = useState<FeatureOption[]>([]);
  const [envNames, setEnvNames] = useState<string[]>([]);
  const form = useForm(emptyForm);

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("project_id", String(projectId));
    if (type) p.set("type", type);
    if (status) p.set("status", status);
    if (keyword.trim()) p.set("q", keyword.trim());
    p.set("limit", String(PAGE_SIZE));
    p.set("offset", String((page - 1) * PAGE_SIZE));
    setLoading(true);
    try {
      const data = await qa<QaTaskList>(`/api/tasks?${p}`);
      setRows(data.items);
      setTotal(data.total);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [groupId, projectId, type, status, keyword, page]);

  // 全局筛选变了就回第一页，避免停在越界的页码上
  useEffect(() => {
    setPage(1);
  }, [groupId, projectId]);

  const openCreate = async () => {
    form.reset(emptyForm);
    setEnvNames([]);
    setCreating(true);
    try {
      setFeatures(await qa<FeatureOption[]>("/api/features"));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  // 环境名取所选功能所属系统的交集：后端要求每个系统都有同名环境，缺一个就整体拒绝
  useEffect(() => {
    if (!creating) return;
    const systemIds = [
      ...new Set(
        features
          .filter((f) => form.values.feature_ids.includes(f.id))
          .map((f) => f.system_id),
      ),
    ];
    if (systemIds.length === 0) {
      setEnvNames([]);
      return;
    }
    let alive = true;
    Promise.all(
      systemIds.map((sid) => qa<{ name: string }[]>(`/api/systems/${sid}/environments`)),
    )
      .then((lists) => {
        if (!alive) return;
        const common = lists
          .slice(1)
          .reduce(
            (acc, list) => acc.filter((n) => list.some((e) => e.name === n)),
            lists[0].map((e) => e.name),
          );
        setEnvNames(common.filter((n) => n !== DEFAULT_ENV));
      })
      .catch(() => {
        if (alive) setEnvNames([]);
      });
    return () => {
      alive = false;
    };
  }, [creating, features, form.values.feature_ids]);

  // 换掉功能后，原来选的环境可能不再覆盖所有系统，回退到默认环境
  useEffect(() => {
    if (form.values.environment_name !== DEFAULT_ENV && !envNames.includes(form.values.environment_name)) {
      form.set("environment_name", DEFAULT_ENV);
    }
  }, [envNames]);

  const create = async () => {
    if (
      !form.validate({
        name: { required: true, message: t("qa.tasks.nameRequired") },
        feature_ids: { required: true, message: t("qa.tasks.featureRequired") },
      })
    ) {
      return;
    }
    const name = form.values.name.trim();
    if (!name) {
      toast.error(t("qa.tasks.nameRequired"));
      return;
    }
    setSaving(true);
    try {
      await qa("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ ...form.values, name }),
      });
      toast.success(t("qa.tasks.created"));
      setCreating(false);
      if (page === 1) load();
      else setPage(1);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const bySystem = new Map<string, FeatureOption[]>();
  for (const feature of features) {
    const list = bySystem.get(feature.system_name) ?? [];
    list.push(feature);
    bySystem.set(feature.system_name, list);
  }

  const columns: DataTableColumn<QaTask>[] = [
    {
      title: t("qa.common.name"),
      dataIndex: "name",
      width: 240,
      render: (v, r) =>
        r.last_batch_id ? (
          <button
            type="button"
            onClick={() => nav(`/qa/batches/${r.last_batch_id}`)}
            className="w-full truncate text-left text-primary hover:underline"
          >
            {v}
          </button>
        ) : (
          <span className="truncate">{v}</span>
        ),
    },
    {
      title: t("qa.tasks.colSystem"),
      dataIndex: "system_name",
      width: 190,
      render: (v) => <span className="truncate">{v || "—"}</span>,
    },
    {
      title: t("qa.tasks.colType"),
      dataIndex: "type",
      width: 100,
      render: (v) => t(`qa.taskType.${v}` as MsgKey),
    },
    {
      title: t("qa.tasks.colRequirement"),
      dataIndex: "requirement_id",
      width: 150,
      render: (_, r) =>
        r.requirement_id == null ? (
          "—"
        ) : (
          <Badge tone="info">{r.requirement_code || `#${r.requirement_id}`}</Badge>
        ),
    },
    {
      title: t("qa.tasks.colStatus"),
      dataIndex: "status",
      width: 100,
      render: (v) => {
        const pair = STATUS[v];
        return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : v}</Badge>;
      },
    },
    {
      title: t("qa.tasks.colCases"),
      width: 90,
      render: (_, r) => `${r.feature_count} / ${r.case_count}`,
    },
    {
      title: t("qa.tasks.colLastRun"),
      width: 200,
      render: (_, r) =>
        r.last_run_at ? (
          <span className="flex items-center gap-1.5">
            <span className="font-mono text-[12px]">{when(r.last_run_at)}</span>
            {r.last_batch_status && (
              <Badge>{STATUS[r.last_batch_status] ? t(STATUS[r.last_batch_status][1]) : r.last_batch_status}</Badge>
            )}
          </span>
        ) : (
          t("qa.tasks.noRun")
        ),
    },
    {
      title: t("qa.tasks.colCreator"),
      dataIndex: "created_by_name",
      width: 100,
      render: (v) => <span className="truncate">{v || "—"}</span>,
    },
    {
      title: t("qa.tasks.colCreated"),
      dataIndex: "created_at",
      width: 170,
      render: (v) => <span className="font-mono text-[12px]">{when(v)}</span>,
    },
  ];

  return (
    <Page
      title={t("nav.qaTasks")}
      desc={t("qa.tasks.desc")}
      actions={
        <>
          <Button size="sm" className="rounded-lg" onClick={openCreate}>
            <Plus className="size-4" />
            {t("qa.tasks.new")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={load} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
            {t("qa.common.refresh")}
          </Button>
        </>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchSelect
          value={type}
          onChange={(v) => {
            setType(v == null ? undefined : String(v));
            setPage(1);
          }}
          placeholder={t("qa.tasks.filterType")}
          clearable
          options={TYPES.map((v) => ({ value: v, label: t(`qa.taskType.${v}` as MsgKey) }))}
          className="w-[150px]"
        />
        <SearchSelect
          value={status}
          onChange={(v) => {
            setStatus(v == null ? undefined : String(v));
            setPage(1);
          }}
          placeholder={t("qa.tasks.filterStatus")}
          clearable
          options={Object.entries(STATUS).map(([v, pair]) => ({ value: v, label: t(pair[1]) }))}
          className="w-[150px]"
        />
        <Input
          value={draft}
          onChange={setDraft}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setKeyword(draft.trim());
              setPage(1);
            }
          }}
          placeholder={t("qa.tasks.keyword")}
          leftIcon={<Search />}
          classNames={{ field: "rounded-lg" }}
          className="w-[260px]"
        />
      </div>
      <DataTable<QaTask>
        rowKey="id"
        dataSource={rows}
        columns={columns}
        loading={loading}
        emptyText={t("common.noData")}
        serverPagination={{
          page,
          total,
          pageSize: PAGE_SIZE,
          onPageChange: setPage,
        }}
      />

      <CenterMorphModal
        open={creating}
        onOpenChange={(open) => {
          if (!open) setCreating(false);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={t("qa.tasks.createTitle")}
          className="max-w-[560px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {t("qa.tasks.createTitle")}
            </h2>
            <div className="mt-5 flex flex-col gap-4">
              <Input
                label={t("qa.common.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                placeholder={t("qa.tasks.namePlaceholder")}
                maxLength={128}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("qa.tasks.filterType")}>
                <SearchSelect
                  value={form.values.type}
                  onChange={(v) => form.set("type", String(v ?? "case_run"))}
                  options={CREATE_TYPES.map((v) => ({
                    value: v,
                    label: t(`qa.taskType.${v}` as MsgKey),
                  }))}
                  className="w-full"
                />
              </Field>
              <Field
                label={t("qa.tasks.featuresLabel")}
                hint={t("qa.tasks.featuresHint")}
                error={form.errors.feature_ids}
              >
                <MultiSelect
                  value={form.values.feature_ids}
                  onValueChange={(v) => form.set("feature_ids", v)}
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue placeholder={t("qa.tasks.featuresPlaceholder")} />
                    <MultiSelectInput
                      aria-label={t("common.search")}
                      placeholder={t("common.search")}
                      className="h-7 min-w-24 text-sm outline-none placeholder:text-muted-foreground"
                    />
                  </MultiSelectTrigger>
                  <MultiSelectContent>
                    <MultiSelectList>
                      {[...bySystem].map(([systemName, items]) => (
                        <MultiSelectGroup key={systemName}>
                          <MultiSelectLabel>{systemName}</MultiSelectLabel>
                          {items.map((feature) => (
                            <MultiSelectItem
                              key={feature.id}
                              value={feature.id}
                              textValue={feature.name}
                              keywords={[systemName]}
                              disabled={!feature.can_manage}
                            >
                              <span className="flex w-full items-center justify-between gap-2">
                                <span className="truncate">{feature.name}</span>
                                {!feature.can_manage && (
                                  <span className="shrink-0 text-[11px] text-muted-foreground">
                                    {t("qa.tasks.noPermission")}
                                  </span>
                                )}
                              </span>
                            </MultiSelectItem>
                          ))}
                        </MultiSelectGroup>
                      ))}
                      <MultiSelectEmpty>{t("common.noData")}</MultiSelectEmpty>
                    </MultiSelectList>
                  </MultiSelectContent>
                </MultiSelect>
              </Field>
              <Field label={t("qa.tasks.envLabel")} hint={t("qa.tasks.envHint")}>
                <SearchSelect
                  value={form.values.environment_name}
                  onChange={(v) => form.set("environment_name", String(v ?? DEFAULT_ENV))}
                  options={[
                    { value: DEFAULT_ENV, label: t("qa.env.default") },
                    ...envNames.map((n) => ({ value: n, label: n })),
                  ]}
                  className="w-full"
                />
              </Field>
              <Switch
                checked={form.values.run_now}
                onCheckedChange={(v) => form.set("run_now", v)}
                label={t("qa.tasks.runNow")}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setCreating(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={create} disabled={saving}>
                {t("common.create")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </Page>
  );
}