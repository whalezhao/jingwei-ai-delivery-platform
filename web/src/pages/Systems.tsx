import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import { api, isAdmin } from "../api";
import type { MsgKey } from "../i18n";
import { useGroup } from "../group";
import AmSelect from "../components/AmSelect";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
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
import { Drawer } from "../components/motion/drawer";
import { Input } from "../components/motion/input";
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

const EGM_ST: Record<string, { tone: BadgeTone; text: MsgKey }> = {
  running: { tone: "info", text: "systems.egm.running" },
  done: { tone: "success", text: "systems.egm.done" },
  failed: { tone: "danger", text: "systems.egm.failed" },
};

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
  audit_prefixes: string[] | null;
  auto_approve_prefixes: string[] | null;
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
  audit_prefixes: "",
  auto_approve_prefixes: "",
  am_team_id: "",
  am_agent_id: "",
  run_egm_bootstrap: false,
};

export default function Systems() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { groupId, projectId, refreshSystems } = useGroup();
  const [rows, setRows] = useState<System[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<System | null>(null);
  const [keyword, setKeyword] = useState("");
  // EGM 项目级任务：bootstrap 生成 README 树，drift 出设计-代码漂移报告
  const [egmFor, setEgmFor] = useState<System | null>(null);
  const [egmReports, setEgmReports] = useState<any[]>([]);
  const [egmBusy, setEgmBusy] = useState(false);
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
          audit_prefixes: prefixToView(p.audit_prefixes),
          auto_approve_prefixes: prefixToView(p.auto_approve_prefixes),
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

  const openEgm = async (p: System) => {
    setEgmFor(p);
    setEgmReports(
      await api<any[]>(`/api/systems/${p.id}/egm-reports`).catch(() => []),
    );
  };

  const runEgm = async (p: System, kind: "bootstrap" | "drift") => {
    setEgmBusy(true);
    try {
      await api(`/api/systems/${p.id}/egm-jobs`, {
        method: "POST",
        body: JSON.stringify({ kind }),
      });
      toast.success(
        t(kind === "bootstrap" ? "systems.submittedBootstrap" : "systems.submittedDrift"),
      );
      await openEgm(p);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setEgmBusy(false);
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
      width: 320,
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
          <button type="button" className={linkClass} onClick={() => openEgm(r)}>
            {t("systems.egmReports")}
          </button>
          <button
            type="button"
            className={linkClass}
            onClick={() => !egmBusy && runEgm(r, "bootstrap")}
          >
            {t("systems.docTree")}
          </button>
          <button
            type="button"
            className={linkClass}
            onClick={() => !egmBusy && runEgm(r, "drift")}
          >
            {t("systems.driftCheck")}
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
              <Field label={t("systems.amTeam")} tip={t("systems.amTeamTooltip")}>
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
              <Field label={t("systems.amAgent")} tip={t("systems.amAgentTooltip")}>
                <AmSelect
                  kind="agent"
                  teamId={effTeam}
                  value={form.values.am_agent_id}
                  onChange={(v) => form.set("am_agent_id", v ?? "")}
                  placeholder={t("systems.amAgentPlaceholder")}
                />
              </Field>
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

      <Drawer
        open={!!egmFor}
        onOpenChange={(next) => {
          if (!next) setEgmFor(null);
        }}
        ariaLabel={t("systems.egmTitle")}
        className="w-[min(760px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("systems.egmTitle")}
              {egmFor ? ` · ${egmFor.path}` : ""}
            </h3>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => egmFor && openEgm(egmFor)}
              >
                {t("systems.refresh")}
              </Button>
              <button
                type="button"
                aria-label={t("common.close")}
                onClick={() => setEgmFor(null)}
                className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {egmReports.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t("systems.noEgm")}
              </p>
            ) : (
              egmReports.map((r) => {
                const st = EGM_ST[r.status];
                return (
                  <div
                    key={r.id}
                    className="mb-5 border-b border-border pb-5 last:border-0 last:pb-0"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <Badge tone={r.kind === "bootstrap" ? "info" : "violet"}>
                        {r.kind === "bootstrap"
                          ? t("systems.docTree")
                          : t("systems.driftCheck")}
                      </Badge>
                      <Badge tone={st?.tone ?? "neutral"}>
                        {st ? t(st.text) : r.status}
                      </Badge>
                      <span>{r.created_at}</span>
                      {r.branch ? (
                        <span className="font-mono">{r.branch}</span>
                      ) : null}
                      {r.mr_url ? (
                        <a
                          href={r.mr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary hover:underline"
                        >
                          MR
                        </a>
                      ) : null}
                    </div>
                    {r.report_md ? (
                      <div className="md-body mt-2">
                        <Markdown>{r.report_md}</Markdown>
                      </div>
                    ) : (
                      <p className="mt-2 text-xs text-muted-foreground">
                        {r.status === "running"
                          ? t("systems.inProgress")
                          : t("systems.noContent")}
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </Drawer>
    </Page>
  );
}