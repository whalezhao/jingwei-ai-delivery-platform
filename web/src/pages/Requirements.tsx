import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Circle, Plus, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, isAdmin } from "../api";
import { useGroup } from "../group";
import Page from "../Page";
import type { MsgKey } from "../i18n";
import { pct, qa, type QaSummary } from "../qa";
import DataTable, { type DataTableColumn } from "../ui/DataTable";
import Badge, { type BadgeTone } from "../ui/Badge";
import SearchSelect from "../ui/SearchSelect";
import TagInput from "../ui/TagInput";
import Textarea from "../ui/Textarea";
import { confirm } from "../ui/confirm";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";
import { Button } from "../components/motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "../components/motion/center-morph-modal";
import { Input } from "../components/motion/input";

interface Req {
  id: number;
  system_id: number;
  system_name: string | null;
  code: string;
  title: string;
  description: string | null;
  req_type: "requirement" | "bug";
  status: string;
  iteration_status: string;
  tags: string[];
  claimant_id: number | null;
  claimant_name: string | null;
  // 需求提出人：产品认领并定稿文档后留痕在此，认领权已交给开发
  proposer_id: number | null;
  proposer_name: string | null;
  plan_md: string | null;
  created_at: string | null;
  // 后端按当前登录人算出的两个开关：能不能认领、能不能打开详情
  can_claim: boolean;
  can_open: boolean;
}

const STATUS: Record<string, [BadgeTone, MsgKey]> = {
  pool: ["neutral", "requirements.status.pool"],
  claimed: ["info", "requirements.status.claimed"],
  confirmed: ["success", "requirements.status.confirmed"],
  archived: ["neutral", "requirements.status.archived"],
  online: ["violet", "requirements.status.online"],
};

const ITER: Record<string, [BadgeTone, MsgKey]> = {
  iterating: ["info", "requirements.iter.iterating"],
  formal: ["success", "requirements.iter.formal"],
};

const TYPE: Record<string, [BadgeTone, MsgKey]> = {
  requirement: ["info", "requirements.typeRequirement"],
  bug: ["warning", "requirements.typeBug"],
};

const SETUP_ITEMS: [keyof Setup, MsgKey, string][] = [
  ["gitlab", "requirements.setupGitlab", "/groups"],
  ["system", "requirements.setupProject", "/systems"],
  ["people", "requirements.setupPeople", "/people"],
  ["am", "requirements.setupAm", "/systems"],
  ["runner", "requirements.setupRunner", "/nodes"],
];

interface Setup {
  gitlab: boolean;
  system: boolean;
  people: boolean;
  am: boolean;
  runner: boolean;
}

const QA_ST: Record<string, [BadgeTone, MsgKey]> = {
  queued: ["neutral", "qa.status.queued"],
  running: ["info", "qa.status.running"],
  completed: ["success", "qa.status.completed"],
  completed_with_failures: ["warning", "qa.status.completed_with_failures"],
  failed: ["danger", "qa.status.failed"],
  cancelled: ["neutral", "qa.status.cancelled"],
};

function TestCell({ s }: { s?: QaSummary }) {
  const { t } = useTranslation();
  if (!s || s.status === "none") return <span className="text-muted-foreground">—</span>;
  const pair = QA_ST[s.status];
  return (
    <Badge tone={pair?.[0] ?? "neutral"}>
      {pair ? t(pair[1]) : s.status}
      {s.pass_rate != null ? ` · ${pct(s.pass_rate)}` : ""}
    </Badge>
  );
}

const emptyForm = {
  system_id: undefined as number | undefined,
  req_type: "requirement" as "requirement" | "bug",
  title: "",
  description: "",
};

