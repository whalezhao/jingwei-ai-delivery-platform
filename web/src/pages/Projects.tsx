import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Popconfirm, Select, Space, Tag, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { api, isAdmin } from "../api";
import Page from "../Page";
import { PAGE } from "../table";

interface Project {
  id: number;
  name: string;
  code: string | null;
  octo_group_no: string | null;
  leader_person_ids: number[];
  group_ids: number[];
}

const empty: any = { name: "", code: "", octo_group_no: "", leader_person_ids: [] };

/** 项目（顶层）：公司按项目分割团队，项目下挂多个研发组；项目群只放研发组负责人。 */
export default function Projects() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Project[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<any>(null);
  const [keyword, setKeyword] = useState("");
  const [form] = Form.useForm();

  const load = async () => {
    setRows(await api<Project[]>("/api/projects"));
    setGroups(await api<any[]>("/api/groups"));
    setPeople(await api<any[]>("/api/people"));
  };
  useEffect(() => {
    load();
  }, []);

  const save = async (v: any) => {
    try {
      if (editing.id) await api(`/api/projects/${editing.id}`, { method: "PUT", body: JSON.stringify(v) });
      else await api("/api/projects", { method: "POST", body: JSON.stringify(v) });
      message.success(t("common.saved"));
      setEditing(null);
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const pname = (pid: number) => people.find((p) => p.id === pid)?.name || pid;
  const gnames = (ids: number[]) =>
    ids.length ? ids.map((id) => groups.find((g) => g.id === id)?.name || id).join(t("projects.listSep")) : "—";
  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [r.name, r.code, r.octo_group_no, ...r.leader_person_ids.map(pname), ...r.group_ids.map((id) => groups.find((g) => g.id === id)?.name)]
          .some((s) => String(s ?? "").toLowerCase().includes(term)),
      )
    : rows;

  return (
    <Page
      title={t("nav.projects")}
      desc={t("projects.desc")}
      actions={
        isAdmin() && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setEditing({ ...empty });
              form.resetFields();
            }}
          >
            {t("projects.new")}
          </Button>
        )
      }
    >
      <div className="page-card">
        <Input.Search
          style={{ width: 280, marginBottom: 12 }}
          allowClear
          placeholder={t("projects.searchPlaceholder")}
          onSearch={(v) => setKeyword(v)}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
        <ResizableTable<Project>
          rowKey="id"
          dataSource={shown}
          pagination={PAGE}
          columns={[
            { title: t("projects.colName"), dataIndex: "name" },
            { title: t("projects.colCode"), dataIndex: "code", render: (v) => (v ? <Tag color="blue">{v}</Tag> : "—") },
            {
              title: t("projects.colOcto"),
              dataIndex: "octo_group_no",
              render: (v) => (v ? <span className="mono">{v}</span> : "—"),
            },
            {
              title: t("projects.colLeaders"),
              render: (_, r) =>
                r.leader_person_ids.length ? r.leader_person_ids.map(pname).join(t("projects.listSep")) : "—",
            },
            { title: t("projects.colGroups"), render: (_, r) => gnames(r.group_ids) },
            {
              title: t("common.actions"),
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  {isAdmin() && <a onClick={() => setEditing({ ...r })}>{t("common.edit")}</a>}
                  {isAdmin() && (
                    <Popconfirm
                      title={t("projects.deleteConfirm")}
                      onConfirm={async () => {
                        try {
                          await api(`/api/projects/${r.id}`, { method: "DELETE" });
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
        title={editing?.id ? t("projects.editTitle") : t("projects.createTitle")}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || empty} onFinish={save}>
          <Form.Item name="name" label={t("projects.name")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label={t("projects.code")}>
            <Input />
          </Form.Item>
          <Form.Item name="octo_group_no" label={t("projects.octoGroup")} tooltip={t("projects.octoGroupTooltip")}>
            <Input placeholder={t("projects.octoGroupPlaceholder")} />
          </Form.Item>
          <Form.Item name="leader_person_ids" label={t("projects.leaders")}>
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}