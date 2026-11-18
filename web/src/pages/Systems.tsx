import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import { qa } from "../qa";
import { useGroup } from "../group";
import AmSelect from "../components/AmSelect";
import { useAmEnabled } from "../am";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge from "../ui/Badge";
import SearchSelect from "../ui/SearchSelect";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import { Checkbox } from "../components/motion/checkbox";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Input } from "../components/motion/input";
import EgmReportsDrawer from "../components/EgmReportsDrawer";
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

interface System {
  id: number;
  group_id: number;
  gitlab_project_id: number;
  path: string;
  name: string;
  code_prefix: string;
  default_branch: string;
  mr_target_branch: string;
  tech_lead_person_ids: number[];
  owner_person_ids: number[];
  qa_owner_person_ids: number[];
  audit_prefixes: string[] | null;
  auto_approve_prefixes: string[] | null;
  deploy_env_map: { gitlab: string; qa: string }[] | null;
  deploy_refs: string[] | null;
  am_team_id: string | null;
  am_agent_id: string | null;
}

/** 前缀规则在界面上是一个字符串：多个前缀用分号隔开，* = 匹配全部文件
 *  （库里存空前缀，后端 startswith("") 恒真）。空串不能留：它会被当成匹配全部。 */
const prefixToView = (xs: string[] | null | undefined) =>
  (xs || []).map((x) => (x === "" ? "*" : x)).join(";");
const prefixToDb = (s: string | null | undefined) =>
  (s || "")
    .split(/[;；,，\n]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => (x === "*" ? "" : x));

const linkClass =
  "text-[13px] text-muted-foreground transition-colors hover:text-foreground";
const dangerLinkClass =
  "text-[13px] text-destructive transition-colors hover:text-destructive/80";

const empty = {
  group_id: null as number | null,
  gitlab_project_id: "",
  path: "",
  name: "",
  code_prefix: "",
  default_branch: "master",
  mr_target_branch: "nightly",
  tech_lead_person_ids: [] as number[],
  owner_person_ids: [] as number[],
  qa_owner_person_ids: [] as number[],
  audit_prefixes: "",
  auto_approve_prefixes: "",
  deploy_refs: "",
  deploy_env_map: [] as { gitlab: string; qa: string }[],
  am_team_id: "",
  am_agent_id: "",
  run_egm_bootstrap: false,
};

/** 逗号/分号分隔的分支名列表；空串 = 未配置（回退 mr_target_branch） */
const listToDb = (s: string | null | undefined) =>
  (s || "")
    .split(/[;；,，\n]/)
    .map((x) => x.trim())
    .filter(Boolean);

