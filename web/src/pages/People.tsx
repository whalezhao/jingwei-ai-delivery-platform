import { useEffect, useState } from "react";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import type { MsgKey } from "../i18n";
import { useGroup } from "../group";
import Page from "../Page";
import PersonAvatar from "../components/PersonAvatar";
import AvatarPicker from "../components/AvatarPicker";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import EffortSelect from "../ui/EffortSelect";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Input } from "../components/motion/input";
import { Switch } from "../components/motion/switch";
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

interface Person {
  id: number;
  name: string;
  username: string | null;
  avatar: string | null;
  gitlab_username: string | null;
  gitlab_email: string | null;
  octo_uid: string | null;
  is_admin: boolean;
  llm_base_url: string | null;
  llm_model: string | null;
  llm_effort: string | null;
  has_llm_key: boolean;
  has_am_key: boolean;
  roles: string[];
}

// 研发体系角色：product 认领未定稿需求并写需求，dev 定稿后接管做方案与开发；
// 其余三个目前只用于标识与展示，不参与认领门禁
const ROLES: { value: string; label: MsgKey; tone: BadgeTone }[] = [
  { value: "project_lead", label: "people.role.projectLead", tone: "violet" },
  { value: "pm", label: "people.role.pm", tone: "info" },
  { value: "product", label: "people.role.product", tone: "info" },
  { value: "tech_lead", label: "people.role.techLead", tone: "violet" },
  { value: "dev", label: "people.role.dev", tone: "success" },
  { value: "qa", label: "people.role.qa", tone: "warning" },
];

const ROLE_LABEL: Record<string, MsgKey> = Object.fromEntries(
  ROLES.map((r) => [r.value, r.label]),
);

const empty = {
  name: "",
  avatar: "",
  username: "",
  gitlab_username: "",
  gitlab_email: "",
  password: "",
  is_admin: false,
  roles: [] as string[],
  llm_base_url: "",
  llm_api_key: "",
  llm_model: "",
  llm_effort: "",
  am_user_key: "",
};

