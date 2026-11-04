import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { api, isAdmin } from "../api";
import type { MsgKey } from "../i18n";
import AmSelect from "../components/AmSelect";
import Page from "../Page";
import { PAGE } from "../table";

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
  gitlab_accounts: { id: number; username: string; base_url: string; has_token: boolean }[];
  octo_uid: string | null;
  octo_nickname: string | null;
  match_status: string;
  match_confidence: number | null;
  match_reason: string | null;
  manual_lock: boolean;
}

/** 下拉里的账号/群成员标识都很长：一行截断，悬停给全文。 */
const optionTip = (label: string) => (
  <Tooltip title={label} placement="topLeft" mouseEnterDelay={0.3}>
    <span className="sel-ellipsis">{label}</span>
  </Tooltip>
);

// 绑定来源只区分两种：系统对配出来的，和人工调整的（pending 只是 AI 建议、尚未绑定）
const STATUS_TAG: Record<string, [string, MsgKey]> = {
  auto_exact: ["green", "groups.status.matched"],
  auto_ai: ["green", "groups.status.matched"],
  auto: ["green", "groups.status.matched"],
  manual: ["purple", "groups.status.manual"],
  pending: ["default", "groups.status.unmatched"],
  unmatched: ["default", "groups.status.unmatched"],
};

const empty: any = {
  name: "",
  project_id: undefined,
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
  tech_lead_person_ids: [],
  scan_enabled: true,
  auto_webhook: true,
};

