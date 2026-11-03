import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Popconfirm, Space, Tag, Tooltip, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import Page from "../Page";
import { PAGE } from "../table";

interface Node {
  id: number;
  name: string;
  status: string;
  last_heartbeat: string | null;
  running_tasks: number;
}

export default function Nodes() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Node[]>([]);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const load = () => api<Node[]>("/api/runner-nodes").then(setRows);
  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, []);

  return (
    <Page
      title={t("nav.nodes")}
      desc={t("nodes.desc")}
      actions={
        isAdmin() && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              form.resetFields();
              setOpen(true);
            }}
          >
            {t("nodes.new")}
          </Button>
        )
      }
    >
      <div className="page-card">
        <ResizableTable<Node>
          rowKey="id"
          dataSource={rows}
          pagination={PAGE}
          columns={[
            { title: t("nodes.colName"), dataIndex: "name", render: (v) => <span className="mono">{v}</span> },
            {
              title: t("common.status"),
              dataIndex: "status",
              width: 100,
              render: (v) => (
                <Tag color={v === "online" ? "green" : "default"}>
                  {v === "online" ? t("nodes.online") : t("nodes.offline")}
                </Tag>
              ),
            },
            {
              title: t("nodes.colRunning"),
              dataIndex: "running_tasks",
              width: 110,
              render: (v) =>
                v ? (
                  <Tooltip title={t("nodes.runningTip", { n: v })}>
                    <Tag color="processing">{t("nodes.runningTag", { n: v })}</Tag>
                  </Tooltip>
                ) : (
                  "—"
                ),
            },
            {
              title: t("nodes.colHeartbeat"),
              dataIndex: "last_heartbeat",
              render: (v) => (v ? <span className="mono">{v.slice(11, 19)}</span> : "—"),
              width: 110,
            },
            {
              title: t("common.actions"),
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />}>
                  <Popconfirm
                    title={t("nodes.deleteConfirm")}
                    onConfirm={async () => {
                      await api(`/api/runner-nodes/${r.id}`, { method: "DELETE" });
                      load();
                    }}
                  >
                    <a className="danger-link">{t("common.delete")}</a>
                  </Popconfirm>
                </Space>
              ),
              width: 80,
            },
          ]}
        />
      </div>
      <Modal
        open={open}
        title={t("nodes.createTitle")}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ name: "" }}
          onFinish={async (v: any) => {
            try {
              const d = await api<any>("/api/runner-nodes", {
                method: "POST",
                body: JSON.stringify(v),
              });
              setOpen(false);
              load();
              Modal.success({
                title: t("nodes.createdTitle"),
                content: (
                  <div>
                    <p>
                      {t("nodes.startCommand")}
                      <br />
                      <Typography.Text code copyable>
                        {`VEGA_API_URL=http://<api地址>:8000 VEGA_RUNNER_TOKEN=${d.token} VEGA_RUNNER_NAME=${d.name} python -m vega.runner`}
                      </Typography.Text>
                    </p>
                  </div>
                ),
              });
            } catch (e: any) {
              message.error(e.message);
            }
          }}
        >
          <Form.Item name="name" label={t("nodes.nameLabel")} rules={[{ required: true }]}>
            <Input placeholder={t("nodes.namePlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}
