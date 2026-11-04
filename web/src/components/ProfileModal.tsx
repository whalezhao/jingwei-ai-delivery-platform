import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Select, message } from "antd";
import { useTranslation } from "react-i18next";
import AvatarPicker from "./AvatarPicker";
import { api } from "../api";

/** 登录人自己的资料（PersonOut 的前端影子） */
export interface Me {
  id: number;
  name: string;
  username: string | null;
  avatar: string | null;
  gitlab_username: string | null;
  gitlab_email: string | null;
  octo_uid: string | null;
  llm_base_url: string | null;
  llm_model: string | null;
  llm_effort: string | null;
  has_llm_key: boolean;
  has_am_key: boolean;
  roles: string[];
}

/** 个人设置：基本信息/头像 + 个人 LLM 配置 + am key + 改密码。
 *  后端门禁：只能改自己；登录名、octo 绑定、管理员标记仅管理员可改。 */
export default function ProfileModal({
  open,
  me,
  onClose,
}: {
  open: boolean;
  me: Me | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [pwForm] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open && me) {
      form.setFieldsValue({
        name: me.name,
        gitlab_email: me.gitlab_email,
        avatar: me.avatar || "",
        llm_base_url: me.llm_base_url,
        llm_model: me.llm_model,
        llm_effort: me.llm_effort || "",
        llm_api_key: "",
        am_user_key: "",
      });
      pwForm.resetFields();
    }
  }, [open, me]);

  const save = async (v: any) => {
    if (!me) return;
    const body: Record<string, unknown> = {
      name: v.name,
      gitlab_email: v.gitlab_email || null,
      avatar: v.avatar || "",
      llm_base_url: v.llm_base_url || null,
      llm_model: v.llm_model || null,
      llm_effort: v.llm_effort || "",
    };
    for (const k of ["llm_api_key", "am_user_key"]) if (v[k]) body[k] = v[k];
    setSaving(true);
    try {
      await api(`/api/people/${me.id}`, { method: "PUT", body: JSON.stringify(body) });
      message.success(t("profileModal.saved"));
      window.dispatchEvent(new Event("vega-me-updated"));
      onClose();
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const changePw = async (v: any) => {
    if (v.new !== v.confirm) {
      message.error(t("profileModal.pwMismatch"));
      return;
    }
    try {
      await api("/api/auth/password", { method: "POST", body: JSON.stringify({ old: v.old, new: v.new }) });
      message.success(t("profileModal.pwChanged"));
      pwForm.resetFields();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  return (
    <Modal
      open={open}
      title={t("profileModal.title")}
      width={560}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText={t("profileModal.save")}
      confirmLoading={saving}
      destroyOnHidden
    >
      {me && (
        <>
          <Form form={form} layout="vertical" onFinish={save}>
            <Form.Item name="avatar" label={t("people.avatar")}>
              <AvatarPicker />
            </Form.Item>
            <Form.Item name="name" label={t("people.name")} rules={[{ required: true, message: t("profileModal.nameRequired") }]}>
              <Input />
            </Form.Item>
            <Form.Item name="gitlab_email" label={t("people.gitlabEmail")}>
              <Input placeholder={t("profileModal.emailPlaceholder")} />
            </Form.Item>
            <Form.Item label={t("profileModal.loginLabel")}>
              <Input
                value={`${me.username || "—"} · octo: ${me.octo_uid || t("profileModal.octoUnbound")}`}
                disabled
              />
            </Form.Item>
            <Form.Item label={t("profileModal.rolesLabel")}>
              <Input value={(me.roles || []).join(t("profileModal.roleSep")) || t("people.notSet")} disabled />
            </Form.Item>
            <Divider plain style={{ fontSize: 12, margin: "8px 0" }}>
              {t("profileModal.llmSection")}
            </Divider>
            <Form.Item name="llm_base_url" label="LLM Base URL">
              <Input placeholder="https://…/v1" />
            </Form.Item>
            <Form.Item name="llm_api_key" label={t("profileModal.apiKeyLabel")}>
              <Input.Password placeholder={me.has_llm_key ? t("profileModal.configuredKeep") : ""} />
            </Form.Item>
            <Form.Item name="llm_model" label="LLM Model">
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
            <Form.Item name="am_user_key" label={t("profileModal.amKeyLabel")}>
              <Input.Password placeholder={me.has_am_key ? t("profileModal.configuredKeep") : ""} />
            </Form.Item>
          </Form>
          <Divider plain style={{ fontSize: 12, margin: "8px 0" }}>
            {t("profileModal.pwSection")}
          </Divider>
          <Form form={pwForm} layout="vertical" onFinish={changePw}>
            <Form.Item name="old" label={t("profileModal.oldPw")} rules={[{ required: true, message: t("profileModal.oldPwRequired") }]}>
              <Input.Password />
            </Form.Item>
            <Form.Item
              name="new"
              label={t("profileModal.newPw")}
              rules={[{ required: true, message: t("profileModal.pwMin") }, { min: 6, message: t("profileModal.pwMin") }]}
            >
              <Input.Password />
            </Form.Item>
            <Form.Item name="confirm" label={t("profileModal.confirmPw")} rules={[{ required: true, message: t("profileModal.confirmRequired") }]}>
              <Input.Password />
            </Form.Item>
            <Button type="primary" ghost onClick={() => pwForm.submit()}>
              {t("profileModal.pwSection")}
            </Button>
          </Form>
        </>
      )}
    </Modal>
  );
}
