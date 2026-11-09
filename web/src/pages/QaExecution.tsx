import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Bot, RefreshCw, Trophy, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import Page from "../Page";
import type { MsgKey } from "../i18n";
import { dur, qa, type QaBatchDetail, type QaRunItem } from "../qa";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import MiniTable from "../ui/MiniTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import { Drawer } from "../components/motion/drawer";
import type { TableColumn } from "../components/motion/table";

const RUN_ST: Record<string, [BadgeTone, MsgKey]> = {
  queued: ["neutral", "qa.status.queued"],
  preparing: ["info", "qa.runStatus.preparing"],
  running: ["info", "qa.status.running"],
  parsing: ["info", "qa.runStatus.parsing"],
  succeeded: ["success", "qa.runStatus.succeeded"],
  failed: ["danger", "qa.status.failed"],
  cancelled: ["neutral", "qa.status.cancelled"],
  timed_out: ["danger", "qa.runStatus.timed_out"],
};

const BATCH_ST: Record<string, [BadgeTone, MsgKey]> = {
  draft: ["neutral", "qa.status.draft"],
  queued: ["neutral", "qa.status.queued"],
  running: ["info", "qa.status.running"],
  completed: ["success", "qa.status.completed"],
  completed_with_failures: ["warning", "qa.status.completed_with_failures"],
  failed: ["danger", "qa.status.failed"],
  cancelled: ["neutral", "qa.status.cancelled"],
};

const ACTIVE = new Set(["queued", "preparing", "running", "parsing"]);

// 评估结论的枚举是给机器看的，界面上换成中文
const VERDICT: Record<string, [BadgeTone, MsgKey]> = {
  healthy: ["success", "qa.exec.vHealthy"],
  risky: ["warning", "qa.exec.vRisky"],
  needs_attention: ["danger", "qa.exec.vAttention"],
};

// 逐条诊断的责任归属：这条失败该修谁
const OWNER: Record<string, [BadgeTone, MsgKey]> = {
  case: ["info", "qa.exec.ownerCase"],
  product: ["danger", "qa.exec.ownerProduct"],
  env: ["neutral", "qa.exec.ownerEnv"],
};