export default function Groups() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [rows, setRows] = useState<Group[]>([]);
  const [keyword, setKeyword] = useState("");
  const [mkeyword, setMkeyword] = useState("");
  const [editing, setEditing] = useState<any>(null);
  const [form] = Form.useForm();
  // 成员抽屉
  const [mgroup, setMgroup] = useState<Group | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [octoList, setOctoList] = useState<{ uid: string; nickname: string }[]>([]);
  const [octoErr, setOctoErr] = useState<string | null>(null);
  const [people, setPeople] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [addPid, setAddPid] = useState<number | null>(null);
  // 群号下拉：本组 bot 所在的群
  const [octoGroups, setOctoGroups] = useState<{ group_no: string; name: string }[]>([]);
  const [octoGroupsErr, setOctoGroupsErr] = useState<string | null>(null);
  const [glBusy, setGlBusy] = useState(false);

  // 表格里显示中文名而不是 id：octo 群名按组用各组 bot 拉，am 团队名取当前用户的团队列表
  const [octoNames, setOctoNames] = useState<Record<string, string>>({});
  const [teamNames, setTeamNames] = useState<Record<string, string>>({});

  const loadNames = async (list: Group[]) => {
    api<any[]>("/api/am/teams")
      .then((teams) => setTeamNames(Object.fromEntries(teams.map((tm) => [tm.id, tm.name]))))
      .catch(() => {});
    for (const g of list) {
      if (!g.octo_group_no) continue;
      try {
        const d = await api<{ items: { group_no: string; name: string }[] }>(`/api/groups/${g.id}/octo-groups`);
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
    setEditing(g ? { ...g, gitlab_token: "", llm_api_key: "", octo_bot_token: "" } : { ...empty });
    setOctoGroups([]);
    setOctoGroupsErr(null);
    form.resetFields();
  };

  const save = async (v: any) => {
    const body = { ...v };
    for (const k of ["gitlab_token", "llm_api_key", "octo_bot_token"]) if (!body[k]) delete body[k];
    try {
      if (editing.id) await api(`/api/groups/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/groups", { method: "POST", body: JSON.stringify(body) });
      message.success(t("groups.saved"));
      setEditing(null);
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const testBot = async (g: Group) => {
    try {
      const d = await api<any>(`/api/groups/${g.id}/octo-test`, { method: "POST" });
      if (d.ok)
        message.success(t("groups.testOk", { via: d.dedicated ? t("groups.viaOwn") : t("groups.viaGlobal") }));
      else message.error(t("groups.testFail"));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const runScan = async (g: Group) => {
    try {
      const d = await api<any>(`/api/groups/${g.id}/scan`, { method: "POST" });
      message.success(
        `${t("groups.scanDone", { projects: d.projects_added, webhooks: d.webhooks_added })}${
          d.error ? `（${d.error}）` : ""
        }`,
      );
      nav("/projects");
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const loadOctoGroups = async () => {
    try {
      // 新建组还没有 id：直接用全局 bot 的群列表，不必先保存
      const d = await api<{ items: { group_no: string; name: string }[]; error: string | null }>(
        editing?.id ? `/api/groups/${editing.id}/octo-groups` : "/api/integrations/octo-groups",
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
      const d = await api<{ items: unknown[] }>(`/api/groups/${mgroup.id}/gitlab-users/refresh`, {
        method: "POST",
      });
      message.success(t("groups.refreshDone", { n: d.items?.length ?? 0 }));
    } catch (e: any) {
      message.error(e.message);
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
      message.success(accountId ? t("groups.boundGitlab") : t("groups.unboundGitlab"));
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const openMembers = async (g: Group) => {
    setMgroup(g);
    const [ms, oc, ps] = await Promise.all([
      api<Member[]>(`/api/groups/${g.id}/members`),
      api<{ items: { uid: string; nickname: string }[]; error: string | null }>(
        `/api/groups/${g.id}/octo-members`,
      ),
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
      const d = await api<any>(`/api/groups/${mgroup.id}/match/run`, { method: "POST" });
      const s = d.stats || {};
      message.success(
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
      message.error(e.message);
    }
  };

  const manualBind = async (m: Member, uid: string | null) => {
    if (!mgroup) return;
    try {
      const d = await api<any>(`/api/groups/${mgroup.id}/match/${m.person_id}`, {
        method: "PUT",
        body: JSON.stringify({ octo_uid: uid }),
      });
      message.success(
        uid
          ? d.released
            ? t("groups.boundOctoReleased", { released: d.released })
            : t("groups.boundOcto")
          : t("groups.unboundOcto"),
      );
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const gterm = keyword.trim().toLowerCase();
  const grpShown = gterm
    ? rows.filter((r) =>
        [r.name, r.gitlab_base_url].some((s) => (s || "").toLowerCase().includes(gterm)),
      )
    : rows;
  const mterm = mkeyword.trim().toLowerCase();
  // 排序前先复制：直接 sort(members) 会就地改 state 数组
  const memShown = [
    ...(mterm
      ? members.filter((m) =>
          [m.person_name, m.gitlab_account_username, m.gitlab_username, m.gitlab_email, m.octo_nickname, m.octo_uid].some((s) =>
            (s || "").toLowerCase().includes(mterm),
          ),
        )
      : members),
  ].sort((a, b) => Number(!!b.octo_uid) - Number(!!a.octo_uid));

  return (
    <Page
      title={t("nav.groups")}
      desc={t("groups.desc")}
      actions={
        isAdmin() && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => open()}>
            {t("groups.new")}
          </Button>
        )
      }
    >
      <div className="page-card">
        <Input.Search
          style={{ width: 280, marginBottom: 12 }}
          allowClear
          placeholder={t("groups.searchPlaceholder")}
          onSearch={(v) => setKeyword(v.trim())}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
        <ResizableTable<Group>
          rowKey="id"
          dataSource={grpShown}
          pagination={PAGE}
          columns={[
            { title: t("groups.colGroup"), dataIndex: "name" },
            {
              title: t("groups.colProject"),
              dataIndex: "project_name",
              render: (v, r) => (v ? v : <Tag>{t("groups.noProject")}</Tag>),
            },
            {
              title: t("groups.colGitlab"),
              dataIndex: "gitlab_base_url",
              render: (v, r) => (
                <>
                  <span className="mono">{v}</span>{" "}
                  {r.has_gitlab_token ? <Tag color="green">token ✓</Tag> : <Tag color="red">{t("groups.noToken")}</Tag>}
                </>
              ),
            },
            {
              title: t("groups.colOcto"),
              dataIndex: "octo_group_no",
              render: (v, r) => (
                <>
                  {v ? (
                    octoNames[v] ? <span title={v}>{octoNames[v]}</span> : <span className="mono">{v}</span>
                  ) : (
                    "—"
                  )}{" "}
                  {v &&
                    (r.has_octo_bot_token ? (
                      <Tooltip title={t("groups.ownBotTip")}>
                        <Tag color="green">{t("integrations.ownBot")}</Tag>
                      </Tooltip>
                    ) : (
                      <Tooltip title={t("groups.globalBotTip")}>
                        <Tag>{t("integrations.globalBot")}</Tag>
                      </Tooltip>
                    ))}
                </>
              ),
            },
            {
              title: t("groups.colAmTeam"),
              dataIndex: "am_team_id",
              render: (v) =>
                v ? teamNames[v] ? <span title={v}>{teamNames[v]}</span> : <span className="mono">{v}</span> : "—",
            },
            { title: t("groups.colLlm"), render: (_, r) => (r.has_llm_key ? r.llm_model || t("groups.defaultLlm") : "—") },
            {
              title: t("groups.colScan"),
              dataIndex: "scan_enabled",
              render: (v) => (v ? <Tag color="green">{t("groups.on")}</Tag> : <Tag>{t("groups.off")}</Tag>),
            },
            {
              title: t("common.actions"),
              width: 300,
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  <a onClick={() => openMembers(r)}>{t("groups.members")}</a>
                  <a onClick={() => open(r)}>{t("common.edit")}</a>
                  <a onClick={() => runScan(r)}>{t("groups.scanProjects")}</a>
                  <a onClick={() => testBot(r)}>{t("groups.testBot")}</a>
                  {isAdmin() && (
                    <Popconfirm
                      title={t("groups.deleteConfirm")}
                      onConfirm={async () => {
                        try {
                          await api(`/api/groups/${r.id}`, { method: "DELETE" });
                          load();
                        } catch (e: any) {
                          message.error(e.message);
                        }
                      }}
                    >
                      <a className="danger-link">{t("common.delete")}</a>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </div>

      <Modal
        open={!!editing}
        title={editing?.id ? t("groups.editTitle") : t("groups.createTitle")}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || empty} onFinish={save}>
          <Form.Item name="name" label={t("groups.name")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="project_id" label={t("groups.project")} tooltip={t("groups.projectTooltip")}>
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder={t("groups.projectPlaceholder")}
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="gitlab_base_url" label={t("groups.gitlabUrl")}>
            <Input placeholder="https://git.example.com" />
          </Form.Item>
          <Form.Item name="gitlab_bot_username" label={t("groups.gitlabBot")} tooltip={t("groups.gitlabBotTooltip")}>
            <Input placeholder={t("groups.gitlabBotPlaceholder")} />
          </Form.Item>
          <Form.Item name="gitlab_token" label={t("groups.gitlabToken")}>
            <Input.Password placeholder={editing?.has_gitlab_token ? t("groups.configuredKeep") : ""} />
          </Form.Item>
          <Form.Item
            name="octo_group_no"
            label={t("groups.octoGroup")}
            tooltip={t("groups.octoGroupTooltip")}
          >
            <Select
              showSearch
              allowClear
              optionFilterProp="label"
              placeholder={t("groups.octoGroupPlaceholder")}
              onOpenChange={(o) => o && loadOctoGroups()}
              notFoundContent={octoGroupsErr || t("groups.octoNoGroups")}
              options={octoGroups.map((g) => ({
                value: g.group_no,
                label: t("groups.octoGroupLabel", { name: g.name, id: g.group_no.slice(0, 8) }),
              }))}
            />
          </Form.Item>
          <Form.Item name="octo_bot_token" label={t("groups.octoBotToken")}>
            <Input.Password
              placeholder={
                editing?.has_octo_bot_token ? t("groups.configuredKeep") : t("groups.octoBotTokenPlaceholder")
              }
            />
          </Form.Item>
          <Form.Item
            name="tech_lead_person_ids"
            label={t("groups.techLead")}
            tooltip={t("groups.techLeadTooltip")}
          >
            <Select
              mode="multiple"
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder={t("groups.multiPlaceholder")}
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="am_team_id" label={t("groups.amTeam")}>
            <AmSelect kind="team" placeholder={t("groups.amTeamPlaceholder")} />
          </Form.Item>
          <Form.Item name="llm_base_url" label={t("groups.llmBaseUrl")}>
            <Input placeholder="https://…/v1" />
          </Form.Item>
          <Form.Item name="llm_api_key" label={t("groups.llmApiKey")}>
            <Input.Password placeholder={editing?.has_llm_key ? t("groups.configuredKeep") : ""} />
          </Form.Item>
          <Form.Item name="llm_model" label={t("groups.llmModel")}>
            <Input />
          </Form.Item>
          <Form.Item name="llm_effort" label={t("groups.llmEffort")} tooltip={t("groups.llmEffortTooltip")}>
            <Select
              showSearch
              optionFilterProp="label"
              options={[
                { value: "", label: t("groups.effortUnset") },
                { value: "low", label: t("people.effortLow") },
                { value: "medium", label: t("people.effortMedium") },
                { value: "high", label: t("people.effortHigh") },
                { value: "xhigh", label: t("people.effortXhigh") },
                { value: "max", label: t("people.effortMax") },
              ]}
            />
          </Form.Item>
          <Form.Item name="scan_enabled" label={t("groups.scanEnabled")} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="auto_webhook" label={t("groups.autoWebhook")} valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={t("groups.membersDrawer", { name: mgroup?.name || "" })}
        width={1100}
        open={!!mgroup}
        onClose={() => {
          setMgroup(null);
          setMkeyword(""); // 换了组，上一组的检索词不该带过去
        }}
      >
        <Space style={{ marginBottom: 12 }}>
          <Button onClick={runMatch} disabled={!isAdmin()}>
            {t("groups.runMatch")}
          </Button>
          <Button onClick={() => setAddOpen(true)} disabled={!isAdmin()}>
            {t("groups.addMember")}
          </Button>
          <Button onClick={refreshGlUsers} disabled={!isAdmin()} loading={glBusy}>
            {t("groups.refreshGl")}
          </Button>
        {octoErr ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="warning"
            showIcon
            message={t("groups.octoErr", { error: octoErr })}
          />
        ) : (
          <Typography.Text type="secondary">{t("groups.octoMembersCount", { n: octoList.length })}</Typography.Text>
        )}        </Space>
        <Alert
          style={{ marginBottom: 12 }}
          type="info"
          showIcon
          message={t("groups.matchIntro")}
        />
        <Input.Search
          style={{ width: 280, marginBottom: 12 }}
          allowClear
          placeholder={t("groups.memberPlaceholder")}
          onSearch={(v) => setMkeyword(v.trim())}
          onChange={(e) => !e.target.value && setMkeyword("")}
        />
        <ResizableTable<Member>
          rowKey="id"
          // 已绑 octo 的排前面：抽屉一打开先看到有身份的，未绑的堆在后面待处理
          dataSource={memShown}
          pagination={PAGE}
          size="small"
          tableLayout="fixed"
          columns={[
            { title: t("groups.colMember"), dataIndex: "person_name", width: 160, ellipsis: true },
            {
              title: t("groups.colGitlabAccount"),
              width: 190,
              render: (_, m) => (
                <Select
                  size="small"
                  style={{ width: "100%" }}
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  placeholder={t("groups.selectBind")}
                  popupMatchSelectWidth={320}
                  optionRender={(o) => optionTip(String(o.label ?? ""))}
                  labelRender={({ label, value }) => optionTip(String(label ?? value ?? ""))}
                  value={m.gitlab_account_id || undefined}
                  onClear={() => bindAccount(m, null)}
                  onChange={(id) => bindAccount(m, id)}
                  notFoundContent={t("groups.noGlAccounts")}
                  options={(m.gitlab_accounts || []).map((a) => ({
                    value: a.id,
                    label: a.has_token ? a.username : `${a.username}${t("groups.noToken")}`,
                  }))}
                  disabled={!isAdmin() || !(m.gitlab_accounts || []).length}
                />
              ),
            },
            {
              // 绑定与调整合成一列：下拉显示当前绑定的群成员，改动即人工调整
              title: t("groups.colOctoBind"),
              width: 230,
              render: (_, m) => (
                <Select
                  size="small"
                  style={{ width: "100%" }}
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  placeholder={t("groups.selectOcto")}
                  popupMatchSelectWidth={320}
                  optionRender={(o) => optionTip(String(o.label ?? ""))}
                  labelRender={({ label, value }) => optionTip(String(label ?? value ?? ""))}
                  // pending 只是 AI 建议、并未绑定，不显示成已选
                  value={(m.match_status === "pending" ? undefined : m.octo_uid) || undefined}
                  options={octoList.map((o) => ({ value: o.uid, label: `${o.nickname}（${o.uid}）` }))}
                  onChange={(uid) => manualBind(m, uid)}
                  onClear={() => manualBind(m, null)}
                  disabled={!isAdmin()}
                />
              ),
            },
            {
              title: t("groups.colMatchStatus"),
              width: 110,
              render: (_, m) => {
                const pair = STATUS_TAG[m.match_status];
                return (
                  <Tag color={pair?.[0] ?? "default"}>{pair ? t(pair[1]) : m.match_status}</Tag>
                );
              },
            },
            { title: t("groups.colReason"), dataIndex: "match_reason", width: 160, ellipsis: true, render: (v) => v || "—" },
            {
              title: t("common.actions"),
              width: 190,
              render: (_, m) => (
                <Space size={12}>
                  {m.gitlab_account_id && <a onClick={() => bindAccount(m, null)}>{t("groups.unbindGitlab")}</a>}
                  {m.octo_uid && <a onClick={() => manualBind(m, null)}>{t("groups.unbindOcto")}</a>}
                  <a
                    style={{ color: "#cf1322" }}
                    onClick={async () => {
                      await api(`/api/groups/${mgroup!.id}/members/${m.person_id}`, { method: "DELETE" });
                      setMembers(await api<Member[]>(`/api/groups/${mgroup!.id}/members`));
                    }}
                  >
                    {t("groups.remove")}
                  </a>
                </Space>
              ),
            },
          ]}
        />
        <Modal
          open={addOpen}
          title={t("groups.addMember")}
          onCancel={() => setAddOpen(false)}
          onOk={async () => {
            if (!addPid) return;
            try {
              await api(`/api/groups/${mgroup!.id}/members`, {
                method: "POST",
                body: JSON.stringify({ person_id: addPid }),
              });
              setAddOpen(false);
              setMembers(await api<Member[]>(`/api/groups/${mgroup!.id}/members`));
            } catch (e: any) {
              message.error(e.message);
            }
          }}
          destroyOnHidden
        >
          <Select
            style={{ width: "100%" }}
            placeholder={t("groups.selectPerson")}
            showSearch
            optionFilterProp="label"
            value={addPid}
            onChange={setAddPid}
            options={people.map((p) => ({
              value: p.id,
              label: t("groups.personLabel", { name: p.name, username: p.username || p.gitlab_username || "" }),
            }))}
          />
        </Modal>
      </Drawer>
    </Page>
  );
}
