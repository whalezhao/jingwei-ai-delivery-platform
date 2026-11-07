import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search, TriangleAlert, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import type { MsgKey } from "../i18n";
import AmSelect from "../components/AmSelect";
import Page from "../Page";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import EffortSelect from "../ui/EffortSelect";
import SearchSelect from "../ui/SearchSelect";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Alert, AlertDescription, AlertIcon } from "../components/motion/alert";
import { Button } from "../components/motion/button/index";
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
import { Switch } from "../components/motion/switch";

interface Group {
  id: number;
  name: string;
  project_id: number | null;
  project_name: string | null;
  gitlab_bot_username: string | null;
  gitlab_base_url: string | null;
  octo_group_no: string | null;
  am_team_id: string | null;
  scan_enabled: boolean;
  auto_webhook: boolean;
  llm_base_url: string | null;
  llm_model: string | null;
  llm_effort: string | null;
  has_gitlab_token: boolean;
  has_llm_key: boolean;
  has_octo_bot_token: boolean;
  tech_lead_person_ids: number[];
}

interface Member {
  id: number;
  person_id: number;
  person_name: string;
  gitlab_username: string | null;
  gitlab_email: string | null;
  gitlab_account_id: number | null;
  gitlab_account_username: string | null;
  gitlab_accounts: {
    id: number;
    username: string;
    base_url: string;
    has_token: boolean;
  }[];
  octo_uid: string | null;
  octo_nickname: string | null;
  match_status: string;
  match_confidence: number | null;
  match_reason: string | null;
  manual_lock: boolean;
}

// 绑定来源只区分两种：系统对配出来的，和人工调整的（pending 只是 AI 建议、尚未绑定）
const STATUS_TAG: Record<string, [BadgeTone, MsgKey]> = {
  auto_exact: ["success", "groups.status.matched"],
  auto_ai: ["success", "groups.status.matched"],
  auto: ["success", "groups.status.matched"],
  manual: ["violet", "groups.status.manual"],
  pending: ["neutral", "groups.status.unmatched"],
  unmatched: ["neutral", "groups.status.unmatched"],
};

const empty = {
  name: "",
  project_id: null as number | null,
  gitlab_bot_username: "",
  gitlab_base_url: "",
  gitlab_token: "",
  llm_base_url: "",
  llm_api_key: "",
  llm_model: "",
  llm_effort: "",
  octo_group_no: "",
  octo_bot_token: "",
  am_team_id: "",
  tech_lead_person_ids: [] as number[],
  scan_enabled: true,
  auto_webhook: true,
};

const linkClass =
  "text-[13px] text-muted-foreground transition-colors hover:text-foreground";
const dangerLinkClass =
  "text-[13px] text-destructive transition-colors hover:text-destructive/80";

