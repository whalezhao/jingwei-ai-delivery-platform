import { useCallback, useEffect, useState } from "react";
import { Button, Input, Space, Tabs, Tag, Tooltip, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { api } from "../api";
import type { MsgKey } from "../i18n";
import Page from "../Page";

interface Paged<T> {
  items: T[];
  total: number;
}

const PAGE_SIZE = 20;

const VERDICT: Record<string, [string, MsgKey]> = {
  pending: ["blue", "integrations.verdict.pending"],
  approve: ["green", "integrations.verdict.approve"],
  modify: ["orange", "integrations.verdict.modify"],
  failed: ["red", "integrations.verdict.failed"],
};

const FLOW: Record<string, [string, MsgKey]> = {
  merged: ["green", "integrations.flow.merged"],
  conflict_notified: ["red", "integrations.flow.conflict_notified"],
  hold_not_mergeable: ["orange", "integrations.flow.hold_not_mergeable"],
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
        const qs = new URLSearchParams({ page: String(p), page_size: String(PAGE_SIZE) });
        if (q) qs.set("q", q);
        setData(await api<Paged<T>>(`${url}?${qs}`));
      } catch (e: any) {
        message.error(e.message);
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
  return { data, page, setPage, loading, reload: () => load(page) };
}

/** 日志检索框：回车/清空即查，和分页 hook 的 q 配套 */
function LogSearch({ onChange, placeholder }: { onChange: (v: string) => void; placeholder: string }) {
  return (
    <Input.Search
      style={{ width: 280, marginBottom: 12 }}
      allowClear
      placeholder={placeholder}
      onSearch={(v) => onChange(v.trim())}
      onChange={(e) => !e.target.value && onChange("")}
    />
  );
}

function Credentials() {
  const { t } = useTranslation();
  const [sum, setSum] = useState<any>(null);
  const [octo, setOcto] = useState<any>(null);
  const [tok, setTok] = useState("");
  const [busy, setBusy] = useState(false);

  const loadOcto = useCallback(
    () => api<any>("/api/integrations/octo").then(setOcto).catch((e) => message.error(e.message)),
    [],
  );

  useEffect(() => {
    api<any>("/api/integrations/summary").then(setSum).catch((e) => message.error(e.message));
    loadOcto();
  }, [loadOcto]);

  const saveOcto = async () => {
    setBusy(true);
    try {
      await api("/api/integrations/octo", { method: "PUT", body: JSON.stringify({ bot_token: tok }) });
      setTok("");
      await loadOcto();
      // 保存即验证：能拉到群列表才算真的连上 octo
      const g = await api<{ items: any[]; error: string | null }>("/api/integrations/octo-groups");
      if (g.error) message.warning(g.error);
      else message.success(t("integrations.octoConnected", { n: g.items.length }));
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!sum) return null;
  return (
    <div>
      <p>
        {t("integrations.octoIntro")}
        {octo?.has_token ? (
          <Tag color="green">
            {octo.source === "env" ? t("integrations.configuredEnv") : t("integrations.configured")}
          </Tag>
        ) : (
          <Tag color="red">{t("integrations.notConfigured")}</Tag>
        )}
        <Typography.Text type="secondary">API {octo?.api_url}</Typography.Text>
      </p>
      <Space.Compact style={{ width: 560, marginBottom: 20 }}>
        <Input.Password
          value={tok}
          onChange={(e) => setTok(e.target.value)}
          placeholder={
            octo?.has_token ? t("integrations.tokenPlaceholderSet") : t("integrations.tokenPlaceholderNew")
          }
        />
        <Button type="primary" loading={busy} onClick={saveOcto}>
          {t("integrations.saveConnect")}
        </Button>
      </Space.Compact>
      <p>
        {t("integrations.webhookIntro")}
        <br />
        <Typography.Text copyable code>{sum.webhook_url}</Typography.Text>
      </p>
      <ResizableTable
        rowKey="id"
        size="small"
        pagination={false}
        dataSource={sum.groups}
        columns={[
          { title: t("integrations.colGroup"), dataIndex: "name" },
          {
            title: t("integrations.colSecret"),
            dataIndex: "webhook_secret",
            render: (v) => (v ? <Typography.Text copyable code>{v}</Typography.Text> : "—"),
          },
          {
            title: t("integrations.colOctoBot"),
            dataIndex: "has_octo_bot_token",
            render: (v) =>
              v ? (
                <Tooltip title={t("integrations.ownBotTip")}>
                  <Tag color="green">{t("integrations.ownBot")}</Tag>
                </Tooltip>
              ) : (
                <Tooltip title={t("integrations.globalBotTip")}>
                  <Tag>{t("integrations.globalBot")}</Tag>
                </Tooltip>
              ),
          },
        ]}
      />
    </div>
  );
}

function GitlabEvents() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, page, setPage, loading } = usePaged<any>("/api/integrations/events", q);
  return (
    <>
      <LogSearch onChange={setQ} placeholder={t("integrations.eventsPlaceholder")} />
      <ResizableTable<any>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={data.items}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: data.total, onChange: setPage }}
        expandable={{
          expandedRowRender: (r) => (
            <pre style={{ maxHeight: 300, overflow: "auto", fontSize: 12 }}>{JSON.stringify(r.payload, null, 2)}</pre>
          ),
        }}
        columns={[
          { title: "ID", dataIndex: "id", width: 70 },
          { title: t("integrations.colEvent"), dataIndex: "event_type", render: (v) => <Tag>{v}</Tag> },
          { title: t("integrations.colGroupShort"), dataIndex: "group_id", width: 70 },
          {
            title: t("integrations.colSummary"),
            render: (_, r) => {
              const a = r.payload?.object_attributes || {};
              return a.title || a.ref || (r.payload?.project && r.payload.project.name) || "—";
            },
          },
          { title: t("integrations.colHandled"), dataIndex: "handled_at", render: (v) => (v ? v.slice(0, 19) : t("integrations.unhandled")) },
        ]}
      />
    </>
  );
}

