import { useEffect, useState } from "react";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import { toast } from "../ui/toast";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { Input } from "../components/motion/input";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import {
  MultiSelect,
  MultiSelectContent,
  MultiSelectEmpty,
  MultiSelectInput,
  MultiSelectItem,
  MultiSelectList,
  MultiSelectTrigger,
  MultiSelectValue,
} from "../components/motion/multi-select";

interface Project {
  id: number;
  name: string;
  code: string | null;
  octo_group_no: string | null;
  leader_person_ids: number[];
  group_ids: number[];
}

const empty = {
  name: "",
  code: "",
  octo_group_no: "",
  leader_person_ids: [] as number[],
};

/** 项目（顶层）：公司按项目分割团队，项目下挂多个研发组；项目群只放研发组负责人。 */
export default function Projects() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Project[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<Project | null>(null);
  const [keyword, setKeyword] = useState("");
  const form = useForm(empty);

  const load = async () => {
    setRows(await api<Project[]>("/api/projects"));
    setGroups(await api<any[]>("/api/groups"));
    setPeople(await api<any[]>("/api/people"));
  };
  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    if (!form.validate({ name: { required: true, message: t("common.required") } }))
      return;
    try {
      if (editing?.id)
        await api(`/api/projects/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify(form.values),
        });
      else
        await api("/api/projects", {
          method: "POST",
          body: JSON.stringify(form.values),
        });
      toast.success(t("common.saved"));
      setEditing(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (row: Project) => {
    const ok = await confirm({
      title: t("projects.deleteConfirm"),
      confirmText: t("common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/projects/${row.id}`, { method: "DELETE" });
      toast.success(t("common.saved"));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const pname = (pid: number) => people.find((p) => p.id === pid)?.name || pid;
  const gnames = (ids: number[]) =>
    ids.length
      ? ids
          .map((id) => groups.find((g) => g.id === id)?.name || id)
          .join(t("projects.listSep"))
      : "—";
  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [
          r.name,
          r.code,
          r.octo_group_no,
          ...r.leader_person_ids.map(pname),
          ...r.group_ids.map((id) => groups.find((g) => g.id === id)?.name),
        ].some((s) => String(s ?? "").toLowerCase().includes(term)),
      )
    : rows;

  const columns: DataTableColumn<Project>[] = [
    {
      title: t("projects.colName"),
      dataIndex: "name",
      render: (v) => <span className="font-medium text-foreground">{v}</span>,
    },
    {
      title: t("projects.colCode"),
      dataIndex: "code",
      width: 130,
      render: (v) =>
        v ? (
          <span className="inline-flex rounded-md bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-foreground">
            {v}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      title: t("projects.colOcto"),
      dataIndex: "octo_group_no",
      width: 150,
      render: (v) =>
        v ? (
          <span className="font-mono text-[12px]">{v}</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      title: t("projects.colLeaders"),
      width: 200,
      render: (_, r) =>
        r.leader_person_ids.length
          ? r.leader_person_ids.map(pname).join(t("projects.listSep"))
          : "—",
    },
    {
      title: t("projects.colGroups"),
      render: (_, r) => gnames(r.group_ids),
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 120,
      render: (_, r) =>
        isAdmin() ? (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => {
                form.reset({
                  name: r.name,
                  code: r.code ?? "",
                  octo_group_no: r.octo_group_no ?? "",
                  leader_person_ids: [...r.leader_person_ids],
                });
                setEditing(r);
              }}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label={t("common.edit")}
            >
              <Pencil className="size-3.5" />
            </button>
            <button
              type="button"
              onClick={() => remove(r)}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              aria-label={t("common.delete")}
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ) : null,
    },
  ];

  return (
    <Page
      title={t("nav.projects")}
      desc={t("projects.desc")}
      actions={
        isAdmin() ? (
          <Button
            size="sm"
            className="rounded-lg"
            onClick={() => {
              form.reset(empty);
              setEditing({ id: 0 } as Project);
            }}
          >
            <Plus className="size-4" />
            {t("projects.new")}
          </Button>
        ) : null
      }
    >
      <Input
        value={keyword}
        onChange={setKeyword}
        placeholder={t("projects.searchPlaceholder")}
        leftIcon={<Search />}
        classNames={{ field: "rounded-lg" }}
        className="mb-3 max-w-sm"
      />

      <DataTable<Project>
        rowKey="id"
        dataSource={shown}
        columns={columns}
        emptyText={t("common.noData")}
      />

      <CenterMorphModal
        open={!!editing}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={
            editing?.id ? t("projects.editTitle") : t("projects.createTitle")
          }
          className="max-w-[520px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {editing?.id ? t("projects.editTitle") : t("projects.createTitle")}
            </h2>
            <div className="mt-5 flex flex-col gap-4">
              <Input
                label={t("projects.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("projects.code")}
                value={form.values.code}
                onChange={(v) => form.set("code", v)}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("projects.octoGroup")}
                value={form.values.octo_group_no}
                onChange={(v) => form.set("octo_group_no", v)}
                placeholder={t("projects.octoGroupPlaceholder")}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("projects.leaders")}>
                <MultiSelect
                  value={form.values.leader_person_ids.map(String)}
                  onValueChange={(values) =>
                    form.set("leader_person_ids", values.map(Number))
                  }
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue placeholder={t("projects.leaders")} />
                    <MultiSelectInput
                      aria-label={t("common.search")}
                      placeholder={t("common.search")}
                      className="h-7 min-w-24 text-sm outline-none placeholder:text-muted-foreground"
                    />
                  </MultiSelectTrigger>
                  <MultiSelectContent>
                    <MultiSelectList>
                      {people.map((p) => (
                        <MultiSelectItem
                          key={p.id}
                          value={String(p.id)}
                          textValue={p.name}
                        >
                          {p.name}
                        </MultiSelectItem>
                      ))}
                      <MultiSelectEmpty>{t("common.noData")}</MultiSelectEmpty>
                    </MultiSelectList>
                  </MultiSelectContent>
                </MultiSelect>
              </Field>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setEditing(null)}
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
    </Page>
  );
}