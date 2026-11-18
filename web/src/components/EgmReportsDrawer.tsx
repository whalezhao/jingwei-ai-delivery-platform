import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import { Loader2, X } from "lucide-react";
import { api } from "../api";
import type { MsgKey } from "../i18n";
import Badge, { type BadgeTone } from "../ui/Badge";
import { confirm } from "../ui/confirm";
import { toast } from "../ui/toast";
import { Button } from "./motion/button/index";
import { Drawer } from "./motion/drawer";
import { Tabs, TabsList, TabsTrigger } from "./motion/tabs";
import EgmMark from "./EgmMark";
import { cn } from "@/lib/utils";

export interface EgmSystem {
  id: number;
  path: string;
}

interface EgmReport {
  id: number;
  kind: string;
  status: string;
  report_md: string | null;
  branch: string | null;
  mr_url: string | null;
  created_at: string | null;
  updated_at: string | null;
}

const ST: Record<string, { tone: BadgeTone; dot: string; text: MsgKey }> = {
  running: { tone: "info", dot: "bg-primary animate-pulse", text: "systems.egm.running" },
  done: { tone: "success", dot: "bg-success", text: "systems.egm.done" },
  failed: { tone: "danger", dot: "bg-danger", text: "systems.egm.failed" },
};

/** 后端存的是 naive UTC（str(datetime)），按 UTC 解析后再转本地时区。 */
const asDate = (v?: string | null) => {
  if (!v) return null;
  const s = v.includes("T") ? v : v.replace(" ", "T");
  // Postgres 带 +00:00；SQLite 的 naive datetime 无时区，按 UTC 解析
  const iso = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i.test(s) ? s : `${s}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

const fmtTime = (v?: string | null) => {
  const d = asDate(v);
  return d
    ? d.toLocaleString(undefined, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : v || "—";
};

const fmtDuration = (a?: string | null, b?: string | null) => {
  const from = asDate(a);
  const to = asDate(b);
  if (!from || !to) return "";
  const sec = Math.max(0, Math.round((to.getTime() - from.getTime()) / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  return min < 60 ? `${min}m ${sec % 60}s` : `${Math.floor(min / 60)}h ${min % 60}m`;
};

export default function EgmReportsDrawer({
  system,
  onClose,
}: {
  system: EgmSystem | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [reports, setReports] = useState<EgmReport[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [kind, setKind] = useState("all");
  const [busy, setBusy] = useState(false);
  const systemId = system?.id;

  const kindLabel = (k: string) =>
    k === "bootstrap" ? t("systems.docTree") : t("systems.driftCheck");

  const load = async (id: number, silent = false) => {
    try {
      const rows = await api<EgmReport[]>(`/api/systems/${id}/egm-reports`);
      setReports(rows);
      // 选中的记录被删掉了就回到最新一条
      setSelectedId((cur) =>
        rows.some((r) => r.id === cur) ? cur : rows[0]?.id ?? null,
      );
    } catch (e: any) {
      if (!silent) toast.error(e.message);
    }
  };

  useEffect(() => {
    if (!systemId) return;
    setReports([]);
    setSelectedId(null);
    load(systemId);
  }, [systemId]);

  // 有任务在跑就每 8s 静默刷新，跑完自然停
  const running = reports.some((r) => r.status === "running");
  useEffect(() => {
    if (!systemId || !running) return;
    const timer = setInterval(() => load(systemId, true), 8000);
    return () => clearInterval(timer);
  }, [systemId, running]);

  const run = async (k: "bootstrap" | "drift") => {
    if (!systemId) return;
    setBusy(true);
    try {
      const rep = await api<EgmReport>(`/api/systems/${systemId}/egm-jobs`, {
        method: "POST",
        body: JSON.stringify({ kind: k }),
      });
      toast.success(
        t(k === "bootstrap" ? "systems.submittedBootstrap" : "systems.submittedDrift"),
      );
      await load(systemId);
      setSelectedId(rep.id);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const removeOne = async (rep: EgmReport) => {
    if (!systemId) return;
    const ok = await confirm({
      title: t("systems.egm.deleteConfirm"),
      description: t("systems.egm.deleteDesc"),
      confirmText: t("common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/systems/${systemId}/egm-reports/${rep.id}`, { method: "DELETE" });
      toast.success(t("systems.egm.deleted"));
      await load(systemId);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const clearAll = async () => {
    if (!systemId || reports.length === 0) return;
    const ok = await confirm({
      title: t("systems.egm.clearConfirm"),
      description: t("systems.egm.clearDesc", { n: reports.length }),
      confirmText: t("systems.egm.clearAll"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/systems/${systemId}/egm-reports`, { method: "DELETE" });
      toast.success(t("systems.egm.cleared"));
      await load(systemId);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const filtered = kind === "all" ? reports : reports.filter((r) => r.kind === kind);
  const current = filtered.find((r) => r.id === selectedId) ?? filtered[0] ?? null;
  const count = (s: string) => reports.filter((r) => r.status === s).length;
  const currentDuration = current
    ? fmtDuration(current.created_at, current.updated_at)
    : "";

  return (
    <Drawer
      open={!!system}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      ariaLabel={t("systems.egmTitle")}
      className="w-[min(1000px,96vw)] max-w-[96vw] p-0"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <h3 className="m-0 flex min-w-0 items-center gap-2 truncate text-base font-semibold text-foreground">
            <EgmMark size={16} />
            <span className="truncate">
              {t("systems.egmTitle")}
              {system ? ` · ${system.path}` : ""}
            </span>
          </h3>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="rounded-lg"
              disabled={busy || running}
              onClick={() => run("bootstrap")}
            >
              {t("systems.docTree")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="rounded-lg"
              disabled={busy || running}
              onClick={() => run("drift")}
            >
              {t("systems.driftCheck")}
            </Button>
            <button
              type="button"
              aria-label={t("common.close")}
              onClick={onClose}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-5 py-2 text-[12px] tabular-nums text-muted-foreground">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span>{t("systems.egm.total", { n: reports.length })}</span>
            <span>
              <b className="text-[14px] font-semibold text-success">{count("done")}</b>{" "}
              {t("systems.egm.done")}
            </span>
            <span>
              <b
                className={cn(
                  "text-[14px] font-semibold",
                  count("failed") ? "text-danger" : "text-foreground",
                )}
              >
                {count("failed")}
              </b>{" "}
              {t("systems.egm.failed")}
            </span>
            {count("running") ? (
              <span>
                <b className="text-[14px] font-semibold text-primary">{count("running")}</b>{" "}
                {t("systems.egm.running")}
              </span>
            ) : null}
          </div>
          <button
            type="button"
            disabled={reports.length === 0}
            onClick={clearAll}
            className="text-[12px] text-destructive transition-colors hover:text-destructive/80 disabled:pointer-events-none disabled:opacity-40"
          >
            {t("systems.egm.clearAll")}
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <aside className="flex w-[280px] shrink-0 flex-col border-r border-border">
            <div className="px-3 pt-3">
              <Tabs value={kind} onValueChange={setKind} variant="segment">
                <TabsList className="w-full">
                  <TabsTrigger value="all">{t("systems.egm.filterAll")}</TabsTrigger>
                  <TabsTrigger value="bootstrap">{t("systems.docTree")}</TabsTrigger>
                  <TabsTrigger value="drift">{t("systems.driftCheck")}</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
              {filtered.map((r) => {
                const st = ST[r.status];
                return (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => setSelectedId(r.id)}
                    className={cn(
                      "flex w-full flex-col gap-1 rounded-lg px-3 py-2 text-left transition-colors",
                      current?.id === r.id
                        ? "bg-primary/10 text-foreground"
                        : "text-foreground hover:bg-muted",
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <span className={cn("size-1.5 shrink-0 rounded-full", st?.dot ?? "bg-muted-foreground")} />
                      <span className="text-[13px] font-medium">{kindLabel(r.kind)}</span>
                      <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">
                        {fmtTime(r.created_at)}
                      </span>
                    </span>
                    <span className="pl-3.5 text-[11px] text-muted-foreground">
                      {st ? t(st.text) : r.status}
                    </span>
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="min-h-0 min-w-0 flex-1 overflow-y-auto">
            {!current ? (
              <div className="flex h-full items-center justify-center p-8">
                <div className="max-w-sm rounded-xl border border-border bg-card px-6 py-10 text-center">
                  <EgmMark size={28} />
                  <p className="mt-3 text-sm text-muted-foreground">
                    {t("systems.egm.emptyHint")}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-4 rounded-lg"
                    disabled={busy}
                    onClick={() => run("drift")}
                  >
                    {t("systems.driftCheck")}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="px-6 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge tone={current.kind === "bootstrap" ? "info" : "violet"}>
                      {kindLabel(current.kind)}
                    </Badge>
                    <Badge tone={ST[current.status]?.tone ?? "neutral"}>
                      {ST[current.status] ? t(ST[current.status].text) : current.status}
                    </Badge>
                    <span className="tabular-nums">{fmtTime(current.created_at)}</span>
                    {current.status === "done" && currentDuration ? (
                      <span>{t("systems.egm.duration", { v: currentDuration })}</span>
                    ) : null}
                    {current.branch ? (
                      <span className="font-mono">{current.branch}</span>
                    ) : null}
                    {current.mr_url ? (
                      <a
                        href={current.mr_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary hover:underline"
                      >
                        MR
                      </a>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    disabled={current.status === "running"}
                    onClick={() => removeOne(current)}
                    className="shrink-0 text-[12px] text-destructive transition-colors hover:text-destructive/80 disabled:pointer-events-none disabled:opacity-40"
                  >
                    {t("systems.egm.deleteOne")}
                  </button>
                </div>

                <div className="mt-4">
                  {current.status === "running" ? (
                    <div className="flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-8 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" />
                      {t("systems.egm.runningHint")}
                    </div>
                  ) : current.status === "failed" ? (
                    <div className="whitespace-pre-wrap rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 font-mono text-[12px] leading-relaxed text-danger">
                      {current.report_md || t("systems.noContent")}
                    </div>
                  ) : current.report_md ? (
                    <div className="md-body">
                      <Markdown>{current.report_md}</Markdown>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">{t("systems.noContent")}</p>
                  )}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </Drawer>
  );
}