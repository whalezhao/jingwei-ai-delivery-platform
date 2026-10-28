import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Select, message } from "antd";
import AvatarPicker from "./AvatarPicker";
import { api } from "../api";

/** 登录人自己的资料（PersonOut 的前端影子） */
export interface Me {
  id: number;
  name: string;
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
      message.success("已保存");
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
      message.error("两次输入的新密码不一致");
      return;
    }
    try {
      await api("/api/auth/password", { method: "POST", body: JSON.stringify({ old: v.old, new: v.new }) });
      message.success("密码已修改");
      pwForm.resetFields();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  return (
    <Modal
      open={open}
      title="个人设置"
      width={560}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      confirmLoading={saving}
      destroyOnHidden
    >
      {me && (
        <>
          <Form form={form} layout="vertical" onFinish={save}>
            <Form.Item name="avatar" label="头像">
              <AvatarPicker />
            </Form.Item>
            <Form.Item name="name" label="姓名" rules={[{ required: true, message: "请输入姓名" }]}>
              <Input />
            </Form.Item>
            <Form.Item name="gitlab_email" label="GitLab 邮箱">
              <Input placeholder="用于推送/MR 事件按邮箱对回本人" />
            </Form.Item>
            <Form.Item label="登录名 / octo 绑定">
              <Input value={`${me.gitlab_username || "—"} · octo: ${me.octo_uid || "未绑定（管理员维护）"}`} disabled />
            </Form.Item>
            <Form.Item label="角色（管理员维护）">
              <Input value={(me.roles || []).join("、") || "未设置"} disabled />
            </Form.Item>
            <Divider plain style={{ fontSize: 12, margin: "8px 0" }}>
              个人 LLM 配置（留空继承研发组/兜底）
            </Divider>
            <Form.Item name="llm_base_url" label="LLM Base URL">
              <Input placeholder="https://…/v1" />
            </Form.Item>
            <Form.Item name="llm_api_key" label="LLM API Key（只写不回显）">
              <Input.Password placeholder={me.has_llm_key ? "已配置，留空不改" : ""} />
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
              <Input.Password placeholder={me.has_am_key ? "已配置，留空不改" : ""} />
            </Form.Item>
          </Form>
          <Divider plain style={{ fontSize: 12, margin: "8px 0" }}>
            修改密码
          </Divider>
          <Form form={pwForm} layout="vertical" onFinish={changePw}>
            <Form.Item name="old" label="旧密码" rules={[{ required: true, message: "请输入旧密码" }]}>
              <Input.Password />
            </Form.Item>
            <Form.Item name="new" label="新密码" rules={[{ required: true, message: "至少 6 位" }, { min: 6, message: "至少 6 位" }]}>
              <Input.Password />
            </Form.Item>
            <Form.Item name="confirm" label="确认新密码" rules={[{ required: true, message: "再输一次新密码" }]}>
              <Input.Password />
            </Form.Item>
            <Button type="primary" ghost onClick={() => pwForm.submit()}>
              修改密码
            </Button>
          </Form>
        </>
      )}
    </Modal>
  );
}