export default function People() {
  const { t } = useTranslation();
  const { groupId } = useGroup();
  const [rows, setRows] = useState<Person[]>([]);
  const [editing, setEditing] = useState<Person | null>(null);
  const [keyword, setKeyword] = useState("");
  const form = useForm(empty);
  // 个人 GitLab 账号（一个可多个；研发组里选一个用）
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accBase, setAccBase] = useState("");
  const [accToken, setAccToken] = useState("");
  const [accBusy, setAccBusy] = useState(false);

  const loadAccounts = (pid: number) =>
    api<any[]>(`/api/people/${pid}/gitlab-accounts`)
      .then(setAccounts)
      .catch(() => setAccounts([]));

  const addAccount = async () => {
    if (!editing?.id || !accBase.trim() || !accToken.trim()) return;
    setAccBusy(true);
    try {
      await api(`/api/people/${editing.id}/gitlab-accounts`, {
        method: "POST",
        body: JSON.stringify({
          base_url: accBase.trim(),
          token: accToken.trim(),
        }),
      });
      setAccBase("");
      setAccToken("");
      await loadAccounts(editing.id);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setAccBusy(false);
    }
  };

  const delAccount = async (aid: number) => {
    if (!editing?.id) return;
    try {
      await api(`/api/people/${editing.id}/gitlab-accounts/${aid}`, {
        method: "DELETE",
      });
      await loadAccounts(editing.id);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const load = () =>
    api<Person[]>(`/api/people${groupId ? `?group_id=${groupId}` : ""}`).then(
      setRows,
    );
  useEffect(() => {
    load();
  }, [groupId]);

  const open = (p?: Person) => {
    setEditing(p ?? ({ id: 0 } as Person));
    form.reset({
      name: p?.name ?? "",
      avatar: p?.avatar ?? "",
      username: p?.username ?? "",
      gitlab_username: p?.gitlab_username ?? "",
      gitlab_email: p?.gitlab_email ?? "",
      password: "",
      is_admin: p?.is_admin ?? false,
      roles: p?.roles ?? [],
      llm_base_url: p?.llm_base_url ?? "",
      llm_api_key: "",
      llm_model: p?.llm_model ?? "",
      llm_effort: p?.llm_effort ?? "",
      am_user_key: "",
    });
    setAccBase("");
    setAccToken("");
    if (p) loadAccounts(p.id);
    else setAccounts([]);
  };

  const save = async () => {
    if (
      !form.validate({
        name: { required: true, message: t("common.required") },
      })
    )
      return;
    const body: any = { ...form.values };
    // 空 key 不提交，避免把已有配置清掉
    for (const k of ["password", "llm_api_key", "am_user_key"])
      if (!body[k]) delete body[k];
    // 非管理员：登录名/管理员标记/角色/重置密码都不许改（表单回传的是只读值，不是修改意图）
    if (!isAdmin())
      for (const k of [
        "is_admin",
        "username",
        "gitlab_username",
        "password",
        "roles",
      ])
        delete body[k];
    try {
      if (editing?.id)
        await api(`/api/people/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      else
        await api("/api/people", {
          method: "POST",
          body: JSON.stringify(body),
        });
      toast.success(t("common.saved"));
      setEditing(null);
      // 改的是自己 → 通知顶栏头像立即刷新
      if (String(editing?.id) === localStorage.getItem("vega_me"))
        window.dispatchEvent(new Event("vega-me-updated"));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (row: Person) => {
    const ok = await confirm({
      title: t("people.deleteConfirm"),
      confirmText: t("common.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/people/${row.id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [r.name, r.username, r.gitlab_username, r.gitlab_email].some((s) =>
          (s || "").toLowerCase().includes(term),
        ),
      )
    : rows;

  const columns: DataTableColumn<Person>[] = [
    {
      title: t("people.colName"),
      dataIndex: "name",
      width: 200,
      render: (v, r) => (
        <span className="flex min-w-0 items-center gap-2">
          <PersonAvatar name={v} avatar={r.avatar} size={24} />
          <span className="truncate">{v}</span>
          {r.is_admin ? <Badge tone="violet">admin</Badge> : null}
        </span>
      ),
    },
    {
      title: t("people.colUsername"),
      dataIndex: "username",
      width: 105,
      render: (v) => <span className="font-mono text-[12px]">{v || "—"}</span>,
    },
    {
      title: t("people.colGitlab"),
      dataIndex: "gitlab_username",
      width: 105,
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    { title: t("people.colEmail"), dataIndex: "gitlab_email", width: 145 },
    {
      title: t("people.colOcto"),
      dataIndex: "octo_uid",
      width: 95,
      render: (v) =>
        v ? <span className="font-mono text-[12px]">{v}</span> : (
          <Badge>{t("people.unbound")}</Badge>
        ),
    },
    {
      title: t("people.colLlm"),
      width: 105,
      render: (_, r) =>
        r.has_llm_key ? r.llm_model || t("people.default") : "—",
    },
    {
      title: t("people.colRoles"),
      dataIndex: "roles",
      width: 155,
      // 定高行放不下多枚徽标：最多两枚，其余折叠成计数
      render: (v: string[]) => {
        const list = v || [];
        if (!list.length) return <Badge>{t("people.notSet")}</Badge>;
        const label = (x: string) => (ROLE_LABEL[x] ? t(ROLE_LABEL[x]) : x);
        const shown = list.slice(0, 2);
        return (
          <span className="flex items-center gap-1 overflow-hidden">
            {shown.map((x) => (
              <Badge key={x} tone={ROLES.find((r) => r.value === x)?.tone}>
                {label(x)}
              </Badge>
            ))}
            {list.length > shown.length ? (
              <Badge>+{list.length - shown.length}</Badge>
            ) : null}
          </span>
        );
      },
    },
    {
      title: t("people.colAmKey"),
      width: 75,
      render: (_, r) => (r.has_am_key ? t("people.configured") : "—"),
    },
    {
      title: t("common.actions"),
      width: 80,
      render: (_, r) => (
        <div className="flex items-center gap-1">
          {(isAdmin() || String(r.id) === localStorage.getItem("vega_me")) && (
            <button
              type="button"
              onClick={() => open(r)}
              aria-label={t("common.edit")}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Pencil className="size-3.5" />
            </button>
          )}
          {isAdmin() ? (
            <button
              type="button"
              onClick={() => remove(r)}
              aria-label={t("common.delete")}
              className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
            </button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <Page
      title={t("nav.people")}
      desc={t("people.desc")}
      actions={
        isAdmin() ? (
          <Button size="sm" className="rounded-lg" onClick={() => open()}>
            <Plus className="size-4" />
            {t("people.new")}
          </Button>
        ) : null
      }
    >
      <Input
        value={keyword}
        onChange={setKeyword}
        placeholder={t("people.searchPlaceholder")}
        leftIcon={<Search />}
        classNames={{ field: "rounded-lg" }}
        className="mb-3 max-w-sm"
      />

      <DataTable<Person>
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
            editing?.id ? t("people.editTitle") : t("people.createTitle")
          }
          className="max-w-[680px]"
        >
          <div className="flex max-h-[82vh] flex-col">
            <h2 className="m-0 px-6 pb-1 pt-5 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {editing?.id ? t("people.editTitle") : t("people.createTitle")}
            </h2>
            <div className="flex flex-col gap-4 overflow-y-auto px-6 py-4">
              <Field label={t("people.avatar")}>
                <AvatarPicker
                  value={form.values.avatar}
                  onChange={(v) => form.set("avatar", v)}
                />
              </Field>
              <Input
                label={t("people.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("people.username")} tip={t("people.usernameTooltip")}>
                <Input
                  value={form.values.username}
                  onChange={(v) => form.set("username", v)}
                  disabled={!isAdmin()}
                  classNames={{ field: "rounded-lg" }}
                />
              </Field>
              {editing?.id ? (
                <Field
                  label={t("people.gitlabAccounts")}
                  tip={t("people.gitlabAccountsTooltip")}
                >
                  <div className="flex flex-col gap-2">
                    {accounts.map((a) => (
                      <div
                        key={a.id}
                        className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5"
                      >
                        <Badge tone={a.has_token ? "success" : "danger"}>
                          {a.username}
                        </Badge>
                        <span
                          className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground"
                          title={a.base_url}
                        >
                          {a.base_url}
                        </span>
                        <button
                          type="button"
                          onClick={() => delAccount(a.id)}
                          className="shrink-0 text-xs text-destructive hover:underline"
                        >
                          {t("common.delete")}
                        </button>
                      </div>
                    ))}
                    <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                      <Input
                        value={accBase}
                        onChange={setAccBase}
                        placeholder={t("people.accountBaseUrl")}
                        classNames={{ field: "rounded-lg" }}
                      />
                      <Input
                        type="password"
                        value={accToken}
                        onChange={setAccToken}
                        placeholder={t("people.accountToken")}
                        classNames={{ field: "rounded-lg" }}
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        className="rounded-lg"
                        onClick={addAccount}
                        disabled={accBusy || !isAdmin()}
                      >
                        {t("people.addAccount")}
                      </Button>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {t("people.accountHint")}
                    </span>
                  </div>
                </Field>
              ) : null}
              <Input
                label={t("people.gitlabUsername")}
                value={form.values.gitlab_username}
                onChange={(v) => form.set("gitlab_username", v)}
                disabled={!!editing?.id}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("people.gitlabEmail")}
                value={form.values.gitlab_email}
                onChange={(v) => form.set("gitlab_email", v)}
                classNames={{ field: "rounded-lg" }}
              />
              {/* 非管理员改自己不能重置密码：走顶栏「个人设置」里的修改密码（校验旧密码） */}
              {isAdmin() ? (
                <Input
                  type="password"
                  label={
                    editing?.id
                      ? t("people.resetPassword")
                      : t("people.initialPassword")
                  }
                  value={form.values.password}
                  onChange={(v) => form.set("password", v)}
                  classNames={{ field: "rounded-lg" }}
                />
              ) : null}
              <Field label={t("people.admin")}>
                <Switch
                  checked={!!form.values.is_admin}
                  onCheckedChange={(v) => form.set("is_admin", v)}
                  disabled={!isAdmin()}
                />
              </Field>
              <Field
                label={t("people.roles")}
                tip={t("people.roleTooltip")}
                hint={t("people.roleExtra")}
              >
                <MultiSelect
                  value={form.values.roles}
                  onValueChange={(v) => form.set("roles", v)}
                >
                  <MultiSelectTrigger className="rounded-lg">
                    <MultiSelectValue
                      placeholder={t("people.rolePlaceholder")}
                    />
                    <MultiSelectInput
                      aria-label={t("common.search")}
                      placeholder={t("common.search")}
                      className="h-7 min-w-24 text-sm outline-none placeholder:text-muted-foreground"
                    />
                  </MultiSelectTrigger>
                  <MultiSelectContent>
                    <MultiSelectList>
                      {ROLES.map((role) => (
                        <MultiSelectItem
                          key={role.value}
                          value={role.value}
                          textValue={t(role.label)}
                        >
                          {t(role.label)}
                        </MultiSelectItem>
                      ))}
                      <MultiSelectEmpty>{t("common.noData")}</MultiSelectEmpty>
                    </MultiSelectList>
                  </MultiSelectContent>
                </MultiSelect>
              </Field>
              <Input
                label={t("people.llmBaseUrl")}
                value={form.values.llm_base_url}
                onChange={(v) => form.set("llm_base_url", v)}
                placeholder="https://…/v1"
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                type="password"
                label={t("people.llmApiKey")}
                value={form.values.llm_api_key}
                onChange={(v) => form.set("llm_api_key", v)}
                placeholder={
                  editing?.has_llm_key ? t("people.configuredKeep") : ""
                }
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("people.llmModel")}
                value={form.values.llm_model}
                onChange={(v) => form.set("llm_model", v)}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("people.effort")} tip={t("people.effortTooltip")}>
                <EffortSelect
                  value={form.values.llm_effort}
                  onChange={(v) => form.set("llm_effort", v)}
                />
              </Field>
              <Input
                type="password"
                label={t("people.amKey")}
                value={form.values.am_user_key}
                onChange={(v) => form.set("am_user_key", v)}
                placeholder={
                  editing?.has_am_key ? t("people.configuredKeep") : ""
                }
                classNames={{ field: "rounded-lg" }}
              />
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
    </Page>
  );
}