export default function Groups() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [rows, setRows] = useState<Group[]>([]);
  const [keyword, setKeyword] = useState("");
  const [mkeyword, setMkeyword] = useState("");
  const [editing, setEditing] = useState<Group | null>(null);
  const form = useForm(empty);
  // 成员抽屉
  const [mgroup, setMgroup] = useState<Group | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [octoList, setOctoList] = useState<{ uid: string; nickname: string }[]>(
    [],
  );
  const [octoErr, setOctoErr] = useState<string | null>(null);
  const [people, setPeople] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [addPid, setAddPid] = useState<number | null>(null);
  // 群号下拉：本组 bot 所在的群
  const [octoGroups, setOctoGroups] = useState<
    { group_no: string; name: string }[]
  >([]);
  const [octoGroupsErr, setOctoGroupsErr] = useState<string | null>(null);
  const [glBusy, setGlBusy] = useState(false);

  // 表格里显示中文名而不是 id：octo 群名按组用各组 bot 拉，am 团队名取当前用户的团队列表
  const [octoNames, setOctoNames] = useState<Record<string, string>>({});
  const [teamNames, setTeamNames] = useState<Record<string, string>>({});

  const loadNames = async (list: Group[]) => {
    api<any[]>("/api/am/teams")
      .then((teams) =>
        setTeamNames(Object.fromEntries(teams.map((tm) => [tm.id, tm.name]))),
      )
      .catch(() => {});
    for (const g of list) {
      if (!g.octo_group_no) continue;
      try {
        const d = await api<{ items: { group_no: string; name: string }[] }>(
          `/api/groups/${g.id}/octo-groups`,
        );
        setOctoNames((prev) => ({
          ...prev,
          ...Object.fromEntries((d.items || []).map((x) => [x.group_no, x.name])),
        }));
      } catch {
        // 拉不到就退回显示群号
      }
    }
  };

  const load = () =>
    api<Group[]>("/api/groups").then((list) => {
      setRows(list);
      loadNames(list);
    });
  useEffect(() => {
    load();
    api<any[]>("/api/people").then(setPeople).catch(() => {});
    api<any[]>("/api/projects").then(setProjects).catch(() => {});
  }, []);

  const open = (g?: Group) => {
    setEditing(g ?? ({ id: 0 } as Group));
    form.reset(
      g
        ? {
            name: g.name,
            project_id: g.project_id ?? null,
            gitlab_bot_username: g.gitlab_bot_username ?? "",
            gitlab_base_url: g.gitlab_base_url ?? "",
            gitlab_token: "",
            llm_base_url: g.llm_base_url ?? "",
            llm_api_key: "",
            llm_model: g.llm_model ?? "",
            llm_effort: g.llm_effort ?? "",
            octo_group_no: g.octo_group_no ?? "",
            octo_bot_token: "",
            am_team_id: g.am_team_id ?? "",
            tech_lead_person_ids: g.tech_lead_person_ids ?? [],
            scan_enabled: g.scan_enabled,
            auto_webhook: g.auto_webhook,
          }
        : { ...empty },
    );
    setOctoGroups([]);
    setOctoGroupsErr(null);
  };

  const save = async () => {
    if (
      !form.validate({
        name: { required: true, message: t("common.required") },
      })
    )
      return;
    const body: any = { ...form.values };
    for (const k of ["gitlab_token", "llm_api_key", "octo_bot_token"])
      if (!body[k]) delete body[k];
    try {
      if (editing?.id)
        await api(`/api/groups/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      else
        await api("/api/groups", {
          method: "POST",
          body: JSON.stringify(body),
        });
      toast.success(t("groups.saved"));
      setEditing(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const testBot = async (g: Group) => {
    try {
      const d = await api<any>(`/api/groups/${g.id}/octo-test`, { method: "POST" });
      if (d.ok)
        toast.success(
          t("groups.testOk", {
            via: d.dedicated ? t("groups.viaOwn") : t("groups.viaGlobal"),
          }),
        );
      else toast.error(t("groups.testFail"));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const runScan = async (g: Group) => {
    try {
      const d = await api<any>(`/api/groups/${g.id}/scan`, { method: "POST" });
      toast.success(
        `${t("groups.scanDone", {
          projects: d.projects_added,
          webhooks: d.webhooks_added,
        })}${d.error ? `（${d.error}）` : ""}`,
      );
      nav("/projects");
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const loadOctoGroups = async () => {
    try {
      // 新建组还没有 id：直接用全局 bot 的群列表，不必先保存
      const d = await api<{
        items: { group_no: string; name: string }[];
        error: string | null;
      }>(
        editing?.id
          ? `/api/groups/${editing.id}/octo-groups`
          : "/api/integrations/octo-groups",
      );
      setOctoGroups(d.items || []);
      setOctoGroupsErr(d.error || null);
    } catch (e: any) {
      setOctoGroups([]);
      setOctoGroupsErr(e.message);
    }
  };

  // 重拉本组全部项目成员并覆盖快照；账号在「人员」页维护，这里只刷新名单
  const refreshGlUsers = async () => {
    if (!mgroup) return;
    setGlBusy(true);
    try {
      const d = await api<{ items: unknown[] }>(
        `/api/groups/${mgroup.id}/gitlab-users/refresh`,
        { method: "POST" },
      );
      toast.success(t("groups.refreshDone", { n: d.items?.length ?? 0 }));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setGlBusy(false);
    }
  };

  const bindAccount = async (m: Member, accountId: number | null) => {
    if (!mgroup) return;
    try {
      await api(`/api/groups/${mgroup.id}/members/${m.person_id}/account`, {
        method: "PUT",
        body: JSON.stringify({ gitlab_account_id: accountId }),
      });
      toast.success(
        accountId ? t("groups.boundGitlab") : t("groups.unboundGitlab"),
      );
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const openMembers = async (g: Group) => {
    setMgroup(g);
    const [ms, oc, ps] = await Promise.all([
      api<Member[]>(`/api/groups/${g.id}/members`),
      api<{
        items: { uid: string; nickname: string }[];
        error: string | null;
      }>(`/api/groups/${g.id}/octo-members`),
      api<any[]>("/api/people"),
    ]);
    setMembers(ms);
    setOctoList(oc.items || []);
    setOctoErr(oc.error || null);
    setPeople(ps);
  };

  const runMatch = async () => {
    if (!mgroup) return;
    try {
      const d = await api<any>(`/api/groups/${mgroup.id}/match/run`, {
        method: "POST",
      });
      const s = d.stats || {};
      toast.success(
        `${t("groups.matchDone", {
          gitlabCreated: s.gitlab_created ?? 0,
          gitlabMerged: s.gitlab_merged ?? 0,
          octoCreated: s.octo_created ?? 0,
          autoExact: s.auto_exact ?? 0,
          autoAi: s.auto_ai ?? 0,
          pending: s.pending ?? 0,
          keptManual: s.kept_manual ?? 0,
        })}${s.error ? `（${s.error}）` : ""}`,
      );
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const manualBind = async (m: Member, uid: string | null) => {
    if (!mgroup) return;
    try {
      const d = await api<any>(`/api/groups/${mgroup.id}/match/${m.person_id}`, {
        method: "PUT",
        body: JSON.stringify({ octo_uid: uid }),
      });
      toast.success(
        uid
          ? d.released
            ? t("groups.boundOctoReleased", { released: d.released })
            : t("groups.boundOcto")
          : t("groups.unboundOcto"),
      );
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const gterm = keyword.trim().toLowerCase();
  const grpShown = gterm
    ? rows.filter((r) =>
        [r.name, r.gitlab_base_url].some((s) =>
          (s || "").toLowerCase().includes(gterm),
        ),
      )
    : rows;
  const mterm = mkeyword.trim().toLowerCase();
  // 排序前先复制：直接 sort(members) 会就地改 state 数组
  const memShown = [
    ...(mterm
      ? members.filter((m) =>
          [
            m.person_name,
            m.gitlab_account_username,
            m.gitlab_username,
            m.gitlab_email,
            m.octo_nickname,
            m.octo_uid,
          ].some((s) => (s || "").toLowerCase().includes(mterm)),
        )
      : members),
  ].sort((a, b) => Number(!!b.octo_uid) - Number(!!a.octo_uid));

  const columns: DataTableColumn<Group>[] = [
    {
      title: t("groups.colGroup"),
      dataIndex: "name",
      width: 130,
      render: (v) => <span className="font-medium text-foreground">{v}</span>,
    },
    {
      title: t("groups.colProject"),
      dataIndex: "project_name",
      width: 130,
      render: (v) => (v ? v : <Badge>{t("groups.noProject")}</Badge>),
    },
    {
      title: t("groups.colGitlab"),
      dataIndex: "gitlab_base_url",
      width: 190,
      render: (v, r) => (
        <span className="flex items-center gap-1.5">
          <span className="truncate font-mono text-[11px]">
            {v}
          </span>
          {r.has_gitlab_token ? (
            <Badge tone="success">token ✓</Badge>
          ) : (
            <Badge tone="danger">{t("groups.noToken")}</Badge>
          )}
        </span>
      ),
    },
    {
      title: t("groups.colOcto"),
      dataIndex: "octo_group_no",
      width: 170,
      render: (v, r) =>
        v ? (
          <span className="flex items-center gap-1.5">
            <span className="truncate">
              {octoNames[v] || <span className="font-mono text-[11px]">{v}</span>}
            </span>
            {r.has_octo_bot_token ? (
              <Badge tone="success">{t("integrations.ownBot")}</Badge>
            ) : (
              <Badge>{t("integrations.globalBot")}</Badge>
            )}
          </span>
        ) : (
          "—"
        ),
    },
    {
      title: t("groups.colAmTeam"),
      dataIndex: "am_team_id",
      width: 130,
      render: (v) =>
        v ? (
          <span className="truncate">
            {teamNames[v] || <span className="font-mono text-[11px]">{v}</span>}
          </span>
        ) : (
          "—"
        ),
    },
    {
      title: t("groups.colLlm"),
      width: 100,
      render: (_, r) =>
        r.has_llm_key ? r.llm_model || t("groups.defaultLlm") : "—",
    },
    {
      title: t("groups.colScan"),
      dataIndex: "scan_enabled",
      width: 80,
      render: (v) =>
        v ? (
          <Badge tone="success">{t("groups.on")}</Badge>
        ) : (
          <Badge>{t("groups.off")}</Badge>
        ),
    },
    {
      title: t("common.actions"),
      width: 320,
      render: (_, r) => (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button type="button" className={linkClass} onClick={() => openMembers(r)}>
            {t("groups.members")}
          </button>
          <button type="button" className={linkClass} onClick={() => open(r)}>
            {t("common.edit")}
          </button>
          <button type="button" className={linkClass} onClick={() => runScan(r)}>
            {t("groups.scanProjects")}
          </button>
          <button type="button" className={linkClass} onClick={() => testBot(r)}>
            {t("groups.testBot")}
          </button>
          {isAdmin() ? (
            <button
              type="button"
              className={dangerLinkClass}
              onClick={async () => {
                const ok = await confirm({
                  title: t("groups.deleteConfirm"),
                  confirmText: t("common.delete"),
                  danger: true,
                });
                if (!ok) return;
                try {
                  await api(`/api/groups/${r.id}`, { method: "DELETE" });
                  load();
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

  const memberColumns: DataTableColumn<Member>[] = [
    {
      title: t("groups.colMember"),
      dataIndex: "person_name",
      width: 150,
      render: (v) => (
        <span className="truncate">
          {v}
        </span>
      ),
    },
    {
      title: t("groups.colGitlabAccount"),
      width: 210,
      render: (_, m) => (
        <SearchSelect
          value={m.gitlab_account_id}
          onChange={(id) => bindAccount(m, id == null ? null : Number(id))}
          clearable
          disabled={!isAdmin() || !(m.gitlab_accounts || []).length}
          placeholder={t("groups.selectBind")}
          options={(m.gitlab_accounts || []).map((a) => ({
            value: a.id,
            label: a.has_token
              ? a.username
              : `${a.username}${t("groups.noToken")}`,
          }))}
          className="h-8 text-xs"
        />
      ),
    },
    {
      title: t("groups.colOctoBind"),
      width: 240,
      render: (_, m) => (
        <SearchSelect
          value={m.match_status === "pending" ? null : m.octo_uid}
          onChange={(uid) =>
            manualBind(m, uid == null ? null : String(uid))
          }
          clearable
          disabled={!isAdmin()}
          placeholder={t("groups.selectOcto")}
          options={octoList.map((o) => ({
            value: o.uid,
            label: `${o.nickname}（${o.uid}）`,
          }))}
          className="h-8 text-xs"
        />
      ),
    },
    {
      title: t("groups.colMatchStatus"),
      width: 110,
      render: (_, m) => {
        const pair = STATUS_TAG[m.match_status];
        return (
          <Badge tone={pair?.[0] ?? "neutral"}>
            {pair ? t(pair[1]) : m.match_status}
          </Badge>
        );
      },
    },
    {
      title: t("groups.colReason"),
      dataIndex: "match_reason",
      width: 160,
      render: (v) => (
        <span className="truncate">
          {v || "—"}
        </span>
      ),
    },
    {
      title: t("common.actions"),
      width: 200,
      render: (_, m) => (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {m.gitlab_account_id ? (
            <button
              type="button"
              className={linkClass}
              onClick={() => bindAccount(m, null)}
            >
              {t("groups.unbindGitlab")}
            </button>
          ) : null}
          {m.octo_uid ? (
            <button
              type="button"
              className={linkClass}
              onClick={() => manualBind(m, null)}
            >
              {t("groups.unbindOcto")}
            </button>
          ) : null}
          <button
            type="button"
            className={dangerLinkClass}
            onClick={async () => {
              await api(`/api/groups/${mgroup!.id}/members/${m.person_id}`, {
                method: "DELETE",
              });
              setMembers(
                await api<Member[]>(`/api/groups/${mgroup!.id}/members`),
              );
            }}
          >
            {t("groups.remove")}
          </button>
        </div>
      ),
    },
  ];

  return (
    <Page
      title={t("nav.groups")}
      desc={t("groups.desc")}
      actions={
        isAdmin() ? (
          <Button size="sm" className="rounded-lg" onClick={() => open()}>
            <Plus className="size-4" />
            {t("groups.new")}
          </Button>
        ) : null
      }
    >
      <Input
        value={keyword}
        onChange={setKeyword}
        placeholder={t("groups.searchPlaceholder")}
        leftIcon={<Search />}
        classNames={{ field: "rounded-lg" }}
        className="mb-3 max-w-sm"
      />

      <DataTable<Group>
        rowKey="id"
        dataSource={grpShown}
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
            editing?.id ? t("groups.editTitle") : t("groups.createTitle")
          }
          className="max-w-[720px]"
        >
          <div className="flex max-h-[84vh] flex-col">
            <h2 className="m-0 px-6 pb-1 pt-5 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {editing?.id ? t("groups.editTitle") : t("groups.createTitle")}
            </h2>
            <div className="flex flex-col gap-4 overflow-y-auto px-6 py-4">
              <Input
                label={t("groups.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("groups.project")} tip={t("groups.projectTooltip")}>
                <SearchSelect
                  value={form.values.project_id}
                  onChange={(v) => form.set("project_id", v as number | null)}
                  clearable
                  placeholder={t("groups.projectPlaceholder")}
                  options={projects.map((p) => ({ value: p.id, label: p.name }))}
                />
              </Field>
              <Input
                label={t("groups.gitlabUrl")}
                value={form.values.gitlab_base_url}
                onChange={(v) => form.set("gitlab_base_url", v)}
                placeholder="https://git.example.com"
                classNames={{ field: "rounded-lg" }}
              />
              <Field
                label={t("groups.gitlabBot")}
                tip={t("groups.gitlabBotTooltip")}
              >
                <Input
                  value={form.values.gitlab_bot_username}
                  onChange={(v) => form.set("gitlab_bot_username", v)}
                  placeholder={t("groups.gitlabBotPlaceholder")}
                  classNames={{ field: "rounded-lg" }}
                />
              </Field>
              <Input
                type="password"
                label={t("groups.gitlabToken")}
                value={form.values.gitlab_token}
                onChange={(v) => form.set("gitlab_token", v)}
                placeholder={
                  editing?.has_gitlab_token ? t("groups.configuredKeep") : ""
                }
                classNames={{ field: "rounded-lg" }}
              />
              <Field
                label={t("groups.octoGroup")}
                tip={t("groups.octoGroupTooltip")}
                hint={octoGroupsErr || undefined}
              >
                <SearchSelect
                  value={form.values.octo_group_no || null}
                  onChange={(v) =>
                    form.set("octo_group_no", v == null ? "" : String(v))
                  }
                  clearable
                  onOpen={loadOctoGroups}
                  placeholder={t("groups.octoGroupPlaceholder")}
                  options={octoGroups.map((g) => ({
                    value: g.group_no,
                    label: t("groups.octoGroupLabel", {
                      name: g.name,
                      id: g.group_no.slice(0, 8),
                    }),
                  }))}
                />
              </Field>
              <Input
                type="password"
                label={t("groups.octoBotToken")}
                value={form.values.octo_bot_token}
                onChange={(v) => form.set("octo_bot_token", v)}
                placeholder={
                  editing?.has_octo_bot_token
                    ? t("groups.configuredKeep")
                    : t("groups.octoBotTokenPlaceholder")
                }
                classNames={{ field: "rounded-lg" }}
              />
              <Field
                label={t("groups.techLead")}
                tip={t("groups.techLeadTooltip")}
              >
                <MultiSelect
                  value={form.values.tech_lead_person_ids.map(String)}
                  onValueChange={(values) =>
                    form.set("tech_lead_person_ids", values.map(Number))
                  }
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue placeholder={t("groups.multiPlaceholder")} />
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
              <Field label={t("groups.amTeam")}>
                <AmSelect
                  kind="team"
                  value={form.values.am_team_id}
                  onChange={(v) => form.set("am_team_id", v ?? "")}
                  placeholder={t("groups.amTeamPlaceholder")}
                />
              </Field>
              <Input
                label={t("groups.llmBaseUrl")}
                value={form.values.llm_base_url}
                onChange={(v) => form.set("llm_base_url", v)}
                placeholder="https://…/v1"
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                type="password"
                label={t("groups.llmApiKey")}
                value={form.values.llm_api_key}
                onChange={(v) => form.set("llm_api_key", v)}
                placeholder={
                  editing?.has_llm_key ? t("groups.configuredKeep") : ""
                }
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("groups.llmModel")}
                value={form.values.llm_model}
                onChange={(v) => form.set("llm_model", v)}
                classNames={{ field: "rounded-lg" }}
              />
              <Field
                label={t("groups.llmEffort")}
                tip={t("groups.llmEffortTooltip")}
              >
                <EffortSelect
                  value={form.values.llm_effort}
                  onChange={(v) => form.set("llm_effort", v)}
                />
              </Field>
              <Field label={t("groups.scanEnabled")}>
                <Switch
                  checked={!!form.values.scan_enabled}
                  onCheckedChange={(v) => form.set("scan_enabled", v)}
                />
              </Field>
              <Field label={t("groups.autoWebhook")}>
                <Switch
                  checked={!!form.values.auto_webhook}
                  onCheckedChange={(v) => form.set("auto_webhook", v)}
                />
              </Field>
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
        open={!!mgroup}
        onOpenChange={(next) => {
          if (!next) {
            setMgroup(null);
            setMkeyword(""); // 换了组，上一组的检索词不该带过去
          }
        }}
        ariaLabel={t("groups.membersDrawer", { name: mgroup?.name || "" })}
        className="w-[min(1100px,95vw)] max-w-[95vw] p-0"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <h3 className="m-0 truncate text-base font-semibold text-foreground">
              {t("groups.membersDrawer", { name: mgroup?.name || "" })}
            </h3>
            <button
              type="button"
              aria-label={t("common.close")}
              onClick={() => {
                setMgroup(null);
                setMkeyword("");
              }}
              className="grid size-7 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={runMatch}
                disabled={!isAdmin()}
              >
                {t("groups.runMatch")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setAddOpen(true)}
                disabled={!isAdmin()}
              >
                {t("groups.addMember")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={refreshGlUsers}
                disabled={!isAdmin() || glBusy}
              >
                {t("groups.refreshGl")}
              </Button>
              {octoErr ? (
                <Alert variant="warning" className="py-2">
                  <AlertIcon className="pt-0.5">
                    <TriangleAlert className="size-4" />
                  </AlertIcon>
                  <AlertDescription className="text-[13px]">
                    {t("groups.octoErr", { error: octoErr })}
                  </AlertDescription>
                </Alert>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {t("groups.octoMembersCount", { n: octoList.length })}
                </span>
              )}
            </div>
            <Alert variant="info" className="mb-3 py-2">
              <AlertDescription className="text-[13px]">
                {t("groups.matchIntro")}
              </AlertDescription>
            </Alert>
            <Input
              value={mkeyword}
              onChange={setMkeyword}
              placeholder={t("groups.memberPlaceholder")}
              classNames={{ field: "rounded-lg" }}
              className="mb-3 max-w-xs"
            />
            <DataTable<Member>
              rowKey="id"
              dataSource={memShown}
              columns={memberColumns}
              rowHeight={56}
              emptyText={t("common.noData")}
            />
          </div>
        </div>
      </Drawer>

      <CenterMorphModal
        open={addOpen}
        onOpenChange={(next) => {
          if (!next) setAddOpen(false);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={t("groups.addMember")}
          className="max-w-[440px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {t("groups.addMember")}
            </h2>
            <div className="mt-5">
              <SearchSelect
                value={addPid}
                onChange={(v) => setAddPid(v == null ? null : Number(v))}
                placeholder={t("groups.selectPerson")}
                options={people.map((p) => ({
                  value: p.id,
                  label: t("groups.personLabel", {
                    name: p.name,
                    username: p.username || p.gitlab_username || "",
                  }),
                }))}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setAddOpen(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                size="sm"
                className="rounded-lg"
                onClick={async () => {
                  if (!addPid) return;
                  try {
                    await api(`/api/groups/${mgroup!.id}/members`, {
                      method: "POST",
                      body: JSON.stringify({ person_id: addPid }),
                    });
                    setAddOpen(false);
                    setMembers(
                      await api<Member[]>(`/api/groups/${mgroup!.id}/members`),
                    );
                  } catch (e: any) {
                    toast.error(e.message);
                  }
                }}
              >
                {t("common.save")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </Page>
  );
}