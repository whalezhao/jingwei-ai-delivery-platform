import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge from "../ui/Badge";
import CopyText from "../ui/CopyText";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Input } from "../components/motion/input";
import { Tooltip } from "../components/motion/tooltip";

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
  const [created, setCreated] = useState<{ token: string; name: string } | null>(
    null,
  );
  const form = useForm({ name: "" });

  const load = () => api<Node[]>("/api/runner-nodes").then(setRows);
  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, []);

  const save = async () => {
    if (
      !form.validate({
        name: { required: true, message: t("common.required") },
      })
    )
      return;
    try {
      const d = await api<any>("/api/runner-nodes", {
        method: "POST",
        body: JSON.stringify(form.values),
      });
      setOpen(false);
      load();
      setCreated(d);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (row: Node) => {
    const ok = await confirm({
      title: t("nodes.deleteConfirm"),
      confirmText: t("common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/runner-nodes/${row.id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const columns: DataTableColumn<Node>[] = [
    {
      title: t("nodes.colName"),
      dataIndex: "name",
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    {
      title: t("common.status"),
      dataIndex: "status",
      width: 100,
      render: (v) =>
        v === "online" ? (
          <Badge tone="success">{t("nodes.online")}</Badge>
        ) : (
          <Badge>{t("nodes.offline")}</Badge>
        ),
    },
    {
      title: t("nodes.colRunning"),
      dataIndex: "running_tasks",
      width: 120,
      render: (v) =>
        v ? (
          <Tooltip content={t("nodes.runningTip", { n: v })}>
            <span className="inline-flex">
              <Badge tone="info">{t("nodes.runningTag", { n: v })}</Badge>
            </span>
          </Tooltip>
        ) : (
          "—"
        ),
    },
    {
      title: t("nodes.colHeartbeat"),
      dataIndex: "last_heartbeat",
      width: 110,
      render: (v) =>
        v ? <span className="font-mono text-[12px]">{v.slice(11, 19)}</span> : "—",
    },
    {
      title: t("common.actions"),
      width: 80,
      render: (_, r) =>
        isAdmin() ? (
          <button
            type="button"
            onClick={() => remove(r)}
            aria-label={t("common.delete")}
            className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="size-3.5" />
          </button>
        ) : null,
    },
  ];

  return (
    <Page
      title={t("nav.nodes")}
      desc={t("nodes.desc")}
      actions={
        isAdmin() ? (
          <Button
            size="sm"
            className="rounded-lg"
            onClick={() => {
              form.reset({ name: "" });
              setOpen(true);
            }}
          >
            <Plus className="size-4" />
            {t("nodes.new")}
          </Button>
        ) : null
      }
    >
      <DataTable<Node>
        rowKey="id"
        dataSource={rows}
        columns={columns}
        emptyText={t("common.noData")}
      />

      <CenterMorphModal
        open={open}
        onOpenChange={(next) => {
          if (!next) setOpen(false);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={t("nodes.createTitle")}
          className="max-w-[420px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {t("nodes.createTitle")}
            </h2>
            <div className="mt-5">
              <Input
                label={t("nodes.nameLabel")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                placeholder={t("nodes.namePlaceholder")}
                error={form.errors.name}
                classNames={{ field: "rounded-lg" }}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setOpen(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={save}>
                {t("common.save")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>

      <CenterMorphModal
        open={!!created}
        onOpenChange={(next) => {
          if (!next) setCreated(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={t("nodes.createdTitle")}
          className="max-w-[560px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {t("nodes.createdTitle")}
            </h2>
            {created ? (
              <div className="mt-4 flex flex-col gap-2">
                <span className="text-[13px] text-muted-foreground">
                  {t("nodes.startCommand")}
                </span>
                <CopyText
                  value={`VEGA_API_URL=http://<api地址>:8000 VEGA_RUNNER_TOKEN=${created.token} VEGA_RUNNER_NAME=${created.name} python -m vega.runner`}
                />
              </div>
            ) : null}
            <div className="mt-6 flex justify-end">
              <Button
                size="sm"
                className="rounded-lg"
                onClick={() => setCreated(null)}
              >
                {t("common.close")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </Page>
  );
}