function NotifyLogs() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, page, setPage, loading } = usePaged<any>("/api/integrations/notify-logs", q);
  return (
    <>
      <LogSearch onChange={setQ} placeholder={t("integrations.notifyPlaceholder")} />
      <ResizableTable<any>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={data.items}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: data.total, onChange: setPage }}
        columns={[
          { title: "ID", dataIndex: "id", width: 70 },
          { title: t("integrations.colReason"), dataIndex: "reason", render: (v) => <Tag>{v}</Tag> },
          {
            title: t("integrations.colResult"),
            dataIndex: "ok",
            render: (v) => (v ? <Tag color="green">{t("integrations.delivered")}</Tag> : <Tag color="red">{t("integrations.verdict.failed")}</Tag>),
          },
          {
            title: t("integrations.colContent"),
            dataIndex: "content",
            ellipsis: true,
            render: (v) => <span style={{ whiteSpace: "pre-wrap" }}>{v}</span>,
          },
          { title: "@", dataIndex: "mentioned_uids", width: 90, render: (v: any[]) => (v?.length ? v.length : "—") },
          { title: t("integrations.colTime"), dataIndex: "created_at", width: 160, render: (v) => v?.slice(0, 19) },
        ]}
      />
    </>
  );
}

function MrReviews() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, page, setPage, loading } = usePaged<any>("/api/integrations/mr-reviews", q);
  return (
    <>
      <LogSearch onChange={setQ} placeholder={t("integrations.mrPlaceholder")} />
      <ResizableTable<any>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={data.items}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: data.total, onChange: setPage }}
        columns={[
        { title: "MR", width: 110, render: (_, r) => <span className="mono">{r.project_path} !{r.mr_iid}</span> },
        { title: t("integrations.colTitle"), dataIndex: "title", ellipsis: true },
        {
          title: t("integrations.colVerdict"),
          dataIndex: "verdict",
          width: 100,
          render: (v, r) => {
            const pair = VERDICT[v];
            return (
              <Tag color={pair?.[0] ?? "default"}>
                {pair ? t(pair[1]) : v}
                {r.gate ? `（CI ${r.gate.replace("ci:", "")}）` : ""}
              </Tag>
            );
          },
        },
        {
          title: t("integrations.colRelease"),
          dataIndex: "flow_state",
          width: 110,
          render: (v) => {
            if (!v) return <Tag>{t("integrations.pending")}</Tag>;
            const pair = FLOW[v];
            return <Tag color={pair?.[0] ?? "default"}>{pair ? t(pair[1]) : v}</Tag>;
          },
        },
        { title: t("integrations.colChanged"), dataIndex: "changed_files", width: 70, render: (v: any[]) => v?.length ?? 0 },
        { title: t("integrations.colSummary"), dataIndex: "summary", ellipsis: true },
        { title: t("integrations.colCommit"), dataIndex: "head_sha", width: 100, render: (v) => <span className="mono">{v?.slice(0, 8)}</span> },
        ]}
      />
    </>
  );
}

export default function Integrations() {
  const { t } = useTranslation();
  return (
    <Page title={t("nav.integrations")} desc={t("integrations.desc")}>
      <div className="page-card">
        <Tabs
          items={[
            { key: "cred", label: t("integrations.tabCred"), children: <Credentials /> },
            { key: "events", label: t("integrations.tabEvents"), children: <GitlabEvents /> },
            { key: "notify", label: t("integrations.tabNotify"), children: <NotifyLogs /> },
            { key: "mr", label: t("integrations.tabMr"), children: <MrReviews /> },
          ]}
        />
      </div>
    </Page>
  );
}