export default function Systems() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const amOn = useAmEnabled();
  const { groupId, projectId, refreshSystems } = useGroup();
  const [rows, setRows] = useState<System[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<System | null>(null);
  const [keyword, setKeyword] = useState("");
  // 部署触发测试：可选环境（GitLab / QA 各一份，项目维度）
  const [glEnvs, setGlEnvs] = useState<string[]>([]);
  const [qaEnvs, setQaEnvs] = useState<string[]>([]);
  // EGM 项目级任务：bootstrap 生成 README 树，drift 出设计-代码漂移报告
  const [egmFor, setEgmFor] = useState<System | null>(null);
  const form = useForm(empty);

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("project_id", String(projectId));
    setRows(await api<System[]>(`/api/systems${p.toString() ? `?${p}` : ""}`));
    setGroups(await api<any[]>("/api/groups"));
    setPeople(await api<any[]>("/api/people"));
  };
  useEffect(() => {
    load();
  }, [groupId, projectId]);

  // 项目筛选下只允许在该项目的组里建系统
  const formGroups = projectId
    ? groups.filter((g) => g.project_id === projectId)
    : groups;

  const open = (p?: System) => {
    const values = p
      ? {
          group_id: p.group_id,
          gitlab_project_id: String(p.gitlab_project_id ?? ""),
          path: p.path,
          name: p.name,
          code_prefix: p.code_prefix,
          default_branch: p.default_branch,
          mr_target_branch: p.mr_target_branch,
          tech_lead_person_ids: p.tech_lead_person_ids ?? [],
          owner_person_ids: p.owner_person_ids ?? [],
          qa_owner_person_ids: p.qa_owner_person_ids ?? [],
          audit_prefixes: prefixToView(p.audit_prefixes),
          auto_approve_prefixes: prefixToView(p.auto_approve_prefixes),
          deploy_refs: (p.deploy_refs ?? []).join(";"),
          deploy_env_map: (p.deploy_env_map ?? []).map((m) => ({ ...m })),
          am_team_id: p.am_team_id ?? "",
          am_agent_id: p.am_agent_id ?? "",
          run_egm_bootstrap: false,
        }
      : {
          ...empty,
          group_id: groupId ?? formGroups[0]?.id ?? null,
        };
    setEditing(p ?? ({ id: 0 } as System));
    form.reset(values);
    // 部署映射的下拉选项：GitLab 环境（项目维度）+ QA 环境（项目维度）
    if (p?.id) {
      api<string[]>(`/api/systems/${p.id}/gitlab-environments`)
        .then(setGlEnvs)
        .catch(() => setGlEnvs([]));
      qa<{ name: string }[]>(`/api/systems/${p.id}/environments`)
        .then((list) => setQaEnvs(list.map((e) => e.name)))
        .catch(() => setQaEnvs([]));
    } else {
      setGlEnvs([]);
      setQaEnvs([]);
    }
  };

  // 专家列表随团队：项目团队 → 未选回退研发组团队
  const effTeam =
    form.values.am_team_id ||
    groups.find((g) => g.id === editing?.group_id)?.am_team_id ||
    undefined;

  const save = async () => {
    if (
      !form.validate({
        group_id: { required: true, message: t("common.required") },
        gitlab_project_id: { required: true, message: t("common.required") },
        path: { required: true, message: t("common.required") },
        name: { required: true, message: t("common.required") },
        code_prefix: { required: true, message: t("common.required") },
      })
    )
      return;
    const body = {
      ...form.values,
      audit_prefixes: prefixToDb(form.values.audit_prefixes),
      auto_approve_prefixes: prefixToDb(form.values.auto_approve_prefixes),
      deploy_refs: listToDb(form.values.deploy_refs),
      // 半截映射（没选环境）不提交
      deploy_env_map: form.values.deploy_env_map.filter((m) => m.gitlab && m.qa),
    };
    try {
      if (editing?.id)
        await api(`/api/systems/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      else
        await api("/api/systems", {
          method: "POST",
          body: JSON.stringify(body),
        });
      toast.success(
        !editing?.id && form.values.run_egm_bootstrap
          ? t("systems.savedEgm")
          : t("common.saved"),
      );
      setEditing(null);
      load();
      refreshSystems();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  
  const gname = (gid: number) => groups.find((g) => g.id === gid)?.name || gid;
  const pname = (pid: number) => people.find((p) => p.id === pid)?.name || pid;
  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [
          r.path,
          r.name,
          r.code_prefix,
          r.default_branch,
          r.mr_target_branch,
          gname(r.group_id),
        ].some((s) => String(s ?? "").toLowerCase().includes(term)),
      )
    : rows;

  const columns: DataTableColumn<System>[] = [
    {
      title: t("systems.colSystem"),
      dataIndex: "path",
      width: 280,
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    {
      title: t("systems.colGroup"),
      width: 120,
      render: (_, r) => gname(r.group_id),
    },
    {
      title: "GitLab ID",
      dataIndex: "gitlab_project_id",
      width: 100,
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    {
      title: t("systems.colPrefix"),
      dataIndex: "code_prefix",
      width: 100,
      render: (v) => <Badge tone="info">{v}</Badge>,
    },
    {
      title: t("systems.colDefaultBranch"),
      dataIndex: "default_branch",
      width: 110,
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    {
      title: t("systems.colMrTarget"),
      dataIndex: "mr_target_branch",
      width: 110,
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    {
      title: t("systems.colTechLead"),
      width: 110,
      render: (_, r) =>
        r.tech_lead_person_ids.length
          ? r.tech_lead_person_ids.map(pname).join(t("systems.listSep"))
          : "—",
    },
    {
      title: t("systems.colOwner"),
      width: 110,
      render: (_, r) =>
        r.owner_person_ids.length
          ? r.owner_person_ids.map(pname).join(t("systems.listSep"))
          : "—",
    },
    {
      title: t("systems.colQaOwner"),
      width: 110,
      render: (_, r) =>
        r.qa_owner_person_ids.length
          ? r.qa_owner_person_ids.map(pname).join(t("systems.listSep"))
          : "—",
    },
    {
      title: t("systems.colPrefixes"),
      width: 150,
      render: (_, r) => (
        <span className="flex flex-wrap gap-1">
          {r.audit_prefixes?.length ? (
            <Badge tone="info">
              {r.audit_prefixes.includes("")
                ? t("systems.auditAll")
                : t("systems.auditCount", { n: r.audit_prefixes.length })}
            </Badge>
          ) : null}
          {r.auto_approve_prefixes?.length ? (
            <Badge tone="success">
              {r.auto_approve_prefixes.includes("")
                ? t("systems.releaseAll")
                : t("systems.releaseCount", {
                    n: r.auto_approve_prefixes.length,
                  })}
            </Badge>
          ) : null}
          {!r.audit_prefixes?.length && !r.auto_approve_prefixes?.length
            ? "—"
            : null}
        </span>
      ),
    },
    {
      title: t("common.actions"),
      width: 260,
      render: (_, r) => (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button
            type="button"
            className={linkClass}
            onClick={() => nav(`/systems/${r.id}/qa`)}
          >
            {t("qa.entry")}
          </button>
          <button type="button" className={linkClass} onClick={() => open(r)}>
            {t("common.edit")}
          </button>
          <button type="button" className={linkClass} onClick={() => setEgmFor(r)}>
            {t("systems.egmReports")}
          </button>
          {isAdmin() ? (
            <button
              type="button"
              className={dangerLinkClass}
              onClick={async () => {
                const ok = await confirm({
                  title: t("systems.deleteConfirm"),
                  confirmText: t("common.delete"),
                  danger: true,
                });
                if (!ok) return;
                try {
                  await api(`/api/systems/${r.id}`, { method: "DELETE" });
                  load();
                  refreshSystems();
                } catch (e: any) {
                  toast.error(e.message);
                }
              }}
            >
              {t("common.delete")}
            </button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <Page
      title={t("nav.systems")}
      desc={t("systems.desc")}
      actions={
        isAdmin() ? (
          <Button size="sm" className="rounded-lg" onClick={() => open()}>
            <Plus className="size-4" />
            {t("systems.new")}
          </Button>
        ) : null
      }
    >
      <Input
        value={keyword}
        onChange={setKeyword}
        placeholder={t("systems.searchPlaceholder")}
        leftIcon={<Search />}
        classNames={{ field: "rounded-lg" }}
        className="mb-3 max-w-sm"
      />

      <DataTable<System>
        rowKey="id"
        dataSource={shown}
        columns={columns}
        emptyText={t("common.noData")}
      />

      <CenterMorphModal
        open={!!editing}
        onOpenChange={(next) => {
          if (!next) setEditing(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={
            editing?.id ? t("systems.editTitle") : t("systems.createTitle")
          }
          className="max-w-[760px]"
        >
          <div className="flex max-h-[85vh] flex-col">
            <h2 className="m-0 px-6 pb-1 pt-5 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {editing?.id ? t("systems.editTitle") : t("systems.createTitle")}
            </h2>
            <div className="grid gap-4 overflow-y-auto px-6 py-4 sm:grid-cols-2">
              <Field label={t("systems.group")} error={form.errors.group_id}>
                <SearchSelect
                  value={form.values.group_id}
                  onChange={(v) =>
                    form.set("group_id", v == null ? null : Number(v))
                  }
                  disabled={!!editing?.id}
                  placeholder={t("systems.group")}
                  options={formGroups.map((g) => ({
                    value: g.id,
                    label: g.name,
                  }))}
                />
              </Field>
              <Input
                label={t("systems.gitlabId")}
                value={form.values.gitlab_project_id}
                onChange={(v) => form.set("gitlab_project_id", v)}
                disabled={!!editing?.id}
                error={form.errors.gitlab_project_id}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("systems.repoPath")}
                value={form.values.path}
                onChange={(v) => form.set("path", v)}
                error={form.errors.path}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("systems.systemName")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("systems.codePrefix")}
                value={form.values.code_prefix}
                onChange={(v) => form.set("code_prefix", v)}
                maxLength={12}
                placeholder={t("systems.codePrefixPlaceholder")}
                error={form.errors.code_prefix}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("systems.defaultBranch")}
                value={form.values.default_branch}
                onChange={(v) => form.set("default_branch", v)}
                classNames={{ field: "rounded-lg" }}
              />
              <Field
                label={t("systems.mrTarget")}
                tip={t("systems.mrTargetTooltip")}
              >
                <Input
                  value={form.values.mr_target_branch}
                  onChange={(v) => form.set("mr_target_branch", v)}
                  classNames={{ field: "rounded-lg" }}
                />
              </Field>

              <div className="sm:col-span-2">
                <div className="text-[13px] font-medium text-foreground">
                  {t("systems.deployTitle")}
                </div>
                <p className="m-0 mt-1 text-[12px] text-muted-foreground">
                  {t("systems.deployHint")}
                </p>
                <div className="mt-2">
                  <Input
                    label={t("systems.deployRefs")}
                    value={form.values.deploy_refs}
                    onChange={(v) => form.set("deploy_refs", v)}
                    placeholder={form.values.mr_target_branch || "nightly"}
                    classNames={{ field: "rounded-lg" }}
                  />
                </div>
                {editing?.id ? (
                  <div className="mt-3 flex flex-col gap-2">
                    {form.values.deploy_env_map.map((m, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <div className="flex-1">
                          <SearchSelect
                            value={m.gitlab || null}
                            onChange={(v) => {
                              const next = [...form.values.deploy_env_map];
                              next[i] = { ...next[i], gitlab: String(v ?? "") };
                              form.set("deploy_env_map", next);
                            }}
                            options={glEnvs.map((n) => ({ value: n, label: n }))}
                            placeholder={t("systems.deployGitlabEnv")}
                          />
                        </div>
                        <span className="text-muted-foreground">→</span>
                        <div className="flex-1">
                          <SearchSelect
                            value={m.qa || null}
                            onChange={(v) => {
                              const next = [...form.values.deploy_env_map];
                              next[i] = { ...next[i], qa: String(v ?? "") };
                              form.set("deploy_env_map", next);
                            }}
                            options={qaEnvs.map((n) => ({ value: n, label: n }))}
                            placeholder={t("systems.deployQaEnv")}
                          />
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            form.set(
                              "deploy_env_map",
                              form.values.deploy_env_map.filter((_, j) => j !== i),
                            )
                          }
                        >
                          {t("common.delete")}
                        </Button>
                      </div>
                    ))}
                    <div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          form.set("deploy_env_map", [
                            ...form.values.deploy_env_map,
                            { gitlab: "", qa: "" },
                          ])
                        }
                      >
                        <Plus className="mr-1 size-3.5" />
                        {t("systems.deployAdd")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="mt-2 text-[12px] text-muted-foreground">
                    {t("systems.deploySaveFirst")}
                  </p>
                )}
              </div>
              <Field label={t("systems.techLead")}>
                <MultiSelect
                  value={form.values.tech_lead_person_ids.map(String)}
                  onValueChange={(values) =>
                    form.set("tech_lead_person_ids", values.map(Number))
                  }
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue placeholder={t("common.search")} />
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
              <Field label={t("systems.owners")}>
                <MultiSelect
                  value={form.values.owner_person_ids.map(String)}
                  onValueChange={(values) =>
                    form.set("owner_person_ids", values.map(Number))
                  }
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue placeholder={t("common.search")} />
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
              <Field
                label={t("systems.qaOwner")}
                tip={t("systems.qaOwnerTooltip")}
              >
                <MultiSelect
                  value={form.values.qa_owner_person_ids.map(String)}
                  onValueChange={(values) =>
                    form.set("qa_owner_person_ids", values.map(Number))
                  }
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue placeholder={t("common.search")} />
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
              <Field
                label={t("systems.auditPrefixes")}
                tip={t("systems.auditPrefixesTooltip")}
              >
                <Input
                  value={form.values.audit_prefixes}
                  onChange={(v) => form.set("audit_prefixes", v)}
                  placeholder={t("systems.auditPlaceholder")}
                  classNames={{ field: "rounded-lg" }}
                />
              </Field>
              <Field
                label={t("systems.autoApprove")}
                tip={t("systems.autoApproveTooltip")}
              >
                <Input
                  value={form.values.auto_approve_prefixes}
                  onChange={(v) => form.set("auto_approve_prefixes", v)}
                  placeholder="docs/; src/"
                  classNames={{ field: "rounded-lg" }}
                />
              </Field>
              {amOn && (
                <Field
                  label={t("systems.amTeam")}
                  tip={t("systems.amTeamTooltip")}
                >
                  <AmSelect
                    kind="team"
                    value={form.values.am_team_id}
                    onChange={(v) =>
                      form.reset({
                        ...form.values,
                        am_team_id: v ?? "",
                        am_agent_id: "",
                      })
                    }
                    placeholder={t("systems.amTeamPlaceholder")}
                  />
                </Field>
              )}
              {amOn && (
                <Field
                  label={t("systems.amAgent")}
                  tip={t("systems.amAgentTooltip")}
                >
                  <AmSelect
                    kind="agent"
                    teamId={effTeam}
                    value={form.values.am_agent_id}
                    onChange={(v) => form.set("am_agent_id", v ?? "")}
                    placeholder={t("systems.amAgentPlaceholder")}
                  />
                </Field>
              )}
              {!editing?.id ? (
                <Field
                  label={t("systems.runBootstrap")}
                  tip={t("systems.runBootstrapTooltip")}
                >
                  <Checkbox
                    checked={!!form.values.run_egm_bootstrap}
                    onCheckedChange={(v) => form.set("run_egm_bootstrap", v)}
                    label={t("systems.bootstrapNow")}
                  />
                </Field>
              ) : null}
            </div>
            <div className="flex justify-end gap-2 border-t border-border px-6 py-3">
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

      <EgmReportsDrawer system={egmFor} onClose={() => setEgmFor(null)} />
    </Page>
  );
}