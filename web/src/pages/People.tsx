import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Popconfirm, Select, Space, Switch, Tag, message } from "antd";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import type { MsgKey } from "../i18n";
import { useGroup } from "../group";
import Page from "../Page";
import PersonAvatar from "../components/PersonAvatar";
import AvatarPicker from "../components/AvatarPicker";
import { PAGE } from "../table";

interface Person {
  id: number;
  name: string;
  username: string | null;
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
  roles: string[];
}

// 研发体系角色：product 认领未定稿需求并写需求，dev 定稿后接管做方案与开发；
// 其余三个目前只用于标识与展示，不参与认领门禁
const ROLES: { value: string; label: MsgKey; color: string }[] = [
  { value: "project_lead", label: "people.role.projectLead", color: "magenta" },
  { value: "pm", label: "people.role.pm", color: "blue" },
  { value: "product", label: "people.role.product", color: "cyan" },
  { value: "tech_lead", label: "people.role.techLead", color: "purple" },
  { value: "dev", label: "people.role.dev", color: "green" },
  { value: "qa", label: "people.role.qa", color: "orange" },
];

const ROLE_LABEL: Record<string, MsgKey> = Object.fromEntries(ROLES.map((r) => [r.value, r.label]));

const empty: any = {
  name: "",
  username: "",
  gitlab_username: "",
  gitlab_email: "",
  password: "",
  is_admin: false,
  roles: [],
  llm_base_url: "",
  llm_api_key: "",
  llm_model: "",
  llm_effort: "",
  am_user_key: "",
};

