import { useEffect, useRef, useState } from "react";
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
import ResizableTable from "../ResizableTable";
import { api, isAdmin } from "../api";
import AmSelect from "../components/AmSelect";
import Page from "../Page";
import { PAGE } from "../table";

interface Group {
  id: number;
  name: string;
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
const STATUS_TAG: Record<string, [string, string]> = {
  auto_exact: ["green", "系统匹配"],
  auto_ai: ["green", "系统匹配"],
  auto: ["green", "系统匹配"],
  manual: ["purple", "人工调整"],
  pending: ["default", "未匹配"],
  unmatched: ["default", "未匹配"],
};

const empty: any = {
  name: "",
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
  const nav = useNavigate();
  const [rows, setRows] = useState<Group[]>([]);
  const [editing, setEditing] = useState<any>(null);
  const [form] = Form.useForm();
  // 成员抽屉
  const [mgroup, setMgroup] = useState<Group | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [octoList, setOctoList] = useState<{ uid: string; nickname: string }[]>([]);
  const [octoErr, setOctoErr] = useState<string | null>(null);
  const [people, setPeople] = useState<any[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [addPid, setAddPid] = useState<number | null>(null);
  // 群号下拉：本组 bot 所在的群
  const [octoGroups, setOctoGroups] = useState<{ group_no: string; name: string }[]>([]);
  const [octoGroupsErr, setOctoGroupsErr] = useState<string | null>(null);
  // GitLab 账号绑定下拉：读本组成员快照（秒开）；「刷新 GitLab 名单」才重拉全部项目
  const [glUsers, setGlUsers] = useState<{ username: string; name: string; email: string }[]>([]);
  const [glErr, setGlErr] = useState<string | null>(null);
  const [glBusy, setGlBusy] = useState(false);
  const glLoaded = useRef(false);

  // 表格里显示中文名而不是 id：octo 群名按组用各组 bot 拉，am 团队名取当前用户的团队列表
  const [octoNames, setOctoNames] = useState<Record<string, string>>({});
  const [teamNames, setTeamNames] = useState<Record<string, string>>({});

  const loadNames = async (list: Group[]) => {
    api<any[]>("/api/am/teams")
      .then((teams) => setTeamNames(Object.fromEntries(teams.map((t) => [t.id, t.name]))))
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
      message.success("已保存（配置变化已在后台自动触发扫描/同步）");
      setEditing(null);
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const testBot = async (g: Group) => {
    try {
      const d = await api<any>(`/api/groups/${g.id}/octo-test`, { method: "POST" });
      if (d.ok) message.success(`测试消息已送达${d.dedicated ? "（本组独立 bot）" : "（全局 bot）"}，请到群里确认`);
      else message.error("发送失败：octo 未配置或 bot token 无效（已留痕，可在集成中心查看）");
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const runScan = async (g: Group) => {
    try {
      const d = await api<any>(`/api/groups/${g.id}/scan`, { method: "POST" });
      message.success(`扫描完成：新增项目 ${d.projects_added}，补配 webhook ${d.webhooks_added}${d.error ? `（${d.error}）` : ""}`);
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

  const loadGlUsers = async () => {
    if (glLoaded.current) return;
    glLoaded.current = true;
    try {
      const d = await api<{ items: { username: string; name: string; email: string }[]; error: string | null }>(
        `/api/groups/${mgroup?.id}/gitlab-users`,
      );
      setGlUsers(d.items || []);
      setGlErr(d.error || null);
    } catch (e: any) {
      setGlErr(e.message);
    }
  };

  // 重拉本组全部项目成员并覆盖快照；只刷名单，不跑 AI 对配
  const refreshGlUsers = async () => {
    if (!mgroup) return;
    setGlBusy(true);
    try {
      const d = await api<{ items: { username: string; name: string; email: string }[] }>(
        `/api/groups/${mgroup.id}/gitlab-users/refresh`,
        { method: "POST" },
      );
      glLoaded.current = true;
      setGlUsers(d.items || []);
      setGlErr(null);
      message.success(`GitLab 名单已刷新：${d.items?.length ?? 0} 人`);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setGlBusy(false);
    }
  };

  const bindGitlab = async (m: Member, username: string | null) => {
    if (!mgroup) return;
    try {
      const d = await api<any>(`/api/groups/${mgroup.id}/gitlab-match/${m.person_id}`, {
        method: "PUT",
        body: JSON.stringify({ gitlab_username: username }),
      });
      message.success(
        username
          ? `已绑定 GitLab 账号 ${username}${d.released ? `，原绑定「${d.released}」已自动解绑` : ""}`
          : "已解绑 GitLab 账号",
      );
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const openMembers = async (g: Group) => {
    setMgroup(g);
    glLoaded.current = false;
    setGlUsers([]);
    setGlErr(null);
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
        `对配完成：GitLab 建档 ${s.gitlab_created ?? 0} · 同人合并 ${s.gitlab_merged ?? 0} · octo 建档 ${s.octo_created ?? 0} · 规则 ${s.auto_exact ?? 0} / AI ${s.auto_ai ?? 0} / 待确认 ${s.pending ?? 0} / 保留人工 ${s.kept_manual ?? 0}${s.error ? `（${s.error}）` : ""}`,
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
        uid ? `已绑定群成员${d.released ? `，原绑定「${d.released}」已自动解绑` : ""}` : "已解绑群成员",
      );
      setMembers(await api<Member[]>(`/api/groups/${mgroup.id}/members`));
    } catch (e: any) {
      message.error(e.message);
    }
  };

  return (
    <Page
      title="研发组"
      desc="GitLab 绑定 · octo 群对配 · 组 LLM 兜底 · 定时扫描"
      actions={
        isAdmin() && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => open()}>
            新建研发组
          </Button>
        )
      }
    >
      <div className="page-card">
        <ResizableTable<Group>
          rowKey="id"
          dataSource={rows}
          pagination={PAGE}
          columns={[
            { title: "研发组", dataIndex: "name" },
            {
              title: "GitLab",
              dataIndex: "gitlab_base_url",
              render: (v, r) => (
                <>
                  <span className="mono">{v}</span>{" "}
                  {r.has_gitlab_token ? <Tag color="green">token ✓</Tag> : <Tag color="red">无 token</Tag>}
                </>
              ),
            },
            {
              title: "octo 群",
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
                      <Tooltip title="本组用自己的 bot token 发群通知（本页「编辑」里配置）">
                        <Tag color="green">独立 bot</Tag>
                      </Tooltip>
                    ) : (
                      <Tooltip title="未配置本组 token，用全局 bot 发通知；可在本页「编辑」里填本组 token">
                        <Tag>全局 bot</Tag>
                      </Tooltip>
                    ))}
                </>
              ),
            },
            {
              title: "am team",
              dataIndex: "am_team_id",
              render: (v) =>
                v ? teamNames[v] ? <span title={v}>{teamNames[v]}</span> : <span className="mono">{v}</span> : "—",
            },
            { title: "组 LLM", render: (_, r) => (r.has_llm_key ? r.llm_model || "默认" : "—") },
            {
              title: "扫描",
              dataIndex: "scan_enabled",
              render: (v) => (v ? <Tag color="green">开</Tag> : <Tag>关</Tag>),
            },
            {
              title: "操作",
              width: 300,
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  <a onClick={() => openMembers(r)}>成员对配</a>
                  <a onClick={() => open(r)}>编辑</a>
                  <a onClick={() => runScan(r)}>扫描项目</a>
                  <a onClick={() => testBot(r)}>测试 bot</a>
                  {isAdmin() && (
                    <Popconfirm
                      title="删除该研发组？"
                      onConfirm={async () => {
                        try {
                          await api(`/api/groups/${r.id}`, { method: "DELETE" });
                          load();
                        } catch (e: any) {
                          message.error(e.message);
                        }
                      }}
                    >
                      <a className="danger-link">删除</a>
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
        title={editing?.id ? "编辑研发组" : "新建研发组"}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || empty} onFinish={save}>
          <Form.Item name="name" label="组名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="gitlab_base_url" label="GitLab 地址">
            <Input placeholder="https://git.example.com" />
          </Form.Item>
          <Form.Item name="gitlab_token" label="GitLab 管理员 Token（只写不回显）">
            <Input.Password placeholder={editing?.has_gitlab_token ? "已配置，留空不改" : ""} />
          </Form.Item>
          <Form.Item
            name="octo_group_no"
            label="octo 群（从 bot 所在群中选择）"
            tooltip="群号是 32 位 ID 不是群名；列表来自 bot token（本组未单独填则用集成中心配的全局 bot），展开时实时拉取"
          >
            <Select
              showSearch
              allowClear
              optionFilterProp="label"
              placeholder="展开选择群"
              onOpenChange={(o) => o && loadOctoGroups()}
              notFoundContent={octoGroupsErr || "该 bot 不在任何群里"}
              options={octoGroups.map((g) => ({
                value: g.group_no,
                label: `${g.name}（${g.group_no.slice(0, 8)}…）`,
              }))}
            />
          </Form.Item>
          <Form.Item name="octo_bot_token" label="本组 octo bot Token（可选，只写不回显）">
            <Input.Password placeholder={editing?.has_octo_bot_token ? "已配置，留空不改" : "留空即用集成中心配的全局 bot token"} />
          </Form.Item>
          <Form.Item
            name="tech_lead_person_ids"
            label="技术负责人（代码日报 @）"
            tooltip="代码日报/周报/月报在群里 @ 他们；项目未配技术负责人时也兜底走他们"
          >
            <Select
              mode="multiple"
              allowClear
              optionFilterProp="label"
              placeholder="可多选"
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="am_team_id" label="agent-memory 团队（展开实时拉取）">
            <AmSelect kind="team" placeholder="选择团队，留空则不绑定" />
          </Form.Item>
          <Form.Item name="llm_base_url" label="组 LLM Base URL（成员无个人 LLM 时兜底）">
            <Input placeholder="https://…/v1" />
          </Form.Item>
          <Form.Item name="llm_api_key" label="组 LLM API Key（只写不回显）">
            <Input.Password placeholder={editing?.has_llm_key ? "已配置，留空不改" : ""} />
          </Form.Item>
          <Form.Item name="llm_model" label="组 LLM Model">
            <Input />
          </Form.Item>
          <Form.Item name="llm_effort" label="组思考等级" tooltip="透传 claude --effort；默认 Qwen 上游只认 xhigh/medium/low，high/max 适合 claude 系或自定义上游">
            <Select
              options={[
                { value: "", label: "未设置（兜底 xhigh）" },
                { value: "low", label: "低（low）" },
                { value: "medium", label: "中（medium）" },
                { value: "high", label: "高（high · claude 默认）" },
                { value: "xhigh", label: "特高（xhigh · Qwen 最高档）" },
                { value: "max", label: "最高（max）" },
              ]}
            />
          </Form.Item>
          <Form.Item name="scan_enabled" label="定时扫描项目" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="auto_webhook" label="自动补配 webhook" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={`成员与对配 · ${mgroup?.name || ""}`}
        width={1100}
        open={!!mgroup}
        onClose={() => setMgroup(null)}
      >
        <Space style={{ marginBottom: 12 }}>
          <Button onClick={runMatch} disabled={!isAdmin()}>
            运行自动对配（规则 + AI）
          </Button>
          <Button onClick={() => setAddOpen(true)} disabled={!isAdmin()}>
            添加成员
          </Button>
          <Button onClick={refreshGlUsers} disabled={!isAdmin()} loading={glBusy}>
            刷新 GitLab 名单
          </Button>
        {octoErr ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="warning"
            showIcon
            message={`群成员拉不到：${octoErr}`}
          />
        ) : (
          <Typography.Text type="secondary">octo 群成员 {octoList.length} 人</Typography.Text>
        )}        </Space>
        <Alert
          style={{ marginBottom: 12 }}
          type="info"
          showIcon
          message="三方对配：Vega 用户 ↔ GitLab 账号 ↔ octo 群成员。运行自动对配会先从本组 GitLab 项目拉成员自动建档，再按 规则精确 → AI（置信度 ≥ 0.8 生效）→ 人工调整（锁定后永不被覆盖）绑定 octo 身份"
        />
        <ResizableTable<Member>
          rowKey="id"
          // 已绑 octo 的排前面：抽屉一打开先看到有身份的，未绑的堆在后面待处理
          dataSource={[...members].sort((a, b) => Number(!!b.octo_uid) - Number(!!a.octo_uid))}
          pagination={PAGE}
          size="small"
          tableLayout="fixed"
          columns={[
            { title: "成员", dataIndex: "person_name", width: 160, ellipsis: true },
            {
              title: "GitLab 账号",
              width: 190,
              render: (_, m) => (
                <Select
                  size="small"
                  style={{ width: "100%" }}
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  placeholder="选择/搜索绑定"
                  popupMatchSelectWidth={320}
                  optionRender={(o) => optionTip(String(o.label ?? ""))}
                  labelRender={({ label, value }) => optionTip(String(label ?? value ?? ""))}
                  value={m.gitlab_username || undefined}
                  onOpenChange={(o) => o && loadGlUsers()}
                  onClear={() => bindGitlab(m, null)}
                  onChange={(u) => bindGitlab(m, u)}
                  notFoundContent={glErr || "暂无 GitLab 成员"}
                  options={glUsers.map((u) => ({
                    value: u.username,
                    label: u.name ? `${u.username} · ${u.name}` : u.username,
                  }))}
                  disabled={!isAdmin()}
                />
              ),
            },
            {
              // 绑定与调整合成一列：下拉显示当前绑定的群成员，改动即人工调整
              title: "octo 绑定",
              width: 230,
              render: (_, m) => (
                <Select
                  size="small"
                  style={{ width: "100%" }}
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  placeholder="选择/搜索群成员"
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
              title: "对配状态",
              width: 110,
              render: (_, m) => {
                const t = STATUS_TAG[m.match_status] || ["default", m.match_status];
                return <Tag color={t[0]}>{t[1]}</Tag>;
              },
            },
            { title: "依据", dataIndex: "match_reason", width: 160, ellipsis: true, render: (v) => v || "—" },
            {
              title: "操作",
              width: 190,
              render: (_, m) => (
                <Space size={12}>
                  {m.gitlab_username && <a onClick={() => bindGitlab(m, null)}>解绑 GitLab</a>}
                  {m.octo_uid && <a onClick={() => manualBind(m, null)}>解绑 octo</a>}
                  <a
                    style={{ color: "#cf1322" }}
                    onClick={async () => {
                      await api(`/api/groups/${mgroup!.id}/members/${m.person_id}`, { method: "DELETE" });
                      setMembers(await api<Member[]>(`/api/groups/${mgroup!.id}/members`));
                    }}
                  >
                    移除
                  </a>
                </Space>
              ),
            },
          ]}
        />
        <Modal
          open={addOpen}
          title="添加成员"
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
            placeholder="选择人员"
            showSearch
            optionFilterProp="label"
            value={addPid}
            onChange={setAddPid}
            options={people.map((p) => ({ value: p.id, label: `${p.name}（${p.gitlab_username || ""}）` }))}
          />
        </Modal>
      </Drawer>
    </Page>
  );
}
