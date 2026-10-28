import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Divider, Empty, Form, Input, Modal, Select, Space, Tag, Typography, message } from "antd";
import ResizableTable from "../ResizableTable";
import { CheckCircleFilled, CheckCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import { useGroup } from "../group";
import Page from "../Page";
import { PAGE } from "../table";

interface Req {
  id: number;
  project_id: number;
  project_name: string | null;
  code: string;
  title: string;
  description: string | null;
  status: string;
  iteration_status: string;
  tags: string[];
  claimant_id: number | null;
  claimant_name: string | null;
  plan_md: string | null;
  created_at: string | null;
}

const STATUS: Record<string, [string, string]> = {
  pool: ["default", "池中"],
  claimed: ["processing", "方案讨论中"],
  confirmed: ["success", "已定稿"],
  archived: ["default", "已归档"],
  online: ["cyan", "已上线"],
};

const ITER: Record<string, [string, string]> = {
  iterating: ["blue", "迭代中"],
  formal: ["green", "正式"],
};

const SETUP_ITEMS: [keyof Setup, string, string][] = [
  ["gitlab", "研发组接入 GitLab", "/groups"],
  ["project", "项目已导入", "/projects"],
  ["people", "人员 GitLab 账号已录", "/people"],
  ["am", "agent-memory 已绑定", "/projects"],
  ["runner", "Runner 节点在线", "/nodes"],
];

interface Setup {
  gitlab: boolean;
  project: boolean;
  people: boolean;
  am: boolean;
  runner: boolean;
}

export default function Requirements() {
  const nav = useNavigate();
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Req[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [projFilter, setProjFilter] = useState<number | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [iterFilter, setIterFilter] = useState<string | undefined>();
  const [tagFilter, setTagFilter] = useState<string | undefined>();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Req | null>(null);
  const [form] = Form.useForm();

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projFilter) p.set("project_id", String(projFilter));
    setRows(await api<Req[]>(`/api/requirements?${p}`));
    setProjects(await api<any[]>(`/api/projects${groupId ? `?group_id=${groupId}` : ""}`));
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
      project_id: r.project_id,
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

  const claim = async (r: Req) => {
    try {
      await api(`/api/requirements/${r.id}/claim`, { method: "POST" });
      message.success("已认领，进入对话制定方案");
      nav(`/reqs/${r.id}`);
    } catch (e: any) {
      message.error(e.message);
    }
  };

  // 列表本来就整页取回（无服务端分页），三个维度直接在前端过滤
  const tagOptions = Array.from(new Set(rows.flatMap((r) => r.tags || []))).sort();
  const shown = rows.filter(
    (r) =>
      (!statusFilter || r.status === statusFilter) &&
      (!iterFilter || r.iteration_status === iterFilter) &&
      (!tagFilter || (r.tags || []).includes(tagFilter)),
  );
  const filtering = !!(projFilter || statusFilter || iterFilter || tagFilter);

  return (
    <Page
      title="需求池"
      desc="上传需求 → 认领 → AI 对话定方案 → 建任务派发"
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
          新建需求
        </Button>
      }
    >
      {setup && Object.values(setup).some((v) => !v) && (
        <div className="setup-card">
          <div className="setup-head">初始化进度 · 全部完成前，需求→开发的全流程跑不通</div>
          <div className="setup-items">
            {SETUP_ITEMS.map(([k, label, to]) => (
              <a key={k} className={"setup-item" + (setup[k] ? " done" : "")} onClick={() => nav(to)}>
                {setup[k] ? (
                  <CheckCircleFilled style={{ color: "#52c41a" }} />
                ) : (
                  <CheckCircleOutlined style={{ color: "rgba(0,0,0,0.25)" }} />
                )}
                {label}
              </a>
            ))}
          </div>
        </div>
      )}
      <div className="page-card">
        <div style={{ marginBottom: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Select
            style={{ width: 280 }}
            allowClear
            placeholder="按项目筛选（项目下才有需求池）"
            value={projFilter}
            onChange={setProjFilter}
            options={projects.map((p) => ({ value: p.id, label: `${p.path}（${p.code_prefix}）` }))}
          />
          <Select
            style={{ width: 150 }}
            allowClear
            placeholder="按状态筛选"
            value={statusFilter}
            onChange={setStatusFilter}
            options={Object.entries(STATUS).map(([v, t]) => ({ value: v, label: t[1] }))}
          />
          <Select
            style={{ width: 150 }}
            allowClear
            placeholder="按迭代状态筛选"
            value={iterFilter}
            onChange={setIterFilter}
            options={Object.entries(ITER).map(([v, t]) => ({ value: v, label: t[1] }))}
          />
          <Select
            style={{ width: 170 }}
            allowClear
            placeholder="按迭代版本筛选"
            value={tagFilter}
            onChange={setTagFilter}
            options={tagOptions.map((t) => ({ value: t, label: t }))}
          />
        </div>
        <ResizableTable<Req>
          rowKey="id"
          dataSource={shown}
          pagination={PAGE}
          rowClassName={() => "row-click"}
          onRow={(r) => ({ onClick: () => nav(`/reqs/${r.id}`) })}
          locale={{
            emptyText: (
              <Empty
                description={filtering ? "没有符合筛选条件的需求" : "需求池还是空的"}
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                style={{ padding: "32px 0" }}
              >
                <Typography.Text type="secondary">
                  先由管理员建好项目，然后在这里录入第一个需求
                </Typography.Text>
              </Empty>
            ),
          }}
          columns={[
            {
              title: "编号",
              dataIndex: "code",
              width: 235,
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: "标题", dataIndex: "title", ellipsis: true, render: (v, r) => <a onClick={(e) => { e.stopPropagation(); nav(`/reqs/${r.id}`); }}>{v}</a> },
            {
              title: "项目",
              dataIndex: "project_name",
              width: 130,
              ellipsis: true,
              render: (v) => v || "—",
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 110,
              render: (v) => {
                const t = STATUS[v] || ["default", v];
                return <Tag color={t[0]}>{t[1]}</Tag>;
              },
            },
            {
              title: "迭代状态",
              dataIndex: "iteration_status",
              width: 100,
              render: (v) => {
                const t = ITER[v] || ["default", v];
                return <Tag color={t[0]}>{t[1]}</Tag>;
              },
            },
            {
              title: "迭代版本",
              dataIndex: "tags",
              width: 150,
              render: (v: string[] | undefined, r) => (
                <div onClick={(e) => e.stopPropagation()}>
                  <Select
                    mode="tags"
                    size="small"
                    variant="borderless"
                    style={{ width: "100%" }}
                    placeholder="加迭代版本标签"
                    value={v || []}
                    tokenSeparators={[",", "，"]}
                    onChange={(tags: string[]) => saveTags(r, tags)}
                  />
                </div>
              ),
            },
            { title: "认领人", dataIndex: "claimant_name", render: (v) => v || "—", width: 80 },
            {
              title: "操作",
              width: 150,
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />}>
                  {r.status === "pool" ? (
                    <a
                      onClick={(e) => {
                        e.stopPropagation();
                        claim(r);
                      }}
                    >
                      认领
                    </a>
                  ) : (
                    <a
                      onClick={(e) => {
                        e.stopPropagation();
                        nav(`/reqs/${r.id}`);
                      }}
                    >
                      {r.status === "claimed" ? "继续讨论" : "查看"}
                    </a>
                  )}
                  {r.status !== "confirmed" && r.status !== "archived" && r.status !== "online" && (
                    <a
                      onClick={(e) => {
                        e.stopPropagation();
                        openEdit(r);
                      }}
                    >
                      编辑
                    </a>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </div>
      <Modal
        open={open}
        title={editing ? "编辑需求" : "新建需求"}
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
          initialValues={{ project_id: projects[0]?.id }}
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
                message.success("已保存");
                setOpen(false);
                setEditing(null);
                load();
              } else {
                const r = await api<Req>("/api/requirements", { method: "POST", body: JSON.stringify(v) });
                message.success(`已创建 ${r.code}，可上传需求文档`);
                setOpen(false);
                nav(`/reqs/${r.id}`);
              }
            } catch (e: any) {
              message.error(e.message);
            }
          }}
        >
          <Form.Item name="project_id" label="项目" rules={[{ required: true }]} tooltip="项目创建后不可修改">
            <Select
              disabled={!!editing}
              options={projects.map((p) => ({
                value: p.id,
                label: `${p.path}（${p.code_prefix}）`,
              }))}
            />
          </Form.Item>
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}