export default function People() {
  const { t } = useTranslation();
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Person[]>([]);
  const [editing, setEditing] = useState<any>(null); // {id?: number, ...}
  const [keyword, setKeyword] = useState("");
  const [form] = Form.useForm();
  // 个人 GitLab 账号（一个可多个；研发组里选一个用）
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accBase, setAccBase] = useState("");
  const [accToken, setAccToken] = useState("");
  const [accBusy, setAccBusy] = useState(false);

  const loadAccounts = (pid: number) =>
    api<any[]>(`/api/people/${pid}/gitlab-accounts`)
      .then(setAccounts)
      .catch(() => setAccounts([]));

  const addAccount = async () => {
    if (!editing?.id || !accBase.trim() || !accToken.trim()) return;
    setAccBusy(true);
    try {
      await api(`/api/people/${editing.id}/gitlab-accounts`, {
        method: "POST",
        body: JSON.stringify({ base_url: accBase.trim(), token: accToken.trim() }),
      });
      setAccBase("");
      setAccToken("");
      await loadAccounts(editing.id);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setAccBusy(false);
    }
  };

  const delAccount = async (aid: number) => {
    if (!editing?.id) return;
    try {
      await api(`/api/people/${editing.id}/gitlab-accounts/${aid}`, { method: "DELETE" });
      await loadAccounts(editing.id);
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const load = () => api<Person[]>(`/api/people${groupId ? `?group_id=${groupId}` : ""}`).then(setRows);
  useEffect(() => {
    load();
  }, [groupId]);

  const open = (p?: Person) => {
    // Modal destroyOnHidden：每次打开都会重新挂载 Form，initialValues 取这里的新对象
    setEditing(p ? { ...p, password: "", llm_api_key: "", am_user_key: "" } : { ...empty });
    setAccBase("");
    setAccToken("");
    if (p) loadAccounts(p.id);
    else setAccounts([]);
  };

  const save = async (v: any) => {
    const body: any = { ...v };
    // 空 key 不提交，避免把已有配置清掉
    for (const k of ["password", "llm_api_key", "am_user_key"]) if (!body[k]) delete body[k];
    // 非管理员：登录名/管理员标记/角色/重置密码都不许改（表单回传的是只读值，不是修改意图）
    if (!isAdmin()) for (const k of ["is_admin", "username", "gitlab_username", "password", "roles"]) delete body[k];
    try {
      if (editing.id) await api(`/api/people/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/people", { method: "POST", body: JSON.stringify(body) });
      message.success(t("common.saved"));
      setEditing(null);
      // 改的是自己 → 通知顶栏头像立即刷新
      if (String(editing.id) === localStorage.getItem("vega_me")) window.dispatchEvent(new Event("vega-me-updated"));
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [r.name, r.username, r.gitlab_username, r.gitlab_email].some((s) => (s || "").toLowerCase().includes(term)),
      )
    : rows;

  return (
    <Page
      title={t("nav.people")}
      desc={t("people.desc")}
      actions={
        isAdmin() && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => open()}>
            {t("people.new")}
          </Button>
        )
      }
    >
      <div className="page-card">
        <Input.Search
          style={{ width: 260, marginBottom: 12 }}
          allowClear
          placeholder={t("people.searchPlaceholder")}
          onSearch={(v) => setKeyword(v)}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
        <ResizableTable<Person>
          rowKey="id"
          dataSource={shown}
          pagination={PAGE}
          columns={[
            {
              title: t("people.colName"),
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
              title: t("people.colUsername"),
              dataIndex: "username",
              render: (v) => <span className="mono">{v || "—"}</span>,
            },
            {
              title: t("people.colGitlab"),
              dataIndex: "gitlab_username",
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: t("people.colEmail"), dataIndex: "gitlab_email" },
            {
              title: t("people.colOcto"),
              dataIndex: "octo_uid",
              render: (v) => (v ? <span className="mono">{v}</span> : <Tag>{t("people.unbound")}</Tag>),
            },
            {
              title: t("people.colLlm"),
              render: (_, r) => (r.has_llm_key ? r.llm_model || t("people.default") : "—"),
            },
            {
              title: t("people.colRoles"),
              dataIndex: "roles",
              width: 170,
              render: (v: string[]) =>
                (v || []).length ? (
                  <Space size={4} wrap>
                    {v.map((x) => (
                      <Tag key={x} color={ROLES.find((r) => r.value === x)?.color}>
                        {ROLE_LABEL[x] ? t(ROLE_LABEL[x]) : x}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  <Tag>{t("people.notSet")}</Tag>
                ),
            },
            { title: t("people.colAmKey"), render: (_, r) => (r.has_am_key ? t("people.configured") : "—") },
            { title: t("people.colAdmin"), dataIndex: "is_admin", render: (v) => (v ? <Tag color="gold">admin</Tag> : null) },
            {
              title: t("common.actions"),
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  {(isAdmin() || String(r.id) === localStorage.getItem("vega_me")) && (
                    <a onClick={() => open(r)}>{t("common.edit")}</a>
                  )}
                  {isAdmin() && (
                    <Popconfirm
                      title={t("people.deleteConfirm")}
                      onConfirm={async () => {
                        try {
                          await api(`/api/people/${r.id}`, { method: "DELETE" });
                          load();
                        } catch (e: any) {
                          message.error(e.message);
                        }
                      }}
                    >
                      <a className="danger-link">{t("common.delete")}</a>
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
        title={editing?.id ? t("people.editTitle") : t("people.createTitle")}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || empty} onFinish={save}>
          <Form.Item name="name" label={t("people.name")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="avatar" label={t("people.avatar")}>
            <AvatarPicker />
          </Form.Item>
          <Form.Item name="username" label={t("people.username")} tooltip={t("people.usernameTooltip")}>
            <Input disabled={!isAdmin()} />
          </Form.Item>
          {editing?.id && (
            <Form.Item label={t("people.gitlabAccounts")} tooltip={t("people.gitlabAccountsTooltip")}>
              <Space direction="vertical" style={{ width: "100%" }}>
                {accounts.map((a) => (
                  <Space key={a.id} wrap>
                    <Tag color={a.has_token ? "green" : "red"}>{a.username}</Tag>
                    <span className="mono">{a.base_url}</span>
                    <a className="danger-link" onClick={() => delAccount(a.id)}>
                      {t("common.delete")}
                    </a>
                  </Space>
                ))}
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    placeholder={t("people.accountBaseUrl")}
                    value={accBase}
                    onChange={(e) => setAccBase(e.target.value)}
                  />
                  <Input.Password
                    placeholder={t("people.accountToken")}
                    value={accToken}
                    onChange={(e) => setAccToken(e.target.value)}
                  />
                  <Button onClick={addAccount} loading={accBusy} disabled={!isAdmin()}>
                    {t("people.addAccount")}
                  </Button>
                </Space.Compact>
                <span style={{ fontSize: 12, opacity: 0.65 }}>{t("people.accountHint")}</span>
              </Space>
            </Form.Item>
          )}
          <Form.Item name="gitlab_username" label={t("people.gitlabUsername")}>
            <Input disabled={!!editing?.id} />
          </Form.Item>
          <Form.Item name="gitlab_email" label={t("people.gitlabEmail")}>
            <Input />
          </Form.Item>
          {/* 非管理员改自己不能重置密码：走顶栏「个人设置」里的修改密码（校验旧密码） */}
          {isAdmin() && (
            <Form.Item
              name="password"
              label={editing?.id ? t("people.resetPassword") : t("people.initialPassword")}
            >
              <Input.Password />
            </Form.Item>
          )}
          <Form.Item name="is_admin" label={t("people.admin")} valuePropName="checked">
            <Switch disabled={!isAdmin()} />
          </Form.Item>
          <Form.Item
            name="roles"
            label={t("people.roles")}
            tooltip={t("people.roleTooltip")}
            extra={t("people.roleExtra")}
          >
            <Select
              mode="multiple"
              disabled={!isAdmin()}
              showSearch
              optionFilterProp="label"
              placeholder={t("people.rolePlaceholder")}
              options={ROLES.map((r) => ({ value: r.value, label: t(r.label) }))}
            />
          </Form.Item>
          <Form.Item name="llm_base_url" label={t("people.llmBaseUrl")}>
            <Input placeholder="https://…/v1" />
          </Form.Item>
          <Form.Item name="llm_api_key" label={t("people.llmApiKey")}>
            <Input.Password placeholder={editing?.has_llm_key ? t("people.configuredKeep") : ""} />
          </Form.Item>
          <Form.Item name="llm_model" label={t("people.llmModel")}>
            <Input />
          </Form.Item>
          <Form.Item name="llm_effort" label={t("people.effort")} tooltip={t("people.effortTooltip")}>
            <Select
              showSearch
              optionFilterProp="label"
              options={[
                { value: "", label: t("people.effortUnset") },
                { value: "low", label: t("people.effortLow") },
                { value: "medium", label: t("people.effortMedium") },
                { value: "high", label: t("people.effortHigh") },
                { value: "xhigh", label: t("people.effortXhigh") },
                { value: "max", label: t("people.effortMax") },
              ]}
            />
          </Form.Item>
          <Form.Item name="am_user_key" label={t("people.amKey")}>
            <Input.Password placeholder={editing?.has_am_key ? t("people.configuredKeep") : ""} />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}
