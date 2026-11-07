import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import Page from "../Page";
import { isAdmin } from "../api";
import { dur, qa, type QaWorkerStatus } from "../qa";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge from "../ui/Badge";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import { Input } from "../components/motion/input";

type Worker = QaWorkerStatus["workers"][number];

export default function QaWorkersPage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<QaWorkerStatus | null>(null);
  const [limit, setLimit] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const s = await qa<QaWorkerStatus>("/api/workers/status");
      setStatus(s);
      setLimit(String(s.max_concurrent_runs));
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const saveLimit = async () => {
    const n = Number(limit);
    if (!Number.isFinite(n) || n < 1 || n > 32) return;
    setBusy(true);
    try {
      await qa("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ max_concurrent_runs: n }),
      });
      toast.success(t("qa.common.saved"));
      load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const online =
    status?.workers.filter((w) => w.age_seconds <= (status?.worker_stale_seconds ?? 90)).length ?? 0;
  const stats: [string, string | number][] = [
    [t("qa.workers.online"), online],
    [t("qa.workers.stale"), status?.stale_workers ?? 0],
    [t("qa.workers.queued"), status?.queued_runs ?? 0],
    [t("qa.workers.active"), status?.active_runs ?? 0],
    [t("qa.workers.avg"), dur(status?.avg_duration_ms)],
    [
      t("qa.workers.oldest"),
      status?.oldest_queue_seconds != null ? `${status.oldest_queue_seconds}s` : "—",
    ],
  ];

  const columns: DataTableColumn<Worker>[] = [
    {
      title: "ID",
      dataIndex: "id",
      width: 110,
      render: (v) => <span className="font-mono text-[12px]">{v.slice(0, 8)}</span>,
    },
    { title: t("qa.workers.hostname"), dataIndex: "hostname", render: (v) => v || "—" },
    {
      title: "PID",
      dataIndex: "pid",
      width: 90,
      render: (v) => <span className="font-mono text-[12px]">{v ?? "—"}</span>,
    },
    { title: t("qa.workers.slots"), dataIndex: "slots", width: 80 },
    { title: t("qa.workers.active"), dataIndex: "active_runs", width: 90 },
    {
      title: t("qa.workers.heartbeat"),
      dataIndex: "age_seconds",
      width: 120,
      render: (v) => (
        <Badge tone={v <= (status?.worker_stale_seconds ?? 90) ? "success" : "danger"}>
          {v}s {t("qa.workers.ago")}
        </Badge>
      ),
    },
    {
      title: t("qa.batch.created"),
      dataIndex: "started_at",
      width: 180,
      render: (v) => (
        <span className="font-mono text-[12px]">{(v || "").slice(0, 19).replace("T", " ")}</span>
      ),
    },
  ];

  return (
    <Page
      title={t("qa.workers.title")}
      desc={t("qa.workers.desc")}
      actions={
        <Button variant="outline" size="sm" className="rounded-lg" onClick={load}>
          <RefreshCw className="size-4" />
          {t("qa.common.refresh")}
        </Button>
      }
    >
      <div className="mb-4 grid grid-cols-2 gap-4 rounded-xl border border-border bg-card p-4 sm:grid-cols-3 lg:grid-cols-6">
        {stats.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <div className="truncate text-xs text-muted-foreground">{label}</div>
            <div className="mt-0.5 text-[22px] font-semibold tabular-nums leading-tight text-foreground">
              {value}
            </div>
          </div>
        ))}
      </div>

      {isAdmin() && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-4">
          <span className="text-sm text-foreground">{t("qa.workers.limit")}</span>
          <Input
            type="number"
            value={limit}
            onChange={setLimit}
            classNames={{ field: "rounded-lg" }}
            className="w-24"
          />
          <Button size="sm" className="rounded-lg" onClick={saveLimit} disabled={busy}>
            {t("qa.common.save")}
          </Button>
          <span className="text-xs text-muted-foreground">{t("qa.workers.limitHint")}</span>
        </div>
      )}

      <DataTable<Worker>
        rowKey="id"
        dataSource={status?.workers || []}
        columns={columns}
        emptyText={t("common.noData")}
      />
    </Page>
  );
}