import { useCallback, useEffect, useState } from "react";
import { Button, Input, Space, Tabs, Tag, Tooltip, Typography, message } from "antd";
import ResizableTable from "../ResizableTable";
import { api } from "../api";
import Page from "../Page";

interface Paged<T> {
  items: T[];
  total: number;
}

const PAGE_SIZE = 20;

const VERDICT: Record<string, [string, string]> = {
  pending: ["blue", "评审中"],
  approve: ["green", "✅ 可批准"],
  modify: ["orange", "⚠️ 需修改"],
  failed: ["red", "失败"],
};

const FLOW: Record<string, [string, string]> = {
  merged: ["green", "已自动合并"],
  conflict_notified: ["red", "冲突已@人"],
  hold_not_mergeable: ["orange", "待重试"],
};

/** 分页表格的公共拉取：三个日志 Tab 结构一致，抽一个 hook 少写三遍。 */
function usePaged<T>(url: string) {
  const [data, setData] = useState<Paged<T>>({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const load = useCallback(
    async (p: number) => {
      setLoading(true);
      try {
        setData(await api<Paged<T>>(`${url}?page=${p}&page_size=${PAGE_SIZE}`));
      } catch (e: any) {
        message.error(e.message);
      } finally {
        setLoading(false);
      }
    },
    [url],
  );
  useEffect(() => {
    load(page);
  }, [page, load]);
  return { data, page, setPage, loading, reload: () => load(page) };
}

function Credentials() {
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
      else message.success(`已连接 octo：bot 在 ${g.items.length} 个群里`);
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
        octo 全局 bot（拉群列表、发群通知都用它；研发组选群不必再有本组 token）：
        {octo?.has_token ? (
          <Tag color="green">已配置{octo.source === "env" ? " · 来自 .env" : ""}</Tag>
        ) : (
          <Tag color="red">未配置</Tag>
        )}
        <Typography.Text type="secondary">API {octo?.api_url}</Typography.Text>
      </p>
      <Space.Compact style={{ width: 560, marginBottom: 20 }}>
        <Input.Password
          value={tok}
          onChange={(e) => setTok(e.target.value)}
          placeholder={octo?.has_token ? "已配置；输入新 token 覆盖，留空保存 = 清除" : "粘贴 bot token"}
        />
        <Button type="primary" loading={busy} onClick={saveOcto}>
          保存并连接
        </Button>
      </Space.Compact>
      <p>
        全局 Webhook 地址（GitLab 项目统一配这一个 URL，靠各组 Secret 识别归属）：
        <br />
        <Typography.Text copyable code>{sum.webhook_url}</Typography.Text>
      </p>
      <ResizableTable
        rowKey="id"
        size="small"
        pagination={false}
        dataSource={sum.groups}
        columns={[
          { title: "研发组", dataIndex: "name" },
          {
            title: "Webhook Secret",
            dataIndex: "webhook_secret",
            render: (v) => (v ? <Typography.Text copyable code>{v}</Typography.Text> : "—"),
          },
          {
            title: "octo bot",
            dataIndex: "has_octo_bot_token",
            render: (v) =>
              v ? (
                <Tooltip title="本组用自己的 bot token 发群通知；在「研发组」页编辑里配置">
                  <Tag color="green">独立 bot</Tag>
                </Tooltip>
              ) : (
                <Tooltip title="未配置本组 token，发通知用全局 bot；可在「研发组」页编辑里填本组 token">
                  <Tag>全局 bot</Tag>
                </Tooltip>
              ),
          },
        ]}
      />
    </div>
  );
}

function GitlabEvents() {
  const { data, page, setPage, loading } = usePaged<any>("/api/integrations/events");
  return (
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
        { title: "事件", dataIndex: "event_type", render: (v) => <Tag>{v}</Tag> },
        { title: "组", dataIndex: "group_id", width: 70 },
        {
          title: "摘要",
          render: (_, r) => {
            const a = r.payload?.object_attributes || {};
            return a.title || a.ref || (r.payload?.project && r.payload.project.name) || "—";
          },
        },
        { title: "处理时间", dataIndex: "handled_at", render: (v) => (v ? v.slice(0, 19) : "未处理") },
      ]}
    />
  );
}

function NotifyLogs() {
  const { data, page, setPage, loading } = usePaged<any>("/api/integrations/notify-logs");
  return (
    <ResizableTable<any>
      rowKey="id"
      size="small"
      loading={loading}
      dataSource={data.items}
      pagination={{ current: page, pageSize: PAGE_SIZE, total: data.total, onChange: setPage }}
      columns={[
        { title: "ID", dataIndex: "id", width: 70 },
        { title: "原因", dataIndex: "reason", render: (v) => <Tag>{v}</Tag> },
        { title: "结果", dataIndex: "ok", render: (v) => (v ? <Tag color="green">已送达</Tag> : <Tag color="red">失败</Tag>) },
        {
          title: "内容",
          dataIndex: "content",
          ellipsis: true,
          render: (v) => <span style={{ whiteSpace: "pre-wrap" }}>{v}</span>,
        },
        { title: "@", dataIndex: "mentioned_uids", width: 90, render: (v: any[]) => (v?.length ? v.length : "—") },
        { title: "时间", dataIndex: "created_at", width: 160, render: (v) => v?.slice(0, 19) },
      ]}
    />
  );
}

function MrReviews() {
  const { data, page, setPage, loading } = usePaged<any>("/api/integrations/mr-reviews");
  return (
    <ResizableTable<any>
      rowKey="id"
      size="small"
      loading={loading}
      dataSource={data.items}
      pagination={{ current: page, pageSize: PAGE_SIZE, total: data.total, onChange: setPage }}
      columns={[
        { title: "MR", width: 110, render: (_, r) => <span className="mono">{r.project_path} !{r.mr_iid}</span> },
        { title: "标题", dataIndex: "title", ellipsis: true },
        {
          title: "初审",
          dataIndex: "verdict",
          width: 100,
          render: (v, r) => {
            const t = VERDICT[v] || ["default", v];
            return (
              <Tag color={t[0]}>
                {t[1]}
                {r.gate ? `（CI ${r.gate.replace("ci:", "")}）` : ""}
              </Tag>
            );
          },
        },
        {
          title: "放行",
          dataIndex: "flow_state",
          width: 110,
          render: (v) => {
            if (!v) return <Tag>待处理</Tag>;
            const t = FLOW[v] || ["default", v];
            return <Tag color={t[0]}>{t[1]}</Tag>;
          },
        },
        { title: "改动", dataIndex: "changed_files", width: 70, render: (v: any[]) => v?.length ?? 0 },
        { title: "摘要", dataIndex: "summary", ellipsis: true },
        { title: "提交", dataIndex: "head_sha", width: 100, render: (v) => <span className="mono">{v?.slice(0, 8)}</span> },
      ]}
    />
  );
}

export default function Integrations() {
  return (
    <Page title="集成中心" desc="Webhook 凭证 · 事件与通知历史 · MR 评审记录">
      <div className="page-card">
        <Tabs
          items={[
            { key: "cred", label: "凭证", children: <Credentials /> },
            { key: "events", label: "GitLab 事件", children: <GitlabEvents /> },
            { key: "notify", label: "通知记录", children: <NotifyLogs /> },
            { key: "mr", label: "MR 评审", children: <MrReviews /> },
          ]}
        />
      </div>
    </Page>
  );
}
