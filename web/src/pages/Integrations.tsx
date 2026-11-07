import { useCallback, useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { MsgKey } from "../i18n";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import CopyText from "../ui/CopyText";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Input } from "../components/motion/input";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/motion/tabs";

interface Paged<T> {
  items: T[];
  total: number;
}

const PAGE_SIZE = 20;

const VERDICT: Record<string, [BadgeTone, MsgKey]> = {
  pending: ["info", "integrations.verdict.pending"],
  approve: ["success", "integrations.verdict.approve"],
  modify: ["warning", "integrations.verdict.modify"],
  failed: ["danger", "integrations.verdict.failed"],
};

const FLOW: Record<string, [BadgeTone, MsgKey]> = {
  merged: ["success", "integrations.flow.merged"],
  conflict_notified: ["danger", "integrations.flow.conflict_notified"],
  hold_not_mergeable: ["warning", "integrations.flow.hold_not_mergeable"],
};

/** 分页表格的公共拉取：三个日志 Tab 结构一致，抽一个 hook 少写三遍。
 *  q 变化回到第一页重新拉（服务端过滤）。 */
function usePaged<T>(url: string, q: string) {
  const [data, setData] = useState<Paged<T>>({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const load = useCallback(
    async (p: number) => {
      setLoading(true);
      try {
        const qs = new URLSearchParams({
          page: String(p),
          page_size: String(PAGE_SIZE),
        });
        if (q) qs.set("q", q);
        setData(await api<Paged<T>>(`${url}?${qs}`));
      } catch (e: any) {
        toast.error(e.message);
      } finally {
        setLoading(false);
      }
    },
    [url, q],
  );
  useEffect(() => {
    setPage(1);
  }, [q]);
  useEffect(() => {
    load(page);
  }, [page, load]);
  return { data, page, setPage, loading };
}

/** 日志检索框：回车/清空即查，和分页 hook 的 q 配套 */
function LogSearch({
  onChange,
  placeholder,
}: {
  onChange: (v: string) => void;
  placeholder: string;
}) {
  const [value, setValue] = useState("");
  return (
    <div className="mb-3 max-w-sm">
      <Input
        value={value}
        onChange={setValue}
        placeholder={placeholder}
        leftIcon={<Search />}
        classNames={{ field: "rounded-lg" }}
        onKeyDown={(e) => {
          if (e.key === "Enter") onChange(value.trim());
        }}
        rightIcon={
          value ? (
            <button
              type="button"
              aria-label="clear"
              onClick={() => {
                setValue("");
                onChange("");
              }}
            >
              <X />
            </button>
          ) : undefined
        }
      />
    </div>
  );
}

function Credentials() {
  const { t } = useTranslation();
  const [sum, setSum] = useState<any>(null);
  const [octo, setOcto] = useState<any>(null);
  const [tok, setTok] = useState("");
  const [busy, setBusy] = useState(false);

  const loadOcto = useCallback(
    () =>
      api<any>("/api/integrations/octo")
        .then(setOcto)
        .catch((e) => toast.error(e.message)),
    [],
  );

  useEffect(() => {
    api<any>("/api/integrations/summary")
      .then(setSum)
      .catch((e) => toast.error(e.message));
    loadOcto();
  }, [loadOcto]);

  const saveOcto = async () => {
    setBusy(true);
    try {
      await api("/api/integrations/octo", {
        method: "PUT",
        body: JSON.stringify({ bot_token: tok }),
      });
      setTok("");
      await loadOcto();
      // 保存即验证：能拉到群列表才算真的连上 octo
      const g = await api<{ items: any[]; error: string | null }>(
        "/api/integrations/octo-groups",
      );
      if (g.error) toast.warning(g.error);
      else toast.success(t("integrations.octoConnected", { n: g.items.length }));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!sum) return null;

  const columns: DataTableColumn<any>[] = [
    { title: t("integrations.colGroup"), dataIndex: "name" },
    {
      title: t("integrations.colSecret"),
      dataIndex: "webhook_secret",
      render: (v) => (v ? <CopyText value={v} className="py-1" /> : "—"),
    },
    {
      title: t("integrations.colOctoBot"),
      dataIndex: "has_octo_bot_token",
      width: 130,
      render: (v) =>
        v ? (
          <Badge tone="success">{t("integrations.ownBot")}</Badge>
        ) : (
          <Badge>{t("integrations.globalBot")}</Badge>
        ),
    },
  ];

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="m-0 flex flex-wrap items-center gap-2 text-sm text-foreground">
        {t("integrations.octoIntro")}
        {octo?.has_token ? (
          <Badge tone="success">
            {octo.source === "env"
              ? t("integrations.configuredEnv")
              : t("integrations.configured")}
          </Badge>
        ) : (
          <Badge tone="danger">{t("integrations.notConfigured")}</Badge>
        )}
        <span className="font-mono text-[11px] text-muted-foreground">
          API {octo?.api_url}
        </span>
      </p>
      <div className="my-4 flex max-w-[560px] flex-col gap-2 sm:flex-row">
        <Input
          type="password"
          value={tok}
          onChange={setTok}
          placeholder={
            octo?.has_token
              ? t("integrations.tokenPlaceholderSet")
              : t("integrations.tokenPlaceholderNew")
          }
          classNames={{ field: "rounded-lg" }}
          className="flex-1"
        />
        <Button
          size="sm"
          className="h-11 rounded-lg px-4"
          onClick={saveOcto}
          disabled={busy}
        >
          {t("integrations.saveConnect")}
        </Button>
      </div>
      <p className="m-0 text-sm text-foreground">
        {t("integrations.webhookIntro")}
      </p>
      <CopyText value={sum.webhook_url} className="my-2 max-w-[560px]" />
      <div className="mt-4">
        <DataTable
          rowKey="id"
          dataSource={sum.groups}
          columns={columns}
          rowHeight={56}
          emptyText={t("common.noData")}
        />
      </div>
    </div>
  );
}

function GitlabEvents() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, page, setPage, loading } = usePaged<any>(
    "/api/integrations/events",
    q,
  );
  const [payload, setPayload] = useState<any>(null);

  const columns: DataTableColumn<any>[] = [
    { title: "ID", dataIndex: "id", width: 70 },
    {
      title: t("integrations.colEvent"),
      dataIndex: "event_type",
      width: 180,
      render: (v) => <Badge>{v}</Badge>,
    },
    {
      title: t("integrations.colGroupShort"),
      dataIndex: "group_id",
      width: 70,
    },
    {
      title: t("integrations.colSummary"),
      render: (_, r) => {
        const a = r.payload?.object_attributes || {};
        return (
          a.title ||
          a.ref ||
          (r.payload?.project && r.payload.project.name) ||
          "—"
        );
      },
    },
    {
      title: t("integrations.colHandled"),
      dataIndex: "handled_at",
      width: 170,
      render: (v) =>
        v ? (
          <span className="font-mono text-[11px]">{v.slice(0, 19)}</span>
        ) : (
          t("integrations.unhandled")
        ),
    },
  ];

  return (
    <>
      <LogSearch onChange={setQ} placeholder={t("integrations.eventsPlaceholder")} />
      <DataTable
        rowKey="id"
        dataSource={data.items}
        columns={columns}
        loading={loading}
        onRowClick={(r) => setPayload(r.payload)}
        serverPagination={{
          page,
          total: data.total,
          pageSize: PAGE_SIZE,
          onPageChange: setPage,
        }}
        emptyText={t("common.noData")}
      />
      <CenterMorphModal
        open={payload != null}
        onOpenChange={(next) => {
          if (!next) setPayload(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={t("integrations.colSummary")}
          className="max-w-[720px]"
        >
          <div className="p-5">
            <pre className="m-0 max-h-[65vh] overflow-auto rounded-lg bg-muted/40 p-3 font-mono text-[11px] leading-5">
              {JSON.stringify(payload, null, 2)}
            </pre>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </>
  );
}

function NotifyLogs() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, page, setPage, loading } = usePaged<any>(
    "/api/integrations/notify-logs",
    q,
  );

  const columns: DataTableColumn<any>[] = [
    { title: "ID", dataIndex: "id", width: 70 },
    {
      title: t("integrations.colReason"),
      dataIndex: "reason",
      width: 160,
      render: (v) => <Badge>{v}</Badge>,
    },
    {
      title: t("integrations.colResult"),
      dataIndex: "ok",
      width: 100,
      render: (v) =>
        v ? (
          <Badge tone="success">{t("integrations.delivered")}</Badge>
        ) : (
          <Badge tone="danger">{t("integrations.verdict.failed")}</Badge>
        ),
    },
    {
      title: t("integrations.colContent"),
      dataIndex: "content",
      render: (v) => (
        <span className="truncate">
          {v}
        </span>
      ),
    },
    {
      title: "@",
      dataIndex: "mentioned_uids",
      width: 80,
      render: (v: any[]) => (v?.length ? v.length : "—"),
    },
    {
      title: t("integrations.colTime"),
      dataIndex: "created_at",
      width: 170,
      render: (v) => (
        <span className="font-mono text-[11px]">{v?.slice(0, 19)}</span>
      ),
    },
  ];

  return (
    <>
      <LogSearch onChange={setQ} placeholder={t("integrations.notifyPlaceholder")} />
      <DataTable
        rowKey="id"
        dataSource={data.items}
        columns={columns}
        loading={loading}
        serverPagination={{
          page,
          total: data.total,
          pageSize: PAGE_SIZE,
          onPageChange: setPage,
        }}
        emptyText={t("common.noData")}
      />
    </>
  );
}

function MrReviews() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, page, setPage, loading } = usePaged<any>(
    "/api/integrations/mr-reviews",
    q,
  );

  const columns: DataTableColumn<any>[] = [
    {
      title: "MR",
      width: 150,
      render: (_, r) => (
        <span className="font-mono text-[11px]">
          {r.project_path} !{r.mr_iid}
        </span>
      ),
    },
    {
      title: t("integrations.colTitle"),
      dataIndex: "title",
      render: (v) => (
        <span className="truncate">
          {v}
        </span>
      ),
    },
    {
      title: t("integrations.colVerdict"),
      dataIndex: "verdict",
      width: 120,
      render: (v, r) => {
        const pair = VERDICT[v];
        return (
          <Badge tone={pair?.[0] ?? "neutral"}>
            {pair ? t(pair[1]) : v}
            {r.gate ? `（CI ${r.gate.replace("ci:", "")}）` : ""}
          </Badge>
        );
      },
    },
    {
      title: t("integrations.colRelease"),
      dataIndex: "flow_state",
      width: 130,
      render: (v) => {
        if (!v) return <Badge>{t("integrations.pending")}</Badge>;
        const pair = FLOW[v];
        return (
          <Badge tone={pair?.[0] ?? "neutral"}>
            {pair ? t(pair[1]) : v}
          </Badge>
        );
      },
    },
    {
      title: t("integrations.colChanged"),
      dataIndex: "changed_files",
      width: 70,
      render: (v: any[]) => v?.length ?? 0,
    },
    {
      title: t("integrations.colSummary"),
      dataIndex: "summary",
      render: (v) => (
        <span className="truncate">
          {v}
        </span>
      ),
    },
    {
      title: t("integrations.colCommit"),
      width: 100,
      render: (_, r) => (
        <span className="font-mono text-[11px]">{r.head_sha?.slice(0, 8)}</span>
      ),
    },
  ];

  return (
    <>
      <LogSearch onChange={setQ} placeholder={t("integrations.mrPlaceholder")} />
      <DataTable
        rowKey="id"
        dataSource={data.items}
        columns={columns}
        loading={loading}
        serverPagination={{
          page,
          total: data.total,
          pageSize: PAGE_SIZE,
          onPageChange: setPage,
        }}
        emptyText={t("common.noData")}
      />
    </>
  );
}

export default function Integrations() {
  const { t } = useTranslation();
  return (
    <Page title={t("nav.integrations")} desc={t("integrations.desc")}>
      <Tabs defaultValue="cred" variant="segment">
        <TabsList className="mb-3">
          <TabsTrigger value="cred">{t("integrations.tabCred")}</TabsTrigger>
          <TabsTrigger value="events">{t("integrations.tabEvents")}</TabsTrigger>
          <TabsTrigger value="notify">{t("integrations.tabNotify")}</TabsTrigger>
          <TabsTrigger value="mr">{t("integrations.tabMr")}</TabsTrigger>
        </TabsList>
        <TabsContent value="cred">
          <Credentials />
        </TabsContent>
        <TabsContent value="events">
          <GitlabEvents />
        </TabsContent>
        <TabsContent value="notify">
          <NotifyLogs />
        </TabsContent>
        <TabsContent value="mr">
          <MrReviews />
        </TabsContent>
      </Tabs>
    </Page>
  );
}