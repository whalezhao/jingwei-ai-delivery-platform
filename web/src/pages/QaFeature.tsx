import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Plus, RefreshCw, Sparkles, X, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import Page from "../Page";
import { qa, qaStream, type QaCase, type QaCaseVersion, type QaFeature } from "../qa";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import MiniTable from "../ui/MiniTable";
import Badge from "../ui/Badge";
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

const DEFAULT_YAML = "cases:\n  - name: 用例名\n    steps:\n      - aiAssert: 页面正常\n";

type Step = { case: string; case_index: number; index: number; kind: string; text: string };

/** 步骤按 case 分组：一份 YAML 里可以有多个 case，编号要在各自 case 内数。 */
function groupSteps(steps: Step[]) {
  const groups: { key: number; name: string; items: Step[] }[] = [];
  for (const step of steps) {
    const last = groups[groups.length - 1];
    if (last && last.key === step.case_index) last.items.push(step);
    else groups.push({ key: step.case_index, name: step.case, items: [step] });
  }
  return groups;
}

const emptyForm = {
  name: "",
  priority: "P1" as string | null,
  description: "" as string,
  yaml_content: DEFAULT_YAML,
  is_active: true,
};

export default function QaFeaturePage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { id, fid } = useParams();
  const systemId = Number(id);
  const [feature, setFeature] = useState<QaFeature | null>(null);
  const [cases, setCases] = useState<QaCase[]>([]);
  // undefined = 抽屉关闭；null = 新建；string = 编辑的用例 id
  const [editingId, setEditingId] = useState<string | null | undefined>(undefined);
  const form = useForm(emptyForm);
  const [errors, setErrors] = useState<string[]>([]);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiInput, setAiInput] = useState("");
  const [aiMessages, setAiMessages] = useState<
    { role: "user" | "assistant"; content: string }[]
  >([]);
  const [aiYaml, setAiYaml] = useState<string | null>(null);
  // 非空表示这次是「修改已有用例」：载入它的 YAML，采用后回到编辑抽屉覆盖保存
  const [aiCase, setAiCase] = useState<QaCase | null>(null);
  const [aiYamlErrors, setAiYamlErrors] = useState<string[]>([]);
  const [aiDiff, setAiDiff] = useState<string | null>(null);
  const [aiSteps, setAiSteps] = useState<Step[]>([]);
  const [aiRunId, setAiRunId] = useState<string | null>(null);
  const [trialBusy, setTrialBusy] = useState(false);
  const [trial, setTrial] = useState<any>(null);
  const trialTimer = useRef<number | null>(null);
  // null = 还在查；false = 系统没配模型，生成必然失败，先把话说在前面
  const [modelReady, setModelReady] = useState<boolean | null>(null);
  const aiScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    aiScrollRef.current?.scrollTo({ top: aiScrollRef.current.scrollHeight });
  }, [aiMessages]);
  // 关掉弹窗后别再轮询试跑结果
  useEffect(() => () => stopTrial(), []);
  const [versionFor, setVersionFor] = useState<QaCase | null>(null);
  const [versions, setVersions] = useState<QaCaseVersion[]>([]);
  const [running, setRunning] = useState(false);

  const load = async () => {
    try {
      const [f, cs] = await Promise.all([
        qa<QaFeature>(`/api/features/${fid}`),
        qa<QaCase[]>(`/api/features/${fid}/cases`),
      ]);
      setFeature(f);
      setCases(cs);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [fid]);

  const open = (c?: QaCase) => {
    setEditingId(c ? c.id : null);
    setErrors([]);
    form.reset(
      c
        ? {
            name: c.name,
            priority: c.priority,
            description: c.description ?? "",
            yaml_content: c.yaml_content,
            is_active: c.is_active,
          }
        : { ...emptyForm },
    );
  };

  const validate = async () => {
    try {
      const r = await qa<{ ok: boolean; errors: string[]; case_count: number }>(
        "/api/cases/validate",
        { method: "POST", body: JSON.stringify({ yaml_content: form.values.yaml_content || "" }) },
      );
      setErrors(r.errors);
      if (r.ok) toast.success(t("qa.case.valid", { n: r.case_count }));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const save = async () => {
    if (!form.validate({ name: { required: true, message: t("common.required") } })) return;
    const body = { ...form.values, description: form.values.description || null };
    try {
      if (editingId) {
        await qa(`/api/cases/${editingId}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        await qa(`/api/features/${fid}/cases`, { method: "POST", body: JSON.stringify(body) });
      }
      toast.success(t("qa.common.saved"));
      setEditingId(undefined);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (c: QaCase) => {
    const ok = await confirm({
      title: t("qa.case.deleteConfirm"),
      confirmText: t("qa.common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await qa(`/api/cases/${c.id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const openAi = async (target?: QaCase) => {
    setAiCase(target ?? null);
    setAiOpen(true);
    setAiMessages([]);
    setAiInput("");
    // 修改已有用例时把它的 YAML 作为 current_yaml 交出去，模型在原文上改
    setAiYaml(target?.yaml_content ?? null);
    setAiYamlErrors([]);
    setAiDiff(null);
    setAiSteps(target?.steps ?? []);
    setAiRunId(null);
    setTrial(null);
    setModelReady(null);
    try {
      const system = await qa<{ has_model_config: boolean }>(`/api/systems/${systemId}`);
      setModelReady(system.has_model_config);
    } catch {
      // 状态查不到不拦，真没配时发送会回同样的 400
      setModelReady(true);
    }
  };

  const aiSend = async (opts?: { mode?: "rewrite" | "append"; text?: string }) => {
    const text = (opts?.text ?? aiInput).trim();
    if (!text || aiBusy) return;
    const mode = opts?.mode || "rewrite";
    const history = [...aiMessages, { role: "user" as const, content: text }];
    setAiMessages([...history, { role: "assistant", content: "" }]);
    setAiInput("");
    setAiBusy(true);
    try {
      await qaStream(
        `/api/features/${fid}/ai/case-draft/stream`,
        {
          messages: history,
          current_yaml: aiYaml,
          mode,
          run_id: mode === "append" ? aiRunId : null,
        },
        (event, data) => {
          if (event === "delta") {
            setAiMessages((msgs) => {
              const next = [...msgs];
              const last = next[next.length - 1];
              next[next.length - 1] = { ...last, content: last.content + (data.text || "") };
              return next;
            });
          } else if (event === "done") {
            // yaml 为 null = 这轮没给 YAML，保留右边已有的
            if (data.yaml != null) {
              setAiYaml(data.yaml);
              setAiYamlErrors(data.yaml_errors || []);
              setAiSteps(Array.isArray(data.steps) ? data.steps : []);
              setAiDiff(data.diff || null);
            }
            if (data.lock_violated) toast.error(t("qa.case.aiLocked"));
            setAiMessages((msgs) => {
              const next = [...msgs];
              next[next.length - 1] = {
                role: "assistant",
                content: data.reply || next[next.length - 1].content,
              };
              return next;
            });
          } else if (event === "error") {
            toast.error(data.message || t("qa.case.aiNoYaml"));
            setAiMessages((msgs) => {
              const next = [...msgs];
              next[next.length - 1] = { role: "assistant", content: data.message || "" };
              return next;
            });
          }
        },
      );
    } catch (e: any) {
      toast.error(e.message);
      setAiMessages((msgs) => msgs.slice(0, -1));
    } finally {
      setAiBusy(false);
    }
  };

  const stopTrial = () => {
    if (trialTimer.current) window.clearInterval(trialTimer.current);
    trialTimer.current = null;
  };

  // 试跑到「第 caseIndex 条 case 的第 N 步」：case 顺序执行，
// 前面的整条跑完，后面的不跑（执行器就是这么跑的）
  const trialRun = async (limit: number, caseIndex: number) => {
    if (!aiYaml || trialBusy) return;
    setTrialBusy(true);
    setTrial({ status: "queued" });
    try {
      const r = await qa<{ run_id: string; steps: number }>(
        `/api/features/${fid}/ai/trial-run`,
        {
          method: "POST",
          body: JSON.stringify({
            yaml_content: aiYaml,
            step_limit: Math.max(1, limit),
            case_index: caseIndex,
          }),
        },
      );
      setAiRunId(r.run_id);
      stopTrial();
      trialTimer.current = window.setInterval(async () => {
        try {
          const run = await qa<any>(`/api/runs/${r.run_id}`);
          setTrial(run);
          if (["succeeded", "failed", "cancelled", "timed_out"].includes(run.status)) {
            stopTrial();
          }
        } catch {
          stopTrial();
        }
      }, 2000);
    } catch (e: any) {
      toast.error(e.message);
      setTrial(null);
    } finally {
      setTrialBusy(false);
    }
  };

  const trialText = (run: any): string => {
    if (!run) return "";
    if (["queued", "preparing", "running", "parsing"].includes(run.status)) {
      return t("qa.case.aiTrialRunning");
    }
    if (run.status === "succeeded") return t("qa.case.aiTrialPassed");
    if (["cancelled", "timed_out"].includes(run.status)) {
      return t("qa.case.aiTrialInterrupted");
    }
    const step = (run.failure_steps || [])
      .flatMap((item: any) => item.steps || [])
      .find((s: any) => s.status === "failed" || s.reason);
    const detail = step ? `：${step.action || ""}${step.reason ? ` — ${step.reason}` : ""}` : "";
    return `${t("qa.case.aiTrialFailed")}${detail}`;
  };

  const aiContinue = () => {
    if (aiBusy) return;
    aiSend({ mode: "append", text: t("qa.case.aiContinuePrompt") });
  };

  const aiAdopt = () => {
    if (!aiYaml) return;
    setAiOpen(false);
    setErrors(aiYamlErrors);
    if (aiCase) {
      // 修改已有用例：回到编辑抽屉，保存时 PATCH 这一条
      setEditingId(aiCase.id);
      form.reset({
        name: aiCase.name,
        priority: aiCase.priority,
        description: aiCase.description ?? "",
        yaml_content: aiYaml,
        is_active: aiCase.is_active,
      });
      return;
    }
    setEditingId(null);
    form.reset({
      ...emptyForm,
      name: aiMessages.find((m) => m.role === "user")?.content.slice(0, 40) || feature?.name || "",
      yaml_content: aiYaml,
    });
  };

  const openVersions = async (c: QaCase) => {
    setVersionFor(c);
    try {
      setVersions(await qa<QaCaseVersion[]>(`/api/cases/${c.id}/versions`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const rollback = async (c: QaCase, v: QaCaseVersion) => {
    const ok = await confirm({
      title: t("qa.case.rollbackConfirm", { n: v.version_no }),
      confirmText: t("qa.case.rollback"),
    });
    if (!ok) return;
    try {
      await qa(`/api/cases/${c.id}/versions/${v.id}/rollback`, { method: "POST" });
      toast.success(t("qa.case.rolledBack", { n: v.version_no }));
      setVersionFor(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const runFeature = async () => {
    if (!feature) return;
    const ok = await confirm({
      title: t("qa.feature.runConfirm"),
      confirmText: t("qa.feature.run"),
    });
    if (!ok) return;
    setRunning(true);
    try {
      await qa(`/api/systems/${systemId}/tasks`, {
        method: "POST",
        body: JSON.stringify({
          name: feature.name,
          type: "feature_regression",
          feature_ids: [feature.id],
          run_now: true,
        }),
      });
      toast.success(t("qa.feature.started"));
      nav(`/systems/${systemId}/qa?tab=batches`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setRunning(false);
    }
  };

  const columns: DataTableColumn<QaCase>[] = [
    {
      title: t("qa.common.name"),
      dataIndex: "name",
      render: (v, r) => (
        <button
          type="button"
          onClick={() => open(r)}
          className="w-full truncate text-left text-primary hover:underline"
        >
          {v}
        </button>
      ),
    },
    {
      title: t("qa.case.priority"),
      dataIndex: "priority",
      width: 90,
      render: (v) => (v ? <Badge>{v}</Badge> : "—"),
    },
    {
      title: t("qa.case.active"),
      dataIndex: "is_active",
      width: 80,
      render: (v) =>
        v ? <Badge tone="success">{t("qa.common.on")}</Badge> : <Badge>{t("qa.common.off")}</Badge>,
    },
    {
      title: t("qa.case.version"),
      dataIndex: "version_no",
      width: 70,
      render: (v) => (v ? `v${v}` : "—"),
    },
    {
      title: t("qa.common.actions"),
      width: 260,
      render: (_, r) => (
        <div className="flex items-center gap-2 text-[12px]">
          <button type="button" onClick={() => open(r)} className="text-primary hover:underline">
            {t("qa.common.edit")}
          </button>
          <button
            type="button"
            onClick={() => openAi(r)}
            className="text-primary hover:underline"
          >
            {t("qa.case.aiModify")}
          </button>
          <button
            type="button"
            onClick={() => openVersions(r)}
            className="text-primary hover:underline"
          >
            {t("qa.case.versions")}
          </button>
          <button
            type="button"
            onClick={() => remove(r)}
            className="text-destructive hover:underline"
          >
            {t("qa.common.delete")}
          </button>
        </div>
      ),
    },
  ];

  const versionCols: TableColumn<QaCaseVersion>[] = [
    { key: "version_no", header: "#", width: "60px", cell: (r) => `v${r.version_no}` },
    {
      key: "name",
      header: t("qa.common.name"),
      cell: (r) => <span className="block truncate">{r.name}</span>,
    },
    { key: "change_source", header: t("qa.case.changeSource"), width: "110px" },
    {
      key: "created_at",
      header: t("qa.batch.created"),
      width: "170px",
      cell: (r) => (
        <span className="font-mono text-[12px]">
          {(r.created_at || "").slice(0, 19).replace("T", " ")}
        </span>
      ),
    },
    {
      key: "rollback",
      header: "",
      width: "70px",
      cell: (r) => (
        <button
          type="button"
          onClick={() => versionFor && rollback(versionFor, r)}
          className="text-[12px] text-primary hover:underline"
        >
          {t("qa.case.rollback")}
        </button>
      ),
    },
  ];

  const stepGroups = groupSteps(aiSteps);

  return (
    <Page
      title={`${t("qa.feature.cases")} · ${feature?.name || ""}`}
      desc={feature?.description || undefined}
      actions={
        <>
          <Link to={`/systems/${systemId}/qa`} className="text-[13px] text-primary hover:underline">
            {t("qa.backFeatures")}
          </Link>
          <Button size="sm" className="rounded-lg" onClick={() => open()}>
            <Plus className="size-4" />
            {t("qa.case.new")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={() => openAi()}>
            <Sparkles className="size-4" />
            {t("qa.case.aiGen")}
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={runFeature} disabled={running}>
            <Zap className="size-4" />
            {t("qa.feature.run")}
          </Button>
        </>
      }
    >
      <div className="mb-3">
        <Button variant="outline" size="sm" className="rounded-lg" onClick={load}>
          <RefreshCw className="size-4" />
          {t("qa.common.refresh")}
        </Button>
      </div>
      <DataTable<QaCase> rowKey="id" dataSource={cases} columns={columns} emptyText={t("common.noData")} />

      <Drawer
        open={editingId !== undefined}
        onOpenChange={(next) => {
          if (!next) setEditingId(undefined);
        }}
        ariaLabel={editingId ? t("qa.case.editTitle") : t("qa.case.new")}
        className="w-[min(900px,96vw)] max-w-[96vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {editingId ? t("qa.case.editTitle") : t("qa.case.new")}
            </h3>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="outline" size="sm" className="rounded-lg" onClick={validate}>
                {t("qa.case.validate")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={save}>
                {t("common.save")}
              </Button>
              <button
                type="button"
                aria-label={t("common.close")}
                onClick={() => setEditingId(undefined)}
                className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_110px_auto] sm:items-end">
              <Input
                label={t("qa.common.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                maxLength={128}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("qa.case.priority")}>
                <SearchSelect
                  value={form.values.priority}
                  onChange={(v) => form.set("priority", v == null ? null : String(v))}
                  options={["P0", "P1", "P2", "P3"].map((p) => ({ value: p, label: p }))}
                />
              </Field>
              <Field label={t("qa.case.active")}>
                <span className="flex h-9 items-center">
                  <Switch
                    checked={!!form.values.is_active}
                    onCheckedChange={(v) => form.set("is_active", v)}
                  />
                </span>
              </Field>
            </div>
            <Input
              label={t("qa.common.description")}
              value={form.values.description}
              onChange={(v) => form.set("description", v)}
              classNames={{ field: "rounded-lg" }}
              className="mt-4"
            />
            <Textarea
              label="YAML"
              rows={20}
              spellCheck={false}
              value={form.values.yaml_content}
              onChange={(v) => form.set("yaml_content", v)}
              className="mt-4 [&_textarea]:font-mono [&_textarea]:text-[12px]"
            />
            {errors.length > 0 && (
              <div className="mt-3 rounded-lg bg-danger/10 px-3 py-2 text-[13px] text-danger">
                <p className="m-0 font-medium">{t("qa.case.yamlErrors")}</p>
                <ul className="m-0 mt-1 list-disc pl-5">
                  {errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </Drawer>

      <CenterMorphModal
        open={aiOpen}
        onOpenChange={(next) => {
          if (!next) setAiOpen(false);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={aiCase ? t("qa.case.aiModify") : t("qa.case.aiGen")}
          className="max-w-[640px]"
        >
          <div className="flex max-h-[76vh] flex-col p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {aiCase ? t("qa.case.aiModify") : t("qa.case.aiGen")}
            </h2>
            {modelReady === false ? (
              <div className="mt-4 rounded-lg bg-warning/10 px-3 py-3 text-[13px] leading-relaxed text-foreground">
                <p className="m-0">{t("qa.case.aiModelMissing")}</p>
                <Link
                  to={`/systems/${systemId}/qa?tab=config`}
                  onClick={() => setAiOpen(false)}
                  className="mt-2 inline-block font-medium text-primary hover:underline"
                >
                  {t("qa.case.aiGoConfig")}
                </Link>
              </div>
            ) : (
              <>
                <div
                  ref={aiScrollRef}
                  className="mt-4 flex max-h-[45vh] min-h-24 flex-col gap-2 overflow-y-auto"
                >
                  {aiMessages.length === 0 ? (
                    <p className="m-0 text-[13px] text-muted-foreground">
                      {t("qa.case.aiPrompt")}
                    </p>
                  ) : (
                    aiMessages.map((m, i) => (
                      <div
                        key={i}
                        className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
                      >
                        <div
                          className={`max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-[13px] leading-relaxed text-foreground ${
                            m.role === "user" ? "bg-primary/10" : "border border-border bg-card"
                          }`}
                        >
                          {m.content ||
                            (aiBusy && i === aiMessages.length - 1 ? "…" : "")}
                        </div>
                      </div>
                    ))
                  )}
                </div>
                {aiYaml && aiSteps.length > 0 ? (
                  <div className="mt-3 overflow-hidden rounded-lg border border-border">
                    <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/50 px-3 py-1.5">
                      <span className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                        {t("qa.case.aiSteps")}
                      </span>
                      {trial ? (
                        <span
                          className={`truncate text-[12px] ${
                            trial.status === "succeeded"
                              ? "text-success"
                              : ["queued", "preparing", "running", "parsing"].includes(trial.status)
                                ? "text-muted-foreground"
                                : "text-danger"
                          }`}
                          title={trialText(trial)}
                        >
                          {trialText(trial)}
                        </span>
                      ) : null}
                    </div>
                    <ol className="m-0 max-h-40 list-none overflow-auto p-1.5">
                      {stepGroups.map((group) => (
                        <li key={group.key} className="py-0.5">
                          {stepGroups.length > 1 ? (
                            <div
                              className="truncate px-1 text-[11px] text-muted-foreground"
                              title={group.name}
                            >
                              {group.name || `#${group.key + 1}`}
                            </div>
                          ) : null}
                          <ol className="m-0 list-none p-0">
                            {group.items.map((s) => (
                              <li
                                key={s.index}
                                className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-muted/60"
                              >
                                <span className="w-5 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
                                  {s.index + 1}
                                </span>
                                <span className="min-w-0 flex-1 truncate text-[12px]" title={s.text}>
                                  {s.text || s.kind}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => trialRun(s.index + 1, s.case_index)}
                                  disabled={trialBusy}
                                  className="shrink-0 text-[11px] text-primary hover:underline disabled:opacity-50"
                                >
                                  {t("qa.case.aiTrialHere")}
                                </button>
                              </li>
                            ))}
                          </ol>
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
                {aiYaml ? (
                  <div className="mt-3 overflow-hidden rounded-lg border border-border">
                    <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/50 px-3 py-1.5">
                      <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                        YAML
                      </span>
                      <div className="flex items-center gap-3">
                        {aiRunId ? (
                          <button
                            type="button"
                            onClick={aiContinue}
                            disabled={aiBusy}
                            className="text-[12px] font-medium text-primary hover:underline disabled:opacity-50"
                          >
                            {t("qa.case.aiContinue")}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          onClick={aiAdopt}
                          className="text-[12px] font-medium text-primary hover:underline"
                        >
                          {t("qa.case.aiAdopt")}
                        </button>
                      </div>
                    </div>
                    <pre className="m-0 max-h-40 overflow-auto p-3 font-mono text-[12px] leading-relaxed">
                      {aiYaml}
                    </pre>
                    {aiDiff ? (
                      <details className="border-t border-border">
                        <summary className="cursor-pointer px-3 py-1.5 text-[12px] text-muted-foreground">
                          {t("qa.case.aiDiff")}
                        </summary>
                        <pre className="m-0 max-h-40 overflow-auto border-t border-border p-3 font-mono text-[11px] leading-relaxed">
                          {aiDiff}
                        </pre>
                      </details>
                    ) : null}
                    {aiYamlErrors.length > 0 && (
                      <p className="m-0 border-t border-border px-3 py-2 text-[12px] text-danger">
                        {aiYamlErrors.join("；")}
                      </p>
                    )}
                  </div>
                ) : null}
                <div className="mt-3">
                  <Textarea
                    rows={3}
                    value={aiInput}
                    onChange={setAiInput}
                    placeholder={t("qa.case.aiPrompt")}
                  />
                </div>
                <div className="mt-4 flex justify-end gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="rounded-lg"
                    onClick={() => setAiOpen(false)}
                  >
                    {t("common.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    className="rounded-lg"
                    onClick={() => aiSend()}
                    disabled={aiBusy || !aiInput.trim()}
                  >
                    {aiBusy ? t("qa.case.aiSending") : t("qa.case.aiSend")}
                  </Button>
                </div>
              </>
            )}
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>

      <Drawer
        open={!!versionFor}
        onOpenChange={(next) => {
          if (!next) setVersionFor(null);
        }}
        ariaLabel={`${t("qa.case.versions")} · ${versionFor?.name || ""}`}
        className="w-[min(680px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("qa.case.versions")} · {versionFor?.name || ""}
            </h3>
            <button
              type="button"
              aria-label={t("common.close")}
              onClick={() => setVersionFor(null)}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <MiniTable
              data={versions}
              columns={versionCols}
              rowKey="id"
              rowHeight={40}
              emptyText={t("common.noData")}
            />
          </div>
        </div>
      </Drawer>
    </Page>
  );
}