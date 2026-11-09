import { useEffect, useState } from "react";
import { Pencil, Plus, Search, Settings2, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import { qa, type QaEnv, type QaVar } from "../qa";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import MiniTable from "../ui/MiniTable";
import { toast } from "../ui/toast";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { Input } from "../components/motion/input";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Drawer } from "../components/motion/drawer";
import { Switch } from "../components/motion/switch";
import type { TableColumn } from "../components/motion/table";
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

type VarRow = QaVar & { uid: string };

// 变量行没有稳定 id（新增行 id 为空），用模块级序号作 key
let varUid = 0;
const nextUid = () => `var-${++varUid}`;

/** 项目（顶层）：公司按项目分割团队，项目下挂多个研发组；项目群只放研发组负责人。 */
export default function Projects() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Project[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<Project | null>(null);
  const [keyword, setKeyword] = useState("");
  const form = useForm(empty);

  // 项目环境：项目下所有系统共用一套环境（一份被测地址 + 一组变量）
  const [envProject, setEnvProject] = useState<Project | null>(null);
  const [envs, setEnvs] = useState<QaEnv[]>([]);
  const [envEditing, setEnvEditing] = useState<Partial<QaEnv> | null>(null);
  const envForm = useForm({ name: "", base_url: "" });
  const [varEnv, setVarEnv] = useState<QaEnv | null>(null);
  const [varRows, setVarRows] = useState<VarRow[]>([]);
  const [cleared, setCleared] = useState<string[]>([]);

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

  const loadEnvs = async (projectId: number) => {
    try {
      setEnvs(await qa<QaEnv[]>(`/api/projects/${projectId}/environments`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const openEnvs = (project: Project) => {
    setEnvProject(project);
    setEnvs([]);
    loadEnvs(project.id);
  };

  const openEnv = (env?: QaEnv) => {
    if (!envProject) return;
    setEnvEditing(env ?? { project_id: envProject.id });
    envForm.reset({ name: env?.name ?? "", base_url: env?.base_url ?? "" });
  };

  const saveEnv = async () => {
    if (!envProject) return;
    if (!envEditing?.id && !envForm.values.name.trim()) return;
    const body = { name: envForm.values.name, base_url: envForm.values.base_url || null };
    try {
      if (envEditing?.id) {
        await qa(`/api/environments/${envEditing.id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
      } else {
        await qa(`/api/projects/${envProject.id}/environments`, {
          method: "POST",
          body: JSON.stringify(body),
        });
      }
      toast.success(t("common.saved"));
      setEnvEditing(null);
      loadEnvs(envProject.id);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const removeEnv = async (env: QaEnv) => {
    const ok = await confirm({
      title: t("qa.env.deleteConfirm"),
      confirmText: t("common.delete"),
      danger: true,
    });
    if (!ok || !envProject) return;
    try {
      await qa(`/api/environments/${env.id}`, { method: "DELETE" });
      loadEnvs(envProject.id);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const openVars = (env: QaEnv) => {
    setVarEnv(env);
    setVarRows(env.variables.map((v) => ({ ...v, value: "", uid: nextUid() })));
    setCleared([]);
  };

  const saveVars = async () => {
    if (!varEnv || !envProject) return;
    try {
      await qa(`/api/environments/${varEnv.id}/variables`, {
        method: "PUT",
        body: JSON.stringify({
          variables: [
            ...varRows
              .filter((v) => v.key.trim())
              .map((v) => ({
                key: v.key.trim(),
                value: v.value || null,
                is_secret: v.is_secret,
                description: v.description,
              })),
            ...cleared.map((key) => ({ key, clear: true })),
          ],
        }),
      });
      toast.success(t("common.saved"));
      setVarEnv(null);
      loadEnvs(envProject.id);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const patchVar = (uid: string, patch: Partial<VarRow>) =>
    setVarRows((rs) => rs.map((x) => (x.uid === uid ? { ...x, ...patch } : x)));

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
      width: 160,
      render: (_, r) => (
        <div className="flex items-center gap-2 text-[12px]">
          <button
            type="button"
            onClick={() => openEnvs(r)}
            className="inline-flex items-center gap-1 whitespace-nowrap text-primary hover:underline"
          >
            <Settings2 className="size-3.5" />
            {t("qa.env.title")}
          </button>
          {isAdmin() && (
            <>
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
            </>
          )}
        </div>
      ),
    },
  ];

  const envColumns: TableColumn<QaEnv>[] = [
    {
      key: "name",
      header: t("qa.common.name"),
      cell: (r) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate">{r.name}</span>
          {r.is_default && (
            <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
              {t("qa.env.default")}
            </span>
          )}
        </span>
      ),
    },
    {
      key: "base_url",
      header: t("qa.env.baseUrl"),
      cell: (r) => (
        <span className="truncate font-mono text-[12px]">{r.base_url || "—"}</span>
      ),
    },
    {
      key: "vars",
      header: t("qa.env.vars"),
      width: "80px",
      cell: (r) => r.variables.length,
    },
    {
      key: "actions",
      header: t("common.actions"),
      width: "180px",
      cell: (r) => (
        <div className="flex items-center gap-2 text-[12px]">
          <button
            type="button"
            onClick={() => openVars(r)}
            className="whitespace-nowrap text-primary hover:underline"
          >
            {t("qa.env.vars")}
          </button>
          <button
            type="button"
            onClick={() => openEnv(r)}
            className="whitespace-nowrap text-primary hover:underline"
          >
            {t("common.edit")}
          </button>
          {!r.is_default && (
            <button
              type="button"
              onClick={() => removeEnv(r)}
              className="whitespace-nowrap text-destructive hover:underline"
            >
              {t("common.delete")}
            </button>
          )}
        </div>
      ),
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

      <Drawer
        open={!!envProject}
        onOpenChange={(open) => {
          if (!open) setEnvProject(null);
        }}
        ariaLabel={`${t("qa.env.title")} · ${envProject?.name || ""}`}
        className="w-[min(860px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <div className="min-w-0">
              <h3 className="m-0 truncate text-base font-semibold text-foreground">
                {t("qa.env.title")} · {envProject?.name || ""}
              </h3>
              <p className="m-0 mt-0.5 text-xs text-muted-foreground">
                {t("qa.env.projectHint")}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button size="sm" className="rounded-lg" onClick={() => openEnv()}>
                <Plus className="size-4" />
                {t("qa.env.new")}
              </Button>
              <button
                type="button"
                aria-label={t("common.close")}
                onClick={() => setEnvProject(null)}
                className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <MiniTable<QaEnv>
              data={envs}
              columns={envColumns}
              rowKey="id"
              rowHeight={40}
              emptyText={t("common.noData")}
            />
          </div>
        </div>
      </Drawer>

      <CenterMorphModal
        open={!!envEditing}
        onOpenChange={(next) => {
          if (!next) setEnvEditing(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={envEditing?.id ? t("qa.env.editTitle") : t("qa.env.new")}
          className="max-w-[480px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {envEditing?.id ? t("qa.env.editTitle") : t("qa.env.new")}
            </h2>
            <div className="mt-5 flex flex-col gap-4">
              <Input
                label={t("qa.common.name")}
                value={envForm.values.name}
                onChange={(v) => envForm.set("name", v)}
                disabled={!!envEditing?.is_default}
                maxLength={64}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("qa.env.baseUrl")}
                value={envForm.values.base_url}
                onChange={(v) => envForm.set("base_url", v)}
                placeholder="https://test.example.com"
                classNames={{ field: "rounded-lg" }}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setEnvEditing(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={saveEnv}>
                {t("common.save")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>

      <Drawer
        open={!!varEnv}
        onOpenChange={(next) => {
          if (!next) setVarEnv(null);
        }}
        ariaLabel={`${t("qa.env.vars")} · ${varEnv?.name || ""}`}
        className="w-[min(760px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("qa.env.vars")} · {varEnv?.name || ""}
            </h3>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() =>
                  setVarRows((rs) => [
                    ...rs,
                    {
                      id: "",
                      key: "",
                      value: "",
                      is_secret: false,
                      value_set: false,
                      description: null,
                      uid: nextUid(),
                    },
                  ])
                }
              >
                {t("qa.env.addVar")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={saveVars}>
                {t("common.save")}
              </Button>
              <button
                type="button"
                aria-label={t("common.close")}
                onClick={() => setVarEnv(null)}
                className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="mb-1 grid grid-cols-[170px_1fr_64px_48px] items-center gap-2 text-xs text-muted-foreground">
              <span>{t("qa.env.varKey")}</span>
              <span>{t("qa.env.varValue")}</span>
              <span>{t("qa.env.varSecret")}</span>
              <span />
            </div>
            {varRows.map((r) => (
              <div key={r.uid} className="mb-2 grid grid-cols-[170px_1fr_64px_48px] items-center gap-2">
                <Input
                  value={r.key}
                  onChange={(v) => patchVar(r.uid, { key: v })}
                  disabled={!!r.id}
                  classNames={{ field: "rounded-lg font-mono text-[12px]" }}
                />
                <Input
                  type="password"
                  value={r.id && r.is_secret ? "" : r.value || ""}
                  onChange={(v) => patchVar(r.uid, { value: v })}
                  placeholder={r.id && r.is_secret && r.value_set ? t("qa.env.varSet") : ""}
                  classNames={{ field: "rounded-lg" }}
                />
                <span className="flex justify-center">
                  <Switch
                    checked={r.is_secret}
                    onCheckedChange={(v) => patchVar(r.uid, { is_secret: v })}
                  />
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (r.id) setCleared((cs) => [...cs, r.key]);
                    setVarRows((rs) => rs.filter((x) => x.uid !== r.uid));
                  }}
                  className="text-[12px] text-destructive hover:underline"
                >
                  {t("common.delete")}
                </button>
              </div>
            ))}
            <div className="mt-2 text-xs text-muted-foreground">{t("qa.env.secretHint")}</div>
          </div>
        </div>
      </Drawer>
    </Page>
  );
}