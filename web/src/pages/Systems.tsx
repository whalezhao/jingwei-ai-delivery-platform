import { useEffect, useState } from "react";
import { Button, Checkbox, Divider, Drawer, Empty, Form, Input, Modal, Popconfirm, Select, Space, Tag, message } from "antd";
import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import ResizableTable from "../ResizableTable";
import { PlusOutlined } from "@ant-design/icons";
import { api, isAdmin } from "../api";
import type { MsgKey } from "../i18n";
import { useGroup } from "../group";
import AmSelect from "../components/AmSelect";
import Page from "../Page";
import { PAGE } from "../table";

const EGM_ST: Record<string, { color: string; text: MsgKey }> = {
  running: { color: "processing", text: "systems.egm.running" },
  done: { color: "success", text: "systems.egm.done" },
  failed: { color: "error", text: "systems.egm.failed" },
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

export default function Systems() {
  const { t } = useTranslation();
  const { groupId } = useGroup();
  const [rows, setRows] = useState<System[]>([]);
  const [groups, setGroups] = useState<any[]>([]);
  const [people, setPeople] = useState<any[]>([]);
  const [editing, setEditing] = useState<any>(null);
  const [keyword, setKeyword] = useState("");
  // EGM 项目级任务：bootstrap 生成 README 树，drift 出设计-代码漂移报告
  const [egmFor, setEgmFor] = useState<System | null>(null);
  const [egmReports, setEgmReports] = useState<any[]>([]);
  const [egmBusy, setEgmBusy] = useState(false);
  const [form] = Form.useForm();
  // 专家列表随团队：项目团队 → 未选回退研发组团队
  const watchTeam: string | undefined = Form.useWatch("am_team_id", form);
  const effTeam =
    watchTeam || groups.find((g) => g.id === editing?.group_id)?.am_team_id || undefined;

  const load = async () => {
    setRows(await api<System[]>(`/api/systems${groupId ? `?group_id=${groupId}` : ""}`));
    setGroups(await api<any[]>("/api/groups"));
    setPeople(await api<any[]>("/api/people"));
  };
  useEffect(() => {
    load();
  }, [groupId]);

  const open = (p?: System) => {
    setEditing(
      p
        ? {
            ...p,
            audit_prefixes: prefixToView(p.audit_prefixes),
            auto_approve_prefixes: prefixToView(p.auto_approve_prefixes),
          }
        : {
            group_id: groupId ?? groups[0]?.id,
            gitlab_project_id: "",
            path: "",
            name: "",
            code_prefix: "",
            default_branch: "master",
            mr_target_branch: "nightly",
            tech_lead_person_ids: [],
            owner_person_ids: [],
            audit_prefixes: "",
            auto_approve_prefixes: "",
            am_team_id: "",
            am_agent_id: "",
            run_egm_bootstrap: false,
          },
    );
    form.resetFields();
  };

  const save = async (v: any) => {
    const body = {
      ...v,
      audit_prefixes: prefixToDb(v.audit_prefixes),
      auto_approve_prefixes: prefixToDb(v.auto_approve_prefixes),
    };
    try {
      if (editing.id) await api(`/api/systems/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/systems", { method: "POST", body: JSON.stringify(body) });
      message.success(
        !editing.id && v.run_egm_bootstrap ? t("systems.savedEgm") : t("common.saved"),
      );
      setEditing(null);
      load();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const openEgm = async (p: System) => {
    setEgmFor(p);
    setEgmReports(await api<any[]>(`/api/systems/${p.id}/egm-reports`).catch(() => []));
  };

  const runEgm = async (p: System, kind: "bootstrap" | "drift") => {
    setEgmBusy(true);
    try {
      await api(`/api/systems/${p.id}/egm-jobs`, { method: "POST", body: JSON.stringify({ kind }) });
      message.success(t(kind === "bootstrap" ? "systems.submittedBootstrap" : "systems.submittedDrift"));
      await openEgm(p);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setEgmBusy(false);
    }
  };

  const gname = (gid: number) => groups.find((g) => g.id === gid)?.name || gid;
  const pname = (pid: number) => people.find((p) => p.id === pid)?.name || pid;
  const term = keyword.trim().toLowerCase();
  const shown = term
    ? rows.filter((r) =>
        [r.path, r.name, r.code_prefix, r.default_branch, r.mr_target_branch, gname(r.group_id)]
          .some((s) => String(s ?? "").toLowerCase().includes(term)),
      )
    : rows;

  return (
    <Page
      title={t("nav.systems")}
      desc={t("systems.desc")}
      actions={
        isAdmin() && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => open()}>
            {t("systems.new")}
          </Button>
        )
      }
    >
      <div className="page-card">
        <Input.Search
          style={{ width: 280, marginBottom: 12 }}
          allowClear
          placeholder={t("systems.searchPlaceholder")}
          onSearch={(v) => setKeyword(v)}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
        <ResizableTable<System>
          rowKey="id"
          dataSource={shown}
          pagination={PAGE}
          columns={[
            {
              title: t("systems.colSystem"),
              dataIndex: "path",
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: t("systems.colGroup"), render: (_, r) => gname(r.group_id) },
            {
              title: "GitLab ID",
              dataIndex: "gitlab_project_id",
              render: (v) => <span className="mono">{v}</span>,
            },
            { title: t("systems.colPrefix"), dataIndex: "code_prefix", render: (v) => <Tag color="blue">{v}</Tag> },
            { title: t("systems.colDefaultBranch"), dataIndex: "default_branch", render: (v) => <span className="mono">{v}</span> },
            {
              title: t("systems.colMrTarget"),
              dataIndex: "mr_target_branch",
              render: (v) => <span className="mono">{v}</span>,
            },
            {
              title: t("systems.colTechLead"),
              render: (_, r) =>
                r.tech_lead_person_ids.length
                  ? r.tech_lead_person_ids.map(pname).join(t("systems.listSep"))
                  : "—",
            },
            {
              title: t("systems.colOwner"),
              render: (_, r) =>
                r.owner_person_ids.length
                  ? r.owner_person_ids.map(pname).join(t("systems.listSep"))
                  : "—",
            },
            {
              title: t("systems.colPrefixes"),
              render: (_, r) => (
                <>
                  {r.audit_prefixes?.length ? (
                    <Tag color="geekblue">
                      {r.audit_prefixes.includes("")
                        ? t("systems.auditAll")
                        : t("systems.auditCount", { n: r.audit_prefixes.length })}
                    </Tag>
                  ) : null}
                  {r.auto_approve_prefixes?.length ? (
                    <Tag color="green">
                      {r.auto_approve_prefixes.includes("")
                        ? t("systems.releaseAll")
                        : t("systems.releaseCount", { n: r.auto_approve_prefixes.length })}
                    </Tag>
                  ) : null}
                  {!r.audit_prefixes?.length && !r.auto_approve_prefixes?.length ? "—" : null}
                </>
              ),
            },
            {
              title: t("common.actions"),
              render: (_, r) => (
                <Space size={0} split={<Divider type="vertical" />} wrap>
                  <a onClick={() => open(r)}>{t("common.edit")}</a>
                  <a onClick={() => openEgm(r)}>{t("systems.egmReports")}</a>
                  <a onClick={() => !egmBusy && runEgm(r, "bootstrap")}>{t("systems.docTree")}</a>
                  <a onClick={() => !egmBusy && runEgm(r, "drift")}>{t("systems.driftCheck")}</a>
                  {isAdmin() && (
                    <Popconfirm
                      title={t("systems.deleteConfirm")}
                      onConfirm={async () => {
                        try {
                          await api(`/api/systems/${r.id}`, { method: "DELETE" });
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
        title={editing?.id ? t("systems.editTitle") : t("systems.createTitle")}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={editing || {}} onFinish={save}>
          <Form.Item name="group_id" label={t("systems.group")} rules={[{ required: true }]}>
            <Select
              disabled={!!editing?.id}
              showSearch
              optionFilterProp="label"
              options={groups.map((g) => ({ value: g.id, label: g.name }))}
            />
          </Form.Item>
          <Form.Item name="gitlab_project_id" label={t("systems.gitlabId")} rules={[{ required: true }]}>
            <Input disabled={!!editing?.id} />
          </Form.Item>
          <Form.Item name="path" label={t("systems.repoPath")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label={t("systems.systemName")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code_prefix" label={t("systems.codePrefix")} rules={[{ required: true }]}>
            <Input maxLength={12} placeholder={t("systems.codePrefixPlaceholder")} />
          </Form.Item>
          <Form.Item name="default_branch" label={t("systems.defaultBranch")}>
            <Input />
          </Form.Item>
          <Form.Item name="mr_target_branch" label={t("systems.mrTarget")} tooltip={t("systems.mrTargetTooltip")}>
            <Input />
          </Form.Item>
          {!editing?.id && (
            <Form.Item
              name="run_egm_bootstrap"
              valuePropName="checked"
              label={t("systems.runBootstrap")}
              tooltip={t("systems.runBootstrapTooltip")}
            >
              <Checkbox>{t("systems.bootstrapNow")}</Checkbox>
            </Form.Item>
          )}
          <Form.Item name="tech_lead_person_ids" label={t("systems.techLead")}>
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item name="owner_person_ids" label={t("systems.owners")}>
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={people.map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
          <Form.Item
            name="audit_prefixes"
            label={t("systems.auditPrefixes")}
            tooltip={t("systems.auditPrefixesTooltip")}
          >
            <Input placeholder={t("systems.auditPlaceholder")} />
          </Form.Item>
          <Form.Item
            name="auto_approve_prefixes"
            label={t("systems.autoApprove")}
            tooltip={t("systems.autoApproveTooltip")}
          >
            <Input placeholder="docs/; src/" />
          </Form.Item>
          <Form.Item
            name="am_team_id"
            label={t("systems.amTeam")}
            tooltip={t("systems.amTeamTooltip")}
          >
            <AmSelect kind="team" placeholder={t("systems.amTeamPlaceholder")} />
          </Form.Item>
          <Form.Item name="am_agent_id" label={t("systems.amAgent")} tooltip={t("systems.amAgentTooltip")}>
            <AmSelect kind="agent" teamId={effTeam} placeholder={t("systems.amAgentPlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>
      <Drawer
        title={`${t("systems.egmTitle")}${egmFor ? ` · ${egmFor.path}` : ""}`}
        width={760}
        open={!!egmFor}
        onClose={() => setEgmFor(null)}
        extra={
          egmFor && (
            <Button size="small" onClick={() => openEgm(egmFor)}>
              {t("systems.refresh")}
            </Button>
          )
        }
      >
        {egmReports.length === 0 ? (
          <Empty description={t("systems.noEgm")} />
        ) : (
          egmReports.map((r) => {
            const st = EGM_ST[r.status];
            return (
            <div key={r.id} style={{ marginBottom: 20 }}>
              <Space wrap>
                <Tag color={r.kind === "bootstrap" ? "geekblue" : "purple"}>
                  {r.kind === "bootstrap" ? t("systems.docTree") : t("systems.driftCheck")}
                </Tag>
                <Tag color={st?.color ?? "default"}>{st ? t(st.text) : r.status}</Tag>
                <span style={{ fontSize: 12, opacity: 0.6 }}>{r.created_at}</span>
                {r.branch && <span className="mono" style={{ fontSize: 12 }}>{r.branch}</span>}
                {r.mr_url && (
                  <a href={r.mr_url} target="_blank" rel="noreferrer">
                    MR
                  </a>
                )}
              </Space>
              {r.report_md ? (
                <div className="md-body" style={{ marginTop: 8 }}>
                  <Markdown>{r.report_md}</Markdown>
                </div>
              ) : (
                <div style={{ fontSize: 12, opacity: 0.6, marginTop: 8 }}>
                  {r.status === "running" ? t("systems.inProgress") : t("systems.noContent")}
                </div>
              )}
            </div>
          );
          })
        )}
      </Drawer>
    </Page>
  );
}