export default function QaExecutionPage() {
  const { t } = useTranslation();
  const { bid } = useParams();
  const [batch, setBatch] = useState<QaBatchDetail | null>(null);
  const [caseRun, setCaseRun] = useState<QaRunItem | null>(null);
  const [cases, setCases] = useState<any[]>([]);
  const [ai, setAi] = useState<any>(null);
  const [aiLoaded, setAiLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setBatch(await qa<QaBatchDetail>(`/api/batches/${bid}`));
      const a = await qa(`/api/batches/${bid}/ai-check`).catch(() => null);
      setAi(a);
      setAiLoaded(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [bid]);

  const allPassed =
    !!batch &&
    batch.total > 0 &&
    batch.failed === 0 &&
    batch.passed === batch.total;
  const hasFailure =
    batch?.status === "failed" ||
    batch?.status === "partial" ||
    batch?.status === "completed_with_failures" ||
    (batch?.failed ?? 0) > 0;
  const autoTried = useRef(false);

  // 有失败才评估；成功没有评估的意义。后端在批次收尾时也会入队一次，这里只补漏。
  useEffect(() => {
    if (!hasFailure || !aiLoaded || autoTried.current) return;
    if (ai && ai.status !== "none") return;
    autoTried.current = true;
    qa(`/api/batches/${bid}/ai-check`, { method: "POST" })
      .then(setAi)
      .catch(() => {});
  }, [hasFailure, aiLoaded, ai, bid]);

  // 评估在 API 进程里异步跑，pending 时自动轮询，不用手动刷新
  useEffect(() => {
    if (ai?.status !== "pending") return;
    const id = setInterval(() => {
      qa(`/api/batches/${bid}/ai-check`).then(setAi).catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [ai?.status, bid]);

  const report = async (run: QaRunItem) => {
    try {
      const r = await qa<{ url: string }>(`/api/runs/${run.id}/report-url`);
      window.open(r.url, "_blank", "noopener");
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const openCases = async (run: QaRunItem) => {
    setCaseRun(run);
    try {
      setCases(await qa<any[]>(`/api/runs/${run.id}/cases`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const cancel = async (run: QaRunItem) => {
    try {
      const r = await qa<{ status: string }>(`/api/runs/${run.id}/cancel`, { method: "POST" });
      toast.success(r.status === "cancelling" ? t("qa.exec.cancelling") : t("qa.status.cancelled"));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const rerunRun = async (run: QaRunItem) => {
    try {
      const r = await qa<{ batch_id: string; case_count: number }>(
        `/api/runs/${run.id}/rerun-failed`,
        { method: "POST" },
      );
      toast.success(t("qa.exec.rerunStarted", { n: r.case_count }));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const rerunBatch = async () => {
    setBusy(true);
    try {
      const r = await qa<{ batch_id: string; case_count: number }>(
        `/api/batches/${bid}/rerun-failed`,
        { method: "POST" },
      );
      toast.success(t("qa.exec.rerunStarted", { n: r.case_count }));
      load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const runAi = async () => {
    setBusy(true);
    try {
      const r = await qa(`/api/batches/${bid}/ai-check`, { method: "POST" });
      setAi(r);
      toast.info(t("qa.exec.aiQueued"));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!batch) return null;
  const aiVerdict = ai?.verdict ? VERDICT[ai.verdict] : undefined;
  const canRerun = batch.status !== "running" && batch.status !== "queued";
  const batchPair = BATCH_ST[batch.status];

  const facts: [string, ReactNode][] = [
    [
      t("qa.batch.status"),
      <Badge tone={batchPair?.[0] ?? "neutral"}>{batchPair ? t(batchPair[1]) : batch.status}</Badge>,
    ],
    [t("qa.batch.trigger"), batch.trigger],
    [t("qa.batch.environment"), batch.environment_name || t("qa.env.default")],
    [t("qa.batch.task"), batch.task_name || "—"],
    [t("qa.stats.cases"), `${batch.passed}/${batch.total} ${t("qa.stats.passed")}`],
    [t("qa.batch.failed"), batch.failed],
    [t("qa.batch.duration"), dur(batch.duration_ms)],
  ];

  const columns: DataTableColumn<QaRunItem>[] = [
    {
      title: t("qa.exec.run"),
      dataIndex: "id",
      width: 110,
      render: (v, r) => (
        <span className="font-mono text-[12px]">
          {v.slice(0, 8)}
          {r.shard_count ? ` · ${(r.shard_index ?? 0) + 1}/${r.shard_count}` : ""}
        </span>
      ),
    },
    {
      title: t("qa.batch.status"),
      dataIndex: "status",
      width: 110,
      render: (v) => {
        const pair = RUN_ST[v];
        return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : v}</Badge>;
      },
    },
    {
      title: t("qa.stats.cases"),
      width: 150,
      render: (_, r) => (
        <span className="font-mono text-[12px]">
          {r.passed ?? 0}/{r.total ?? 0} · {t("qa.batch.failedN", { n: r.failed ?? 0 })}
        </span>
      ),
    },
    {
      title: t("qa.batch.duration"),
      dataIndex: "duration_ms",
      width: 90,
      render: (v) => dur(v),
    },
    {
      title: t("qa.stats.model"),
      dataIndex: "model_name",
      width: 140,
      render: (v) => <span className="truncate">{v || "—"}</span>,
    },
    {
      title: t("qa.batch.environment"),
      dataIndex: "environment_name",
      width: 110,
      render: (v) => <span className="truncate">{v || t("qa.env.default")}</span>,
    },
    {
      title: t("qa.exec.failureKind"),
      dataIndex: "failure_kind",
      width: 110,
      render: (v) => (v ? <Badge tone="warning">{v}</Badge> : "—"),
    },
    {
      title: t("qa.common.actions"),
      width: 250,
      render: (_, r) => (
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          {r.report_available && (
            <button type="button" onClick={() => report(r)} className="text-primary hover:underline">
              {t("qa.exec.report")}
            </button>
          )}
          <button type="button" onClick={() => openCases(r)} className="text-primary hover:underline">
            {t("qa.exec.cases")}
          </button>
          {ACTIVE.has(r.status) ? (
            <button type="button" onClick={() => cancel(r)} className="text-primary hover:underline">
              {t("qa.exec.cancel")}
            </button>
          ) : (
            <button type="button" onClick={() => rerunRun(r)} className="text-primary hover:underline">
              {t("qa.exec.rerunFailed")}
            </button>
          )}
        </div>
      ),
    },
  ];

  const caseCols: TableColumn<any>[] = [
    {
      key: "name",
      header: t("qa.stats.caseName"),
      cell: (r) => <span className="block truncate">{r.name || "—"}</span>,
    },
    {
      key: "status",
      header: t("qa.batch.status"),
      width: "100px",
      cell: (r) => (
        <Badge tone={r.status === "passed" ? "success" : r.status === "failed" ? "danger" : "neutral"}>
          {r.status}
        </Badge>
      ),
    },
    { key: "attempt_count", header: t("qa.exec.attempts"), width: "90px" },
    {
      key: "passed_after_retry",
      header: t("qa.exec.retriedPass"),
      width: "90px",
      cell: (r) => (r.passed_after_retry ? <Badge tone="success">✓</Badge> : "—"),
    },
  ];

  return (
    <Page
      title={`${t("qa.exec.title")} · ${batch.name}`}
      actions={
        <>
          <Link
            to={`/systems/${batch.system_id}/qa?tab=batches`}
            className="text-[13px] text-primary hover:underline"
          >
            {t("qa.backBatches")}
          </Link>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={load}>
            <RefreshCw className="size-4" />
            {t("qa.common.refresh")}
          </Button>
          {canRerun && (
            <Button variant="outline" size="sm" className="rounded-lg" onClick={rerunBatch} disabled={busy}>
              {t("qa.exec.rerunFailed")}
            </Button>
          )}
        </>
      }
    >
      <div className="mb-4 grid grid-cols-2 gap-x-6 gap-y-3 rounded-xl border border-border bg-card p-4 sm:grid-cols-4">
        {facts.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="mt-0.5 truncate text-[13px] text-foreground">{value}</div>
          </div>
        ))}
      </div>

      {allPassed ? (
        <div className="mb-4 flex items-center gap-4 rounded-xl border border-success/25 bg-success/5 p-5">
          <span className="grid size-11 shrink-0 place-items-center rounded-full bg-success/10 text-success">
            <Trophy className="size-5" />
          </span>
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-foreground">{t("qa.exec.passed")}</div>
            <div className="mt-1 text-[13px] text-muted-foreground">
              {t("qa.exec.passedSummary", {
                passed: batch.passed,
                total: batch.total,
                duration: dur(batch.duration_ms),
              })}
            </div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">{t("qa.exec.passedNote")}</div>
          </div>
        </div>
      ) : (
        <div className="mb-4 rounded-xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-center gap-2">
            <Bot className="size-4 text-primary" />
            <span className="text-[14px] font-semibold text-foreground">{t("qa.exec.aiCheck")}</span>
            {ai?.status === "done" && (
              <Badge tone={aiVerdict?.[0] ?? "neutral"}>
                {aiVerdict ? t(aiVerdict[1]) : ai.verdict || "—"}
              </Badge>
            )}
            <Button
              variant="outline"
              size="sm"
              className="ml-auto rounded-lg"
              onClick={runAi}
              disabled={busy || ai?.status === "pending"}
            >
              <RefreshCw className="size-3.5" />
              {t("qa.exec.reassess")}
            </Button>
          </div>
          <div className="mt-3 text-[13px] leading-relaxed text-foreground">
            {ai?.status === "done" ? (
              <div className="space-y-3">
                {ai.summary && <p className="m-0 text-[14px]">{ai.summary}</p>}
                {ai.reasons?.length > 0 && (
                  <div>
                    <div className="mb-1 font-semibold">{t("qa.exec.findings")}</div>
                    <ul className="m-0 list-disc pl-5 text-muted-foreground">
                      {ai.reasons.map((r: string, i: number) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {ai.diagnoses?.length > 0 && (
                  <div>
                    <div className="mb-1 font-semibold">{t("qa.exec.diagnoses")}</div>
                    <ul className="m-0 list-disc pl-5">
                      {ai.diagnoses.map((d: any, i: number) => (
                        <li key={i} className="mb-1.5">
                          <div className="flex flex-wrap items-center gap-2">
                            {d.case && <span className="font-medium">{d.case}</span>}
                            {d.owner && (
                              <Badge tone={OWNER[d.owner]?.[0] ?? "neutral"}>
                                {OWNER[d.owner] ? t(OWNER[d.owner][1]) : d.owner}
                              </Badge>
                            )}
                          </div>
                          {d.cause && <div className="mt-0.5">{d.cause}</div>}
                          {d.action && (
                            <div className="mt-0.5 text-[12px] text-muted-foreground">
                              {t("qa.exec.suggestion")}
                              {d.action}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {ai.advice?.length > 0 && (
                  <div>
                    <div className="mb-1 font-semibold">{t("qa.exec.advice")}</div>
                    <ul className="m-0 list-disc pl-5 text-muted-foreground">
                      {ai.advice.map((r: string, i: number) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="text-[12px] text-muted-foreground">
                  {[
                    ai.model_name ? t("qa.exec.byModel", { model: ai.model_name }) : null,
                    ai.created_at ? new Date(ai.created_at).toLocaleString() : null,
                    t("qa.exec.disclaimer"),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>
            ) : ai?.status === "failed" ? (
              <span className="text-muted-foreground">
                {t("qa.exec.aiUnavailable")}
                {ai.error ? ` · ${ai.error}` : ""}
              </span>
            ) : ai?.status === "pending" ? (
              <span className="text-muted-foreground">{t("qa.exec.aiPending")}</span>
            ) : (
              <span className="text-muted-foreground">{t("qa.exec.aiEmpty")}</span>
            )}
          </div>
        </div>
      )}

      <DataTable<QaRunItem>
        rowKey="id"
        dataSource={batch.run_items}
        columns={columns}
        emptyText={t("common.noData")}
      />

      <Drawer
        open={!!caseRun}
        onOpenChange={(next) => {
          if (!next) setCaseRun(null);
        }}
        ariaLabel={`${t("qa.exec.cases")} · ${caseRun?.id.slice(0, 8) || ""}`}
        className="w-[min(760px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("qa.exec.cases")} · <span className="font-mono">{caseRun?.id.slice(0, 8)}</span>
            </h3>
            <button
              type="button"
              aria-label={t("common.close")}
              onClick={() => setCaseRun(null)}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <MiniTable
              data={cases}
              columns={caseCols}
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