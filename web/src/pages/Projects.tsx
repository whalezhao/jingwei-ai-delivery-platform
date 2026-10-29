import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Popconfirm, Select, Space, Tag, message } from "antd";
import ResizableTable from "../ResizableTable";
import { PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import { useGroup } from "../group";
import AmSelect from "../components/AmSelect";
import Page from "../Page";
import { PAGE } from "../table";

interface Project {
  id: number;
  group_id: number;
  gitlab_project_id: number;
  path: string;
  name: string;
  code_prefix: string;
  default_branch: string;
  mr_target_branch: string;
  tech_lead_person_ids: number[];
  owner_person_ids: number[];
  audit_prefixes: string[] | null;
  auto_approve_prefixes: string[] | null;
  am_team_id: string | null;
  am_agent_id: string | null;
}

/** 前缀规则在界面上是一个字符串：多个前缀用分号隔开，* = 匹配全部文件
 *  （库里存空前缀，后端 startswith("") 恒真）。空串不能留：它会被当成匹配全部。 */
const prefixToView = (xs: string[] | null | undefined) =>
  (xs || []).map((x) => (x === "" ? "*" : x)).join(";");
const prefixToDb = (s: string | null | undefined) =>
  (s || "")
    .split(/[;；,，\n]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => (x === "*" ? "" : x));

export default function Projects() {
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Project[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<any>(null);
  const [keyword, setKeyword] = useState("");
  const [form] = Form.useForm();
  // 专家列表随团队：项目团队 → 未选回退研发组团队
  const watchTeam: string | undefined = Form.useWatch("am_team_id", form);
  const effTeam =
    watchTeam || groups.find((g) => g.id === editing?.group_id)?.am_team_id || undefined;

  const load = async () => {
    setRows(await api<Project[]>(`/api/projects${groupId ? `?group_id=${groupId}` : ""}`));
    setGroups(await api<any[]>("/api/groups"));
    setPeople(await api<any[]>("/api/people"));
  };
  useEffect(() => {
    load();
  }, [groupId]);

  const open = (p?: Project) => {
    setEditing(
      p
        ? {
            ...p,
            audit_prefixes: prefixToView(p.audit_prefixes),
            auto_approve_prefixes: prefixToView(p.auto_approve_prefixes),
          }
        : {
            group_id: groupId ?? groups[0]?.id,
            gitlab_project_id: "",
            path: "",
            name: "",
            code_prefix: "",
            default_branch: "master",
            mr_target_branch: "nightly",
            tech_lead_person_ids: [],
            owner_person_ids: [],
            audit_prefixes: "",
            auto_approve_prefixes: "",
            am_team_id: "",
            am_agent_id: "",
          },
    );
    form.resetFields();
  };

  const save = async (v: any) => {
    const body = {
      ...v,
      audit_prefixes: prefixToDb(v.audit_prefixes),
      auto_approve_prefixes: prefixToDb(v.auto_approve_prefixes),
    };
    try {
      if (editing.id) await api(`/api/projects/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/projects", { method: "POST", body: JSON.stringify(body) });
      message.success("已保存");
      setEditing(null);
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const gname = (gid: number) => groups.find((g) => g.id === gid)?.name || gid;
  const pname = (pid: number) => people.find((p) => p.id === pid)?.name || pid;
  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [r.path, r.name, r.code_prefix, r.default_branch, r.mr_target_branch, gname(r.group_id)]
          .some((s) => String(s ?? "").toLowerCase().includes(term)),
      )
    : rows;

  return (
    <Page
      title="项目"
      desc="需求编号前缀 · 默认/MR 目标分支 · 技术负责人（日报接收 / 卡 MR 升级对象）/ 项目负责人"
      actions={
        isAdmin() && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => open()}>
            新建项目
          </Button>
        )
      }
    >
      <div className="page-card">
        <Input.Search
          style={{ width: 280, marginBottom: 12 }}
          allowClear
          placeholder="项目路径 / 名称 / 编号前缀 / 研发组"
          onSearch={(v) => setKeyword(v)}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
        <ResizableTable<Project>
          rowKey="id"
          dataSource={shown}
          pagination={PAGE}
          columns={[
            {
              title: "项目",
              dataIndex: "path",
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: "研发组", render: (_, r) => gname(r.group_id) },
            {
              title: "GitLab ID",
              dataIndex: "gitlab_project_id",
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: "编号前缀", dataIndex: "code_prefix", render: (v) => <Tag color="blue">{v}</Tag> },
            { title: "默认分支", dataIndex: "default_branch", render: (v) => <span className="mono">{v}</span> },
            {
              title: "MR 目标分支",
              dataIndex: "mr_target_branch",
              render: (v) => <span className="mono">{v}</span>,
            },
            {
              title: "技术负责人",
              render: (_, r) =>
                r.tech_lead_person_ids.length ? r.tech_lead_person_ids.map(pname).join("、") : "—",
            },
            {
              title: "项目负责人",
              render: (_, r) => (r.owner_person_ids.length ? r.owner_person_ids.map(pname).join("、") : "—"),
            },
            {
              title: "审计/放行前缀",
              render: (_, r) => (
                <>
                  {r.audit_prefixes?.length ? (
                    <Tag color="geekblue">
                      审 {r.audit_prefixes.includes("") ? "全部" : r.audit_prefixes.length}
                    </Tag>
                  ) : null}
                  {r.auto_approve_prefixes?.length ? (
                    <Tag color="green">
                      放 {r.auto_approve_prefixes.includes("") ? "全部" : r.auto_approve_prefixes.length}
                    </Tag>
                  ) : null}
                  {!r.audit_prefixes?.length && !r.auto_approve_prefixes?.length ? "—" : null}
                </>
              ),
            },
            {
              title: "操作",
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  <a onClick={() => open(r)}>编辑</a>
                  {isAdmin() && (
                    <Popconfirm
                      title="删除该项目？"
                      onConfirm={async () => {
                        try {
                          await api(`/api/projects/${r.id}`, { method: "DELETE" });
                          load();
                        } catch (e: any) {
                          message.error(e.message);
                        }
                      }}
                    >
                      <a className="danger-link">删除</a>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </div>
      <Modal
        open={!!editing}
        title={editing?.id ? "编辑项目" : "新建项目"}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || {}} onFinish={save}>
          <Form.Item name="group_id" label="研发组" rules={[{ required: true }]}>
            <Select
              disabled={!!editing?.id}
              showSearch
              optionFilterProp="label"
              options={groups.map((g) => ({ value: g.id, label: g.name }))}
            />
          </Form.Item>
          <Form.Item name="gitlab_project_id" label="GitLab 项目 ID" rules={[{ required: true }]}>
            <Input disabled={!!editing?.id} />
          </Form.Item>
          <Form.Item name="path" label="仓库路径（group/repo）" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label="项目名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code_prefix" label="需求编号前缀" rules={[{ required: true }]}>
            <Input maxLength={12} placeholder="如 FUS" />
          </Form.Item>
          <Form.Item name="default_branch" label="默认分支">
            <Input />
          </Form.Item>
          <Form.Item name="mr_target_branch" label="MR 目标分支" tooltip="AI 开发完成后向该分支提交 MR">
            <Input />
          </Form.Item>
          <Form.Item name="tech_lead_person_ids" label="技术负责人（日报接收 / 卡 MR 升级对象）">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="owner_person_ids" label="项目负责人">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item
            name="audit_prefixes"
            label="AI 审计前缀（MR 改动命中才触发 CRG 初审；留空 = 不审）"
            tooltip="前缀匹配规则：多个前缀用分号隔开，如 api/; src/core/；* = 匹配全部文件"
          >
            <Input placeholder="api/; src/core/ 或 *" />
          </Form.Item>
          <Form.Item
            name="auto_approve_prefixes"
            label="自动放行前缀（初审可批准 + CI 绿 + 全部改动命中才自动合并；留空 = 不放行）"
            tooltip="前缀匹配规则：多个前缀用分号隔开；fail-closed，改动文件只要有一个不命中就不会自动合并；* = 全部文件"
          >
            <Input placeholder="docs/; src/" />
          </Form.Item>
          <Form.Item
            name="am_team_id"
            label="agent-memory 团队（项目级绑定，随 AI 调用直选注入团队资产）"
            tooltip="展开实时拉取团队列表；留空回退研发组配置"
          >
            <AmSelect kind="team" placeholder="留空回退研发组配置" />
          </Form.Item>
          <Form.Item name="am_agent_id" label="agent-memory 专家（可选，指定团队内某专家）" tooltip="专家列表随上面的团队加载">
            <AmSelect kind="agent" teamId={effTeam} placeholder="留空用团队默认专家" />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}