export default function Requirements() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { groupId, projectId, groups, systems } = useGroup();
  const [rows, setRows] = useState<Req[]>([]);
  const [tests, setTests] = useState<Record<number, QaSummary>>({});
  const [systemFilter, setSystemFilter] = useState<number | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [iterFilter, setIterFilter] = useState<string | undefined>();
  const [tagFilter, setTagFilter] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Req | null>(null);
  const [tagging, setTagging] = useState<Req | null>(null);
  const [tagDraft, setTagDraft] = useState<string[]>([]);
  const form = useForm(emptyForm);

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("project_id", String(projectId));
    if (systemFilter) p.set("system_id", String(systemFilter));
    const data = await api<Req[]>(`/api/requirements?${p}`);
    setRows(data);
    if (isAdmin()) setSetup(await api<Setup>("/api/setup"));
    // QA 未部署/不可达时不阻塞需求列表，测试列显示 —
    const ids = data.map((r) => r.id).join(",");
    if (ids)
      qa<QaSummary[]>(`/api/requirement-test-summaries?ids=${ids}`)
        .then((list) => setTests(Object.fromEntries(list.map((s) => [s.requirement_id, s]))))
        .catch(() => setTests({}));
  };
  useEffect(() => {
    load();
  }, [groupId, projectId, systemFilter]);
  // 全局条件变了，表格内的系统筛选可能已不在范围内
  useEffect(() => {
    setSystemFilter(undefined);
  }, [groupId, projectId]);

  const openEdit = (r: Req) => {
    setEditing(r);
    form.reset({
      system_id: r.system_id,
      req_type: r.req_type,
      title: r.title,
      description: r.description ?? "",
    });
    setOpen(true);
  };

  const openCreate = () => {
    setEditing(null);
    form.reset({ ...emptyForm, system_id: scopedSystems[0]?.id });
    setOpen(true);
  };

  const save = async () => {
    if (!form.validate({ title: { required: true, message: t("common.required") } }))
      return;
    const v = form.values;
    try {
      if (editing) {
        await api(`/api/requirements/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify({
            title: v.title,
            description: v.description || null,
          }),
        });
        toast.success(t("requirements.saved"));
        setOpen(false);
        setEditing(null);
        load();
      } else {
        const r = await api<Req>("/api/requirements", {
          method: "POST",
          body: JSON.stringify({
            system_id: v.system_id,
            req_type: v.req_type,
            title: v.title,
            description: v.description || null,
          }),
        });
        toast.success(t("requirements.created", { code: r.code }));
        setOpen(false);
        nav(`/reqs/${r.id}`);
      }
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const saveTags = async () => {
    if (!tagging) return;
    try {
      const updated = await api<Req>(`/api/requirements/${tagging.id}/tags`, {
        method: "PUT",
        body: JSON.stringify({ tags: tagDraft }),
      });
      setRows((prev) => prev.map((x) => (x.id === tagging.id ? updated : x)));
      setTagging(null);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const remove = async (r: Req) => {
    const ok = await confirm({
      title: t("requirements.deleteConfirm"),
      description: t("requirements.deleteDesc"),
      confirmText: t("requirements.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/requirements/${r.id}`, { method: "DELETE" });
      toast.success(t("requirements.deleted", { code: r.code }));
      setRows((prev) => prev.filter((x) => x.id !== r.id));
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const claim = async (r: Req) => {
    try {
      await api(`/api/requirements/${r.id}/claim`, { method: "POST" });
      toast.success(t("requirements.claimed"));
      nav(`/reqs/${r.id}`);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  // 列表本来就整页取回（无服务端分页），三个维度直接在前端过滤
  const tagOptions = Array.from(new Set(rows.flatMap((r) => r.tags || []))).sort();
  const term = keyword.trim().toLowerCase();
  const shown = rows.filter(
    (r) =>
      (!statusFilter || r.status === statusFilter) &&
      (!iterFilter || r.iteration_status === iterFilter) &&
      (!tagFilter || (r.tags || []).includes(tagFilter)) &&
      (!term ||
        [r.code, r.title, r.description, r.system_name, r.claimant_name, r.proposer_name]
          .some((s) => (s || "").toLowerCase().includes(term))),
  );
  const filtering = !!(
    projectId ||
    groupId ||
    systemFilter ||
    statusFilter ||
    iterFilter ||
    tagFilter ||
    term
  );
  // 表格内系统筛选和新建需求都只看当前项目/研发组下的系统
  const scopedSystems = systems.filter(
    (s) =>
      (!groupId || s.group_id === groupId) &&
      (!projectId || groups.find((g) => g.id === s.group_id)?.project_id === projectId)
  );

  const columns: DataTableColumn<Req>[] = [
    {
      title: t("requirements.colCode"),
      dataIndex: "code",
      width: 235,
      render: (v) => <span className="font-mono text-[12px]">{v}</span>,
    },
    {
      title: t("requirements.colType"),
      dataIndex: "req_type",
      width: 100,
      render: (v) => {
        const pair = TYPE[v];
        return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : v}</Badge>;
      },
    },
    {
      title: t("requirements.colTitle"),
      dataIndex: "title",
      // 定宽列总和已超过窄屏容器，不声明宽度会被压到 64px
      width: 240,
      render: (v, r) =>
        r.can_open ? (
          <span className="truncate">{v}</span>
        ) : (
          <span className="truncate text-muted-foreground">{v}</span>
        ),
    },
    {
      title: t("requirements.colProject"),
      dataIndex: "system_name",
      width: 130,
      render: (v) => <span className="truncate">{v || "—"}</span>,
    },
    {
      title: t("requirements.colStatus"),
      dataIndex: "status",
      width: 110,
      render: (v) => {
        const pair = STATUS[v];
        return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : v}</Badge>;
      },
    },
    {
      title: t("qa.req.colTest"),
      width: 150,
      render: (_, r) => <TestCell s={tests[r.id]} />,
    },
    {
      title: t("requirements.colIter"),
      dataIndex: "iteration_status",
      width: 100,
      render: (v) => {
        const pair = ITER[v];
        return <Badge tone={pair?.[0] ?? "neutral"}>{pair ? t(pair[1]) : v}</Badge>;
      },
    },
    {
      title: t("requirements.colTag"),
      dataIndex: "tags",
      width: 150,
      // 定高行放不下内联标签编辑器：这里只做摘要，点开弹窗编辑
      render: (v: string[] | undefined, r) =>
        !r.can_open ? (
          <span className="truncate text-muted-foreground">{(v || []).join("、") || "—"}</span>
        ) : (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setTagDraft(v || []);
              setTagging(r);
            }}
            className="w-full truncate text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground"
          >
            {(v || []).join("、") || t("requirements.tagPlaceholder")}
          </button>
        ),
    },
    {
      title: t("requirements.colClaimant"),
      dataIndex: "claimant_name",
      width: 110,
      render: (v, r) =>
        v ? (
          <span className="truncate">{v}</span>
        ) : r.proposer_name ? (
          // 定稿后认领权已交还开发，产品留痕为提出人
          <span className="truncate text-muted-foreground">
            {t("requirements.proposedBy", { name: r.proposer_name })}
          </span>
        ) : (
          "—"
        ),
    },
    {
      title: t("requirements.colActions"),
      width: 150,
      render: (_, r) => (
        <div className="flex items-center gap-2 text-[12px]">
          {r.can_claim ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                claim(r);
              }}
              className="whitespace-nowrap text-primary hover:underline"
            >
              {t("requirements.claim")}
            </button>
          ) : r.can_open ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                nav(`/reqs/${r.id}`);
              }}
              className="whitespace-nowrap text-primary hover:underline"
            >
              {r.status === "claimed" ? t("requirements.continueDiscuss") : t("requirements.view")}
            </button>
          ) : null}
          {r.can_open &&
            r.status !== "confirmed" &&
            r.status !== "archived" &&
            r.status !== "online" && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  openEdit(r);
                }}
                className="whitespace-nowrap text-primary hover:underline"
              >
                {t("requirements.edit")}
              </button>
            )}
          {(isAdmin() || r.claimant_id === Number(localStorage.getItem("vega_me"))) && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                remove(r);
              }}
              className="whitespace-nowrap text-destructive hover:underline"
            >
              {t("requirements.delete")}
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <Page
      title={t("nav.requirements")}
      desc={t("requirements.desc")}
      actions={
        <Button size="sm" className="rounded-lg" onClick={openCreate}>
          <Plus className="size-4" />
          {t("requirements.new")}
        </Button>
      }
    >
      {setup && Object.values(setup).some((v) => !v) && (
        <div className="setup-card">
          <div className="setup-head">{t("requirements.setupHead")}</div>
          <div className="setup-items">
            {SETUP_ITEMS.map(([k, label, to]) => (
              <button key={k} type="button" className={"setup-item" + (setup[k] ? " done" : "")} onClick={() => nav(to)}>
                {setup[k] ? (
                  <CheckCircle2 className="size-4 text-success" />
                ) : (
                  <Circle className="size-4 text-muted-foreground" />
                )}
                {t(label)}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          value={keyword}
          onChange={setKeyword}
          placeholder={t("requirements.searchPlaceholder")}
          leftIcon={<Search />}
          classNames={{ field: "rounded-lg" }}
          className="w-[240px]"
        />
        <SearchSelect
          value={systemFilter}
          onChange={(v) => setSystemFilter(v == null ? undefined : Number(v))}
          placeholder={t("requirements.filterSystem")}
          clearable
          options={scopedSystems.map((s) => ({ value: s.id, label: `${s.path}（${s.code_prefix}）` }))}
          className="w-[260px]"
        />
        <SearchSelect
          value={statusFilter}
          onChange={(v) => setStatusFilter(v == null ? undefined : String(v))}
          placeholder={t("requirements.filterStatus")}
          clearable
          options={Object.entries(STATUS).map(([v, pair]) => ({ value: v, label: t(pair[1]) }))}
          className="w-[150px]"
        />
        <SearchSelect
          value={iterFilter}
          onChange={(v) => setIterFilter(v == null ? undefined : String(v))}
          placeholder={t("requirements.filterIter")}
          clearable
          options={Object.entries(ITER).map(([v, pair]) => ({ value: v, label: t(pair[1]) }))}
          className="w-[150px]"
        />
        <SearchSelect
          value={tagFilter}
          onChange={(v) => setTagFilter(v == null ? undefined : String(v))}
          placeholder={t("requirements.filterTag")}
          clearable
          options={tagOptions.map((tag) => ({ value: tag, label: tag }))}
          className="w-[170px]"
        />
      </div>

      <DataTable<Req>
        rowKey="id"
        dataSource={shown}
        columns={columns}
        onRowClick={(r) =>
          r.can_open ? nav(`/reqs/${r.id}`) : toast.info(t("requirements.cannotOpen"))
        }
        emptyText={
          <div className="py-10 text-center">
            <p className="m-0 text-sm text-foreground">
              {filtering ? t("requirements.emptyFiltered") : t("requirements.empty")}
            </p>
            <p className="m-0 mt-1 text-xs text-muted-foreground">
              {t("requirements.emptyHint")}
            </p>
          </div>
        }
      />

      <CenterMorphModal
        open={open}
        onOpenChange={(next) => {
          if (!next) {
            setOpen(false);
            setEditing(null);
          }
        }}
      >
        <CenterMorphModalContent
          ariaLabel={
            editing ? t("requirements.editTitle") : t("requirements.createTitle")
          }
          className="max-w-[560px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {editing ? t("requirements.editTitle") : t("requirements.createTitle")}
            </h2>
            <div className="mt-5 flex flex-col gap-4">
              <Field
                label={t("requirements.projectLabel")}
                tip={t("requirements.projectTooltip")}
                error={form.errors.system_id}
              >
                <SearchSelect
                  value={form.values.system_id}
                  onChange={(v) => form.set("system_id", v == null ? undefined : Number(v))}
                  disabled={!!editing}
                  options={scopedSystems.map((s) => ({
                    value: s.id,
                    label: `${s.path}（${s.code_prefix}）`,
                  }))}
                  placeholder={t("requirements.projectLabel")}
                />
              </Field>
              <Field label={t("requirements.typeLabel")}>
                <SearchSelect
                  value={form.values.req_type}
                  onChange={(v) =>
                    form.set("req_type", (v as "requirement" | "bug") || "requirement")
                  }
                  disabled={!!editing}
                  options={[
                    { value: "requirement", label: t("requirements.typeRequirement") },
                    { value: "bug", label: t("requirements.typeBug") },
                  ]}
                />
              </Field>
              <Input
                label={t("requirements.titleLabel")}
                value={form.values.title}
                onChange={(v) => form.set("title", v)}
                error={form.errors.title}
                classNames={{ field: "rounded-lg" }}
              />
              <Textarea
                label={t("requirements.descLabel")}
                rows={4}
                value={form.values.description}
                onChange={(v) => form.set("description", v)}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => {
                  setOpen(false);
                  setEditing(null);
                }}
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
        open={!!tagging}
        onOpenChange={(next) => {
          if (!next) setTagging(null);
        }}
      >
        <CenterMorphModalContent
          ariaLabel={t("requirements.colTag")}
          className="max-w-[460px]"
        >
          <div className="p-6">
            <h2 className="m-0 text-lg font-semibold tracking-[-0.01em] text-foreground">
              {t("requirements.colTag")}
            </h2>
            <p className="m-0 mt-1 font-mono text-[12px] text-muted-foreground">
              {tagging?.code}
            </p>
            <div className="mt-4">
              <TagInput
                value={tagDraft}
                onChange={setTagDraft}
                placeholder={t("requirements.tagPlaceholder")}
              />
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setTagging(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button size="sm" className="rounded-lg" onClick={saveTags}>
                {t("common.save")}
              </Button>
            </div>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </Page>
  );
}