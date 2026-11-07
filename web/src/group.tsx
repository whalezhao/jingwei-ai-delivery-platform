import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";

/** 顶栏全局检索：项目 + 研发组。null = 全部，持久化在 localStorage。 */
interface Project {
  id: number;
  name: string;
  code: string | null;
}
interface Group {
  id: number;
  name: string;
  project_id: number | null;
}
export interface ScopeSystem {
  id: number;
  group_id: number;
  name: string;
  path: string;
  code_prefix: string;
}
interface GroupCtx {
  projects: Project[];
  projectId: number | null;
  setProjectId: (id: number | null) => void;
  groups: Group[];
  groupId: number | null;
  setGroupId: (id: number | null) => void;
  systems: ScopeSystem[];
  refreshSystems: () => Promise<void>;
}

const KEY_PROJECT = "vega_project";
const KEY = "vega_group";

const readId = (key: string) => {
  const n = Number(localStorage.getItem(key));
  return Number.isFinite(n) && n > 0 ? n : null;
};

const Ctx = createContext<GroupCtx>({
  projects: [],
  projectId: null,
  setProjectId: () => {},
  groups: [],
  groupId: null,
  setGroupId: () => {},
  systems: [],
  refreshSystems: async () => {},
});

export function GroupProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [projectId, setProjectIdState] = useState<number | null>(() => readId(KEY_PROJECT));
  const [groups, setGroups] = useState<Group[]>([]);
  const [groupsLoaded, setGroupsLoaded] = useState(false);
  const [groupId, setGroupIdState] = useState<number | null>(() => readId(KEY));
  // Requirements 新建弹窗要按研发组选系统，列表页不再用系统维度筛选
  const [systems, setSystems] = useState<ScopeSystem[]>([]);

  const refreshSystems = useCallback(
    () => api<ScopeSystem[]>("/api/systems").then(setSystems),
    []
  );

  useEffect(() => {
    api<Project[]>("/api/projects").then((ps) => {
      setProjects(ps);
      setProjectsLoaded(true);
    });
    api<Group[]>("/api/groups").then((gs) => {
      setGroups(gs);
      setGroupsLoaded(true);
    });
    refreshSystems();
  }, [refreshSystems]);

  // 存过的项目被删了 → 回到全部
  useEffect(() => {
    if (!projectsLoaded) return;
    setProjectIdState((cur) => {
      if (cur && !projects.some((p) => p.id === cur)) {
        localStorage.removeItem(KEY_PROJECT);
        return null;
      }
      return cur;
    });
  }, [projects, projectsLoaded]);

  // 存过的组被删了，或已不属于当前项目 → 回到全部
  useEffect(() => {
    if (!groupsLoaded) return;
    const project = projects.find((p) => p.id === projectId);
    setGroupIdState((cur) => {
      const group = cur ? groups.find((g) => g.id === cur) : undefined;
      if (cur && (!group || (project && group.project_id !== project.id))) {
        localStorage.removeItem(KEY);
        return null;
      }
      return cur;
    });
  }, [groups, groupsLoaded, projects, projectId]);

  const value = useMemo<GroupCtx>(
    () => ({
      projects,
      projectId,
      setProjectId: (id) => {
        setProjectIdState(id);
        if (id) localStorage.setItem(KEY_PROJECT, String(id));
        else localStorage.removeItem(KEY_PROJECT);
      },
      groups,
      groupId,
      setGroupId: (id) => {
        setGroupIdState(id);
        if (id) localStorage.setItem(KEY, String(id));
        else localStorage.removeItem(KEY);
      },
      systems,
      refreshSystems,
    }),
    [projects, projectId, groups, groupId, systems, refreshSystems]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useGroup = () => useContext(Ctx);