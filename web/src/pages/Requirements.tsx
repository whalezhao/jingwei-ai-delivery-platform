import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Divider, Empty, Form, Input, Modal, Popconfirm, Select, Space, Tag, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { CheckCircleFilled, CheckCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import { useGroup } from "../group";
import Page from "../Page";
import { PAGE } from "../table";
import type { MsgKey } from "../i18n";

interface Req {
  id: number;
  system_id: number;
  system_name: string | null;
  code: string;
  title: string;
  description: string | null;
  status: string;
  iteration_status: string;
  tags: string[];
  claimant_id: number | null;
  claimant_name: string | null;
  // 需求提出人：产品认领并定稿文档后留痕在此，认领权已交给开发
  proposer_id: number | null;
  proposer_name: string | null;
  plan_md: string | null;
  created_at: string | null;
  // 后端按当前登录人算出的两个开关：能不能认领、能不能打开详情
  can_claim: boolean;
  can_open: boolean;
}

const STATUS: Record<string, [string, MsgKey]> = {
  pool: ["default", "requirements.status.pool"],
  claimed: ["processing", "requirements.status.claimed"],
  confirmed: ["success", "requirements.status.confirmed"],
  archived: ["default", "requirements.status.archived"],
  online: ["cyan", "requirements.status.online"],
};

const ITER: Record<string, [string, MsgKey]> = {
  iterating: ["blue", "requirements.iter.iterating"],
  formal: ["green", "requirements.iter.formal"],
};

const SETUP_ITEMS: [keyof Setup, MsgKey, string][] = [
  ["gitlab", "requirements.setupGitlab", "/groups"],
  ["system", "requirements.setupProject", "/systems"],
  ["people", "requirements.setupPeople", "/people"],
  ["am", "requirements.setupAm", "/systems"],
  ["runner", "requirements.setupRunner", "/nodes"],
];

interface Setup {
  gitlab: boolean;
  system: boolean;
  people: boolean;
  am: boolean;
  runner: boolean;
}

export default function Requirements() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Req[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [projFilter, setProjFilter] = useState<number | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [iterFilter, setIterFilter] = useState<string | undefined>();
  const [tagFilter, setTagFilter] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Req | null>(null);
  const [form] = Form.useForm();

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projFilter) p.set("system_id", String(projFilter));
    setRows(await api<Req[]>(`/api/requirements?${p}`));
    setProjects(await api<any[]>(`/api/systems${groupId ? `?group_id=${groupId}` : ""}`));
    if (isAdmin()) setSetup(await api<Setup>("/api/setup"));
  };
  useEffect(() => {
    load();
  }, [projFilter, groupId]);
  // 切组后旧的项目筛选可能已不属于该组，清掉
  useEffect(() => {
    setProjFilter(undefined);
  }, [groupId]);

  const openEdit = (r: Req) => {
    setEditing(r);
    setOpen(true);
    form.setFieldsValue({
      system_id: r.system_id,
      title: r.title,
      description: r.description,
    });
  };

  const saveTags = async (r: Req, tags: string[]) => {
    try {
      const updated = await api<Req>(`/api/requirements/${r.id}/tags`, {
        method: "PUT",
        body: JSON.stringify({ tags }),
      });
      setRows((prev) => prev.map((x) => (x.id === r.id ? updated : x)));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const remove = async (r: Req) => {
    try {
      await api(`/api/requirements/${r.id}`, { method: "DELETE" });
      message.success(t("requirements.deleted", { code: r.code }));
      setRows((prev) => prev.filter((x) => x.id !== r.id));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const claim = async (r: Req) => {
    try {
      await api(`/api/requirements/${r.id}/claim`, { method: "POST" });
      message.success(t("requirements.claimed"));
      nav(`/reqs/${r.id}`);
    } catch (e: any) {
      message.error(e.message);
    }
  };

  // 列表本来就整页取回（无服务端分页），三个维度直接在前端过滤
  const tagOptions = Array.from(new Set(rows.flatMap((r) => r.tags || []))).sort();
  const term = keyword.trim().toLowerCase();
  const shown = rows.filter(
    (r) =>
      (!statusFilter || r.status === statusFilter) &&
      (!iterFilter || r.iteration_status === iterFilter) &&
      (!tagFilter || (r.tags || []).includes(tagFilter)) &&
      (!term ||
        [r.code, r.title, r.description, r.system_name, r.claimant_name, r.proposer_name]
          .some((s) => (s || "").toLowerCase().includes(term))),
  );
  const filtering = !!(projFilter || statusFilter || iterFilter || tagFilter || term);

  return (
    <Page
      title={t("nav.requirements")}
      desc={t("requirements.desc")}
      actions={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing(null);
            setOpen(true);
            form.resetFields();
          }}
        >
          {t("requirements.new")}
        </Button>
      }
    >
      {setup && Object.values(setup).some((v) => !v) && (
        <div className="setup-card">
          <div className="setup-head">{t("requirements.setupHead")}</div>
          <div className="setup-items">
            {SETUP_ITEMS.map(([k, label, to]) => (
              <a key={k} className={"setup-item" + (setup[k] ? " done" : "")} onClick={() => nav(to)}>
                {setup[k] ? (
                  <CheckCircleFilled style={{ color: "#52c41a" }} />
                ) : (
                  <CheckCircleOutlined style={{ color: "rgba(0,0,0,0.25)" }} />
                )}
                {t(label)}
              </a>
            ))}
          </div>
        </div>
      )}
      <div className="page-card">
        <div style={{ marginBottom: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Input.Search
            style={{ width: 240 }}
            allowClear
            placeholder={t("requirements.searchPlaceholder")}
            onSearch={(v) => setKeyword(v)}
            onChange={(e) => !e.target.value && setKeyword("")}
          />
          <Select
            style={{ width: 280 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("requirements.filterProject")}
            value={projFilter}
            onChange={setProjFilter}
            options={projects.map((p) => ({ value: p.id, label: `${p.path}（${p.code_prefix}）` }))}
          />
          <Select
            style={{ width: 150 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("requirements.filterStatus")}
            value={statusFilter}
            onChange={setStatusFilter}
            options={Object.entries(STATUS).map(([v, pair]) => ({ value: v, label: t(pair[1]) }))}
          />
          <Select
            style={{ width: 150 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("requirements.filterIter")}
            value={iterFilter}
            onChange={setIterFilter}
            options={Object.entries(ITER).map(([v, pair]) => ({ value: v, label: t(pair[1]) }))}
          />
          <Select
            style={{ width: 170 }}
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("requirements.filterTag")}
            value={tagFilter}
            onChange={setTagFilter}
            options={tagOptions.map((tag) => ({ value: tag, label: tag }))}
          />
        </div>
        <ResizableTable<Req>
          rowKey="id"
          dataSource={shown}
          pagination={PAGE}
          rowClassName={() => "row-click"}
          onRow={(r) => ({
            onClick: () => (r.can_open ? nav(`/reqs/${r.id}`) : message.info(t("requirements.cannotOpen"))),
          })}
          locale={{
            emptyText: (
              <Empty
                description={filtering ? t("requirements.emptyFiltered") : t("requirements.empty")}
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                style={{ padding: "32px 0" }}
              >
                <Typography.Text type="secondary">{t("requirements.emptyHint")}</Typography.Text>
              </Empty>
            ),
          }}
          columns={[
            {
              title: t("requirements.colCode"),
              dataIndex: "code",
              width: 235,
              render: (v) => <span className="mono">{v}</span>,
            },
            {
              title: t("requirements.colTitle"),
              dataIndex: "title",
              ellipsis: true,
              render: (v, r) =>
                r.can_open ? (
                  <a
                    onClick={(e) => {
                      e.stopPropagation();
                      nav(`/reqs/${r.id}`);
                    }}
                  >
                    {v}
                  </a>
                ) : (
                  <span className="muted">{v}</span>
                ),
            },
            {
              title: t("requirements.colProject"),
              dataIndex: "system_name",
              width: 130,
              ellipsis: true,
              render: (v) => v || "—",
            },
            {
              title: t("requirements.colStatus"),
              dataIndex: "status",
              width: 110,
              render: (v) => {
                const pair = STATUS[v];
                return <Tag color={pair?.[0] ?? "default"}>{pair ? t(pair[1]) : v}</Tag>;
              },
            },
            {
              title: t("requirements.colIter"),
              dataIndex: "iteration_status",
              width: 100,
              render: (v) => {
                const pair = ITER[v];
                return <Tag color={pair?.[0] ?? "default"}>{pair ? t(pair[1]) : v}</Tag>;
              },
            },
            {
              title: t("requirements.colTag"),
              dataIndex: "tags",
              width: 150,
              render: (v: string[] | undefined, r) =>
                // 无权打开详情的人也不该能改标签（后端同样会拒）
                !r.can_open ? (
                  <span className="muted">{(v || []).join("、") || "—"}</span>
                ) : (
                  <div onClick={(e) => e.stopPropagation()}>
                    <Select
                      mode="tags"
                      size="small"
                      variant="borderless"
                      style={{ width: "100%" }}
                      placeholder={t("requirements.tagPlaceholder")}
                      value={v || []}
                      tokenSeparators={[",", "，"]}
                      onChange={(tags: string[]) => saveTags(r, tags)}
                    />
                  </div>
                ),
            },
            {
              title: t("requirements.colClaimant"),
              dataIndex: "claimant_name",
              width: 110,
              render: (v, r) =>
                v ? (
                  v
                ) : r.proposer_name ? (
                  // 定稿后认领权已交还开发，产品留痕为提出人
                  <span className="muted">{t("requirements.proposedBy", { name: r.proposer_name })}</span>
                ) : (
                  "—"
                ),
            },
            {
              title: t("requirements.colActions"),
              width: 150,
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />}>
                  {r.can_claim ? (
                    <a
                      onClick={(e) => {
                        e.stopPropagation();
                        claim(r);
                      }}
                    >
                      {t("requirements.claim")}
                    </a>
                  ) : r.can_open ? (
                    <a
                      onClick={(e) => {
                        e.stopPropagation();
                        nav(`/reqs/${r.id}`);
                      }}
                    >
                      {r.status === "claimed" ? t("requirements.continueDiscuss") : t("requirements.view")}
                    </a>
                  ) : null}
                  {r.can_open &&
                    r.status !== "confirmed" &&
                    r.status !== "archived" &&
                    r.status !== "online" && (
                      <a
                        onClick={(e) => {
                          e.stopPropagation();
                          openEdit(r);
                        }}
                      >
                        {t("requirements.edit")}
                      </a>
                    )}
                  {(isAdmin() || r.claimant_id === Number(localStorage.getItem("vega_me"))) && (
                    <Popconfirm
                      title={t("requirements.deleteConfirm")}
                      description={t("requirements.deleteDesc")}
                      okButtonProps={{ danger: true }}
                      onConfirm={() => remove(r)}
                    >
                      <a className="danger-link" onClick={(e) => e.stopPropagation()}>
                        {t("requirements.delete")}
                      </a>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </div>
      <Modal
        open={open}
        title={editing ? t("requirements.editTitle") : t("requirements.createTitle")}
        onCancel={() => {
          setOpen(false);
          setEditing(null);
        }}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ system_id: projects[0]?.id }}
          onFinish={async (v) => {
            try {
              if (editing) {
                await api(`/api/requirements/${editing.id}`, {
                  method: "PUT",
                  body: JSON.stringify({
                    title: v.title,
                    description: v.description ?? null,
                  }),
                });
                message.success(t("requirements.saved"));
                setOpen(false);
                setEditing(null);
                load();
              } else {
                const r = await api<Req>("/api/requirements", { method: "POST", body: JSON.stringify(v) });
                message.success(t("requirements.created", { code: r.code }));
                setOpen(false);
                nav(`/reqs/${r.id}`);
              }
            } catch (e: any) {
              message.error(e.message);
            }
          }}
        >
          <Form.Item
            name="system_id"
            label={t("requirements.projectLabel")}
            rules={[{ required: true }]}
            tooltip={t("requirements.projectTooltip")}
          >
            <Select
              disabled={!!editing}
              showSearch
              optionFilterProp="label"
              options={projects.map((p) => ({
                value: p.id,
                label: `${p.path}（${p.code_prefix}）`,
              }))}
            />
          </Form.Item>
          <Form.Item name="title" label={t("requirements.titleLabel")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label={t("requirements.descLabel")}>
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}