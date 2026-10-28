import { useEffect, useState } from "react";
import { Button, Divider, Form, Input, Modal, Popconfirm, Space, Tag, Tooltip, Typography, message } from "antd";
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
  const [rows, setRows] = useState<Node[]>([]);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const load = () => api<Node[]>("/api/runner-nodes").then(setRows);
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <Page
      title="Runner 节点"
      desc="执行节点（跑 claude -p），可部署任意多台。新建节点会一次性发放 token，关闭后无法再查"
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
            新建节点
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
            { title: "节点", dataIndex: "name", render: (v) => <span className="mono">{v}</span> },
            {
              title: "状态",
              dataIndex: "status",
              width: 100,
              render: (v) => (
                <Tag color={v === "online" ? "green" : "default"}>{v === "online" ? "在线" : "离线"}</Tag>
              ),
            },
            {
              title: "执行中",
              dataIndex: "running_tasks",
              width: 110,
              render: (v) =>
                v ? (
                  <Tooltip title={`${v} 个任务正在本节点执行（每个任务一个 claude 进程）`}>
                    <Tag color="processing">{v} 个 claude</Tag>
                  </Tooltip>
                ) : (
                  "—"
                ),
            },
            {
              title: "最近心跳",
              dataIndex: "last_heartbeat",
              render: (v) => (v ? <span className="mono">{v.slice(11, 19)}</span> : "—"),
              width: 110,
            },
            {
              title: "操作",
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />}>
                  <Popconfirm
                    title="删除该节点？正在执行的任务会失败。"
                    onConfirm={async () => {
                      await api(`/api/runner-nodes/${r.id}`, { method: "DELETE" });
                      load();
                    }}
                  >
                    <a className="danger-link">删除</a>
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
        title="新建 runner 节点"
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
                title: "节点已创建，token 只显示这一次",
                content: (
                  <div>
                    <p>
                      启动命令：
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
          <Form.Item name="name" label="节点名" rules={[{ required: true }]}>
            <Input placeholder="如 mac-runner-1" />
          </Form.Item>
        </Form>
      </Modal>
    </Page>
  );
}
