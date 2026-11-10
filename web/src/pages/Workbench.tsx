import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { useGroup } from "../group";
import Page from "../Page";
import SearchSelect from "../ui/SearchSelect";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/motion/tabs";
import CodeStats from "./CodeStats";
import WorkbenchFlow, { flowCls, type Req } from "./WorkbenchFlow";

/**
 * 工作台：需求 → 任务 → MR → CI → 合入 的实时流程图。
 * 流程图由 WorkbenchFlow 用 G6 渲染：每个需求一行，任务卡并进当前阶段节点，
 * 连线随状态流动；本页只负责数据轮询、项目筛选、统计与标签页。
 * 只显示实时工作的需求：所有任务到终态（合并 / 失败 / MR 关闭）即退场；
 * 全部合入的需求状态会被置为「已上线」，去需求池里看。 */

export default function Workbench() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { groupId } = useGroup();
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<number | undefined>();
  const [reqs, setReqs] = useState<Req[]>([]);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});

  const active = reqs;

  const load = async () => {
    const p = new URLSearchParams();
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("system_id", String(projectId));
    const d = await api<{ requirements: Req[] }>(`/api/workbench?${p}`);
    setReqs(d.requirements);
  };
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [groupId, projectId]);
  useEffect(() => {
    api<any[]>("/api/systems").then(setProjects);
  }, []);
  useEffect(() => {
    setProjectId(undefined); // 切组后旧项目筛选可能不属于该组
  }, [groupId]);

  // 专家卡片显示 agent-memory 里的专家名：按项目团队拉一次；拉不到就回落到 id
  useEffect(() => {
    const teams = [...new Set(projects.map((p) => p.am_team_id).filter(Boolean))] as string[];
    teams.forEach((teamId) => {
      api<any[]>(`/api/am/agents?team_id=${encodeURIComponent(teamId)}`)
        .then((list) =>
          setAgentNames((prev) => ({ ...prev, ...Object.fromEntries(list.map((a: any) => [a.id, a.name])) }))
        )
        .catch(() => {});
    });
  }, [projects]);

  const projOptions = projects
    .filter((p) => !groupId || p.group_id === groupId)
    .map((p) => ({ value: p.id, label: `${p.path}（${p.name}）` }));

  const liveTasks = active.reduce((n, r) => n + r.tasks.filter((task) => flowCls(task) === "live").length, 0);
  const reviewMrs = active.reduce(
    (n, r) =>
      n +
      r.tasks.filter(
        (task) => task.mr_iid && !task.outcome && ["mr_created", "reviewing", "cicd"].includes(task.status),
      ).length,
    0
  );
  const stats: [string, number, string][] = [
    [t("workbench.statActive"), active.length, ""],
    [t("workbench.statRunning"), liveTasks, "live"],
    [t("workbench.statReview"), reviewMrs, "wait"],
  ];

  return (
    <Page title={t("nav.workbench")} desc={t("workbench.desc")}>
      <div className="wb-bar">
        <SearchSelect
          value={projectId ?? null}
          onChange={(v) => setProjectId(v == null ? undefined : Number(v))}
          clearable
          placeholder={t("workbench.projectPlaceholder")}
          options={projOptions}
          className="w-[280px]"
        />
      </div>
      <Tabs defaultValue="flow" variant="segment">
        <TabsList className="mb-3">
          <TabsTrigger value="flow">{t("workbench.tabFlow")}</TabsTrigger>
          <TabsTrigger value="stats">{t("workbench.tabStats")}</TabsTrigger>
        </TabsList>
        <TabsContent value="flow">
        <div className="wb-bar">
        <div className="wb-legend">
          <span><i className="wb-dot live" /> {t("workbench.legendLive")}</span>
          <span><i className="wb-dot ok" /> {t("workbench.legendOk")}</span>
          <span><i className="wb-dot bad" /> {t("workbench.legendBad")}</span>
          <span><i className="wb-dot idle" /> {t("workbench.legendIdle")}</span>
        </div>
      </div>

      <div className="wb-stats">
        {stats.map(([label, n, tone]) => (
          <div className={`wb-stat ${tone}`} key={label}>
            <b>{n}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className="page-card wb-flow">
        <span className="wb-live-chip">
          <i /> {t("workbench.live")}
        </span>
        {active.length === 0 ? (
          <div className="py-12 text-center text-sm text-muted-foreground">
            {t("workbench.empty")}
          </div>
        ) : (
          <WorkbenchFlow
            reqs={active}
            projects={projects}
            agentNames={agentNames}
            onOpenReq={(r) => nav(`/reqs/${r.id}`)}
            onOpenTask={(task) => nav(`/tasks/${task.id}`)}
          />
        )}
      </div>

              </TabsContent>
        <TabsContent value="stats">
          <CodeStats groupId={groupId ?? undefined} projectId={projectId} />
        </TabsContent>
      </Tabs>
    </Page>
  );
}
