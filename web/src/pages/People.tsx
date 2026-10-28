import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Popconfirm, Select, Space, Switch, Tag, message } from "antd";
import ResizableTable from "../ResizableTable";
import { PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import { useGroup } from "../group";
import Page from "../Page";
import PersonAvatar from "../components/PersonAvatar";
import AvatarPicker from "../components/AvatarPicker";
import { PAGE } from "../table";

interface Person {
  id: number;
  name: string;
  avatar: string | null;
  gitlab_username: string | null;
  gitlab_email: string | null;
  octo_uid: string | null;
  is_admin: boolean;
  llm_base_url: string | null;
  llm_model: string | null;
  llm_effort: string | null;
  has_llm_key: boolean;
  has_am_key: boolean;
}

const empty: any = {
  name: "",
  gitlab_username: "",
  gitlab_email: "",
  password: "",
  is_admin: false,
  llm_base_url: "",
  llm_api_key: "",
  llm_model: "",
  llm_effort: "",
  am_user_key: "",
};

export default function People() {
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Person[]>([]);
  const [editing, setEditing] = useState<any>(null); // {id?: number, ...}
  const [form] = Form.useForm();

  const load = () => api<Person[]>(`/api/people${groupId ? `?group_id=${groupId}` : ""}`).then(setRows);
  useEffect(() => {
    load();
  }, [groupId]);

  const open = (p?: Person) => {
    // Modal destroyOnHidden：每次打开都会重新挂载 Form，initialValues 取这里的新对象
    setEditing(p ? { ...p, password: "", llm_api_key: "", am_user_key: "" } : { ...empty });
  };

  const save = async (v: any) => {
    const body: any = { ...v };
    // 空 key 不提交，避免把已有配置清掉
    for (const k of ["password", "llm_api_key", "am_user_key"]) if (!body[k]) delete body[k];
    // 非管理员：登录名/管理员标记/重置密码都不许改（表单回传的是只读值，不是修改意图）
    if (!isAdmin()) for (const k of ["is_admin", "gitlab_username", "password"]) delete body[k];
    try {
      if (editing.id) await api(`/api/people/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/people", { method: "POST", body: JSON.stringify(body) });
      message.success("已保存");
      setEditing(null);
      // 改的是自己 → 通知顶栏头像立即刷新
      if (String(editing.id) === localStorage.getItem("vega_me")) window.dispatchEvent(new Event("vega-me-updated"));
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  return (
    <Page
      title="人员"
      desc="GitLab 账号 · 个人 LLM（缺省用研发组兜底）· agent-memory key"
      actions={
        isAdmin() && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => open()}>
            新建人员
          </Button>
        )
      }
    >
      <div className="page-card">
        <ResizableTable<Person>
          rowKey="id"
          dataSource={rows}
          pagination={PAGE}
          columns={[
            {
              title: "姓名",
              dataIndex: "name",
              width: 190,
              render: (v, r) => (
                <Space>
                  <PersonAvatar name={v} avatar={r.avatar} />
                  {v}
                </Space>
              ),
            },
            {
              title: "GitLab 用户名",
              dataIndex: "gitlab_username",
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: "邮箱", dataIndex: "gitlab_email" },
            {
              title: "octo",
              dataIndex: "octo_uid",
              render: (v) => (v ? <span className="mono">{v}</span> : <Tag>未绑定</Tag>),
            },
            {
              title: "LLM",
              render: (_, r) => (r.has_llm_key ? r.llm_model || "默认" : "—"),
            },
            { title: "am key", render: (_, r) => (r.has_am_key ? "已配置" : "—") },
            { title: "管理员", dataIndex: "is_admin", render: (v) => (v ? <Tag color="gold">admin</Tag> : null) },
            {
              title: "操作",
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  {(isAdmin() || String(r.id) === localStorage.getItem("vega_me")) && (
                    <a onClick={() => open(r)}>编辑</a>
                  )}
                  {isAdmin() && (
                    <Popconfirm
                      title="删除该人员？"
                      onConfirm={async () => {
                        try {
                          await api(`/api/people/${r.id}`, { method: "DELETE" });
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
        title={editing?.id ? "编辑人员" : "新建人员"}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || empty} onFinish={save}>
          <Form.Item name="name" label="姓名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="avatar" label="头像">
            <AvatarPicker />
          </Form.Item>
          <Form.Item name="gitlab_username" label="GitLab 用户名（登录名）">
            <Input disabled={!!editing?.id} />
          </Form.Item>
          <Form.Item name="gitlab_email" label="GitLab 邮箱">
            <Input />
          </Form.Item>
          {/* 非管理员改自己不能重置密码：走顶栏「个人设置」里的修改密码（校验旧密码） */}
          {isAdmin() && (
            <Form.Item name="password" label={editing?.id ? "重置密码（留空不改）" : "初始密码"}>
              <Input.Password />
            </Form.Item>
          )}
          <Form.Item name="is_admin" label="管理员" valuePropName="checked">
            <Switch disabled={!isAdmin()} />
          </Form.Item>
          <Form.Item name="llm_base_url" label="LLM Base URL（个人，留空用研发组兜底）">
            <Input placeholder="https://…/v1" />
          </Form.Item>
          <Form.Item name="llm_api_key" label="LLM API Key（只写不回显）">
            <Input.Password placeholder={editing?.has_llm_key ? "已配置，留空不改" : ""} />
          </Form.Item>
          <Form.Item name="llm_model" label="LLM Model">
            <Input />
          </Form.Item>
          <Form.Item name="llm_effort" label="思考等级" tooltip="透传 claude --effort；默认 Qwen 上游只认 xhigh/medium/low，high/max 适合 claude 系或自定义上游">
            <Select
              options={[
                { value: "", label: "未设置（继承研发组/兜底 xhigh）" },
                { value: "low", label: "低（low）" },
                { value: "medium", label: "中（medium）" },
                { value: "high", label: "高（high · claude 默认）" },
                { value: "xhigh", label: "特高（xhigh · Qwen 最高档）" },
                { value: "max", label: "最高（max）" },
              ]}
            />
          </Form.Item>
          <Form.Item name="am_user_key" label="agent-memory 用户 key（sk-mem-…，只写不回显）">
            <Input.Password placeholder={editing?.has_am_key ? "已配置，留空不改" : ""} />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}
