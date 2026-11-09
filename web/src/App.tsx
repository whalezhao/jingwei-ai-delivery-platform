import { useEffect, useState, type CSSProperties } from "react";
import {
  HashRouter,
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  Boxes,
  ChevronDown,
  CirclePlay,
  Code2,
  FileText,
  FlaskConical,
  FolderKanban,
  LayoutDashboard,
  Laptop,
  LogOut,
  Network,
  PanelLeft,
  PlugZap,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { motion, useReducedMotion } from "motion/react";
import type { MsgKey } from "./i18n";
import { SPRING_LAYOUT } from "./lib/ease";
import Logo, { LogoMark } from "./components/Logo";
import LangSwitch from "./components/LangSwitch";
import ProfileModal, { Me } from "./components/ProfileModal";
import { avatarSrc } from "./components/PersonAvatar";
import { GroupProvider, useGroup } from "./group";
import { AUTH_EVENT, api, goLogin, pingAuth, token } from "./api";
import Login from "./pages/Login";
import People from "./pages/People";
import Groups from "./pages/Groups";
import Projects from "./pages/Projects";
import Systems from "./pages/Systems";
import Requirements from "./pages/Requirements";
import RequirementChat from "./pages/RequirementChat";
import Tasks from "./pages/Tasks";
import TaskDetail from "./pages/TaskDetail";
import Workbench from "./pages/Workbench";
import Nodes from "./pages/Nodes";
import Integrations from "./pages/Integrations";
import QaSystem from "./pages/QaSystem";
import QaFeature from "./pages/QaFeature";
import QaExecution from "./pages/QaExecution";
import QaTasks from "./pages/QaTasks";
import QaWorkers from "./pages/QaWorkers";
import {
  AnimatedSidebar,
  AnimatedSidebarContent,
  AnimatedSidebarFooter,
  AnimatedSidebarGroup,
  AnimatedSidebarGroupContent,
  AnimatedSidebarGroupLabel,
  AnimatedSidebarHeader,
  AnimatedSidebarInset,
  AnimatedSidebarMenu,
  AnimatedSidebarMenuButton,
  AnimatedSidebarMenuItem,
  AnimatedSidebarProvider,
  AnimatedSidebarRail,
  AnimatedSidebarTrigger,
  useAnimatedSidebar,
} from "./components/motion/animated-sidebar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "./components/motion/popover";
import FilterSelect from "./ui/FilterSelect";
import { Toaster } from "./ui/toast";
import { ConfirmHost } from "./ui/confirm";

type MenuItem = {
  path: string;
  key: MsgKey;
  icon: LucideIcon;
  group: "navGroup.delivery" | "navGroup.org" | "navGroup.ops";
  match: string[];
};

/** 菜单序：交付流（工作台→需求→任务→测试）→ 组织 → 运行。 */
const MENU: MenuItem[] = [
  {
    path: "/workbench",
    key: "nav.workbench",
    icon: LayoutDashboard,
    group: "navGroup.delivery",
    match: ["/workbench"],
  },
  {
    path: "/reqs",
    key: "nav.requirements",
    icon: FileText,
    group: "navGroup.delivery",
    match: ["/reqs"],
  },
  {
    path: "/tasks",
    key: "nav.tasks",
    icon: Code2,
    group: "navGroup.delivery",
    match: ["/tasks"],
  },
  {
    path: "/qa/tasks",
    key: "nav.qaTasks",
    icon: CirclePlay,
    group: "navGroup.delivery",
    match: ["/qa/tasks", "/qa/batches"],
  },
  {
    path: "/projects",
    key: "nav.projects",
    icon: FolderKanban,
    group: "navGroup.org",
    match: ["/projects"],
  },
  {
    path: "/groups",
    key: "nav.groups",
    icon: Network,
    group: "navGroup.org",
    match: ["/groups"],
  },
  {
    path: "/systems",
    key: "nav.systems",
    icon: Boxes,
    group: "navGroup.org",
    match: ["/systems"],
  },
  {
    path: "/people",
    key: "nav.people",
    icon: Users,
    group: "navGroup.org",
    match: ["/people"],
  },
  {
    path: "/nodes",
    key: "nav.nodes",
    icon: Laptop,
    group: "navGroup.ops",
    match: ["/nodes"],
  },
  {
    path: "/qa/workers",
    key: "nav.qaWorkers",
    icon: FlaskConical,
    group: "navGroup.ops",
    match: ["/qa/workers"],
  },
  {
    path: "/integrations",
    key: "nav.integrations",
    icon: PlugZap,
    group: "navGroup.ops",
    match: ["/integrations"],
  },
];

const GROUPS = [
  "navGroup.delivery",
  "navGroup.org",
  "navGroup.ops",
] as const;

function SidebarBrand() {
  const { state } = useAnimatedSidebar();
  return (
    <div className="flex h-9 items-center px-1">
      {state === "collapsed" ? <LogoMark size={28} /> : <Logo size={26} />}
    </div>
  );
}

function AvatarMenu({
  avatar,
  onProfile,
}: {
  avatar: string | null;
  onProfile: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const user = localStorage.getItem("vega_user") || t("app.account");
  const src = avatarSrc(avatar);

  const logout = () => {
    setOpen(false);
    token.clear();
    localStorage.removeItem("vega_admin");
    localStorage.removeItem("vega_user");
    pingAuth();
    goLogin();
  };

  return (
    <Popover open={open} onOpenChange={setOpen} align="end" sideOffset={10}>
      <PopoverTrigger>
        <button
          type="button"
          className="flex h-9 items-center gap-2 rounded-full border border-border bg-card pl-1 pr-2.5 text-sm transition-colors hover:border-(--color-border-strong)"
        >
          {src ? (
            <img
              src={src}
              alt=""
              className="size-7 rounded-full object-cover"
            />
          ) : (
            <span className="grid size-7 place-items-center rounded-full bg-muted text-xs font-medium text-foreground">
              {user.slice(0, 1)}
            </span>
          )}
          <span className="hidden max-w-24 truncate text-[13px] sm:block">
            {user}
          </span>
          <ChevronDown className="size-3.5 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-1.5">
        <div className="border-b border-border px-2.5 pb-2 pt-1.5">
          <div className="truncate text-sm font-medium text-foreground">
            {user}
          </div>
          <div className="text-[11px] text-muted-foreground">
            {t("app.name")}
          </div>
        </div>
        <div className="pt-1">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onProfile();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-foreground transition-colors hover:bg-muted"
          >
            <Settings className="size-4 text-muted-foreground" />
            {t("app.profile")}
          </button>
          <button
            type="button"
            onClick={logout}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive/5"
          >
            <LogOut className="size-4" />
            {t("app.logout")}
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Shell() {
  const loc = useLocation();
  const nav = useNavigate();
  const { t } = useTranslation();
  const { projects, projectId, setProjectId, groups, groupId, setGroupId } =
    useGroup();
  const reduce = useReducedMotion();
  const [hoveredNav, setHoveredNav] = useState<string | null>(null);
  const [myAvatar, setMyAvatar] = useState<string | null>(null);
  const [meRecord, setMeRecord] = useState<Me | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);

  // 顶栏头像跟随「我的」人员记录；导航或人员页改到自己时刷新（无需重新登录）
  useEffect(() => {
    const refreshMe = () =>
      api<any[]>("/api/people")
        .then((ps) => {
          const me = ps.find(
            (p) => String(p.id) === localStorage.getItem("vega_me"),
          );
          setMyAvatar(me?.avatar || null);
          setMeRecord((me as Me) || null);
        })
        .catch(() => {});
    refreshMe();
    window.addEventListener("vega-me-updated", refreshMe);
    return () => window.removeEventListener("vega-me-updated", refreshMe);
  }, [loc.pathname]);

  const activePath = (item: MenuItem) =>
    item.match.some(
      (path) => loc.pathname === path || loc.pathname.startsWith(`${path}/`),
    );

  return (
    <AnimatedSidebarProvider
      style={
        {
          "--sidebar-width": "236px",
          "--sidebar-width-icon": "68px",
        } as CSSProperties
      }
    >
      <AnimatedSidebar
        collapsible="icon"
        ariaLabel={t("app.name")}
        panelClassName="bg-[#FBFAF6]"
      >
        <AnimatedSidebarHeader className="px-3 pb-2 pt-4">
          <SidebarBrand />
        </AnimatedSidebarHeader>
        <AnimatedSidebarContent>
          {/* 织纹：一根经线串起所有菜单，每条菜单一根纬线，激活项金纬（对齐登录页） */}
          <div
            className="nav-weave"
            onMouseLeave={() => setHoveredNav(null)}
          >
          {GROUPS.map((groupKey) => (
            <AnimatedSidebarGroup key={groupKey}>
              <AnimatedSidebarGroupLabel>
                {t(groupKey)}
              </AnimatedSidebarGroupLabel>
              <AnimatedSidebarGroupContent>
                <AnimatedSidebarMenu>
                  {MENU.filter((item) => item.group === groupKey).map((item) => {
                    const Icon = item.icon;
                    const active = activePath(item);
                    return (
                      <AnimatedSidebarMenuItem
                        key={item.path}
                        onMouseEnter={() => setHoveredNav(item.path)}
                      >
                        {active && (
                          <motion.span
                            layoutId="nav-weave-tick"
                            transition={
                              reduce ? { duration: 0 } : SPRING_LAYOUT
                            }
                            className="nav-weave-tick"
                          />
                        )}
                        {!active && hoveredNav === item.path && (
                          <motion.span
                            layoutId="nav-weave-hover"
                            transition={
                              reduce ? { duration: 0 } : SPRING_LAYOUT
                            }
                            className="nav-hover-line"
                          />
                        )}
                        <AnimatedSidebarMenuButton
                          icon={
                            <Icon
                              className={
                                active
                                  ? "size-[18px] text-primary"
                                  : "size-[18px]"
                              }
                              strokeWidth={1.8}
                            />
                          }
                          isActive={active}
                          onSelect={() => nav(item.path)}
                        >
                          {t(item.key)}
                        </AnimatedSidebarMenuButton>
                      </AnimatedSidebarMenuItem>
                    );
                  })}
                </AnimatedSidebarMenu>
              </AnimatedSidebarGroupContent>
            </AnimatedSidebarGroup>
          ))}
          </div>
        </AnimatedSidebarContent>
        <AnimatedSidebarFooter className="px-4 py-3">
          <SidebarTagline />
        </AnimatedSidebarFooter>
        <AnimatedSidebarRail />
      </AnimatedSidebar>

      <AnimatedSidebarInset>
        <header className="sticky top-0 z-40 flex flex-wrap items-center gap-2 border-b border-border bg-background/85 px-3 py-2 backdrop-blur-xl sm:px-4 lg:h-14 lg:flex-nowrap lg:py-0">
          <AnimatedSidebarTrigger
            aria-label={t("app.name")}
            className="text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <PanelLeft className="size-[18px]" />
          </AnimatedSidebarTrigger>

          <div className="md:hidden">
            <Logo size={24} showCategory={false} />
          </div>

          <div className="ml-auto flex items-center gap-2 max-lg:order-last max-lg:w-full">
            <FilterSelect
              value={projectId}
              onChange={setProjectId}
              options={projects.map((p) => ({
                value: p.id,
                label: p.code ? `${p.name}（${p.code}）` : p.name,
              }))}
              placeholder={t("app.allProjects")}
              allLabel={t("app.allProjects")}
              className="flex-1 lg:w-[190px] lg:flex-none"
            />
            <FilterSelect
              value={groupId}
              onChange={setGroupId}
              options={groups
                .filter((g) => !projectId || g.project_id === projectId)
                .map((g) => ({ value: g.id, label: g.name }))}
              placeholder={t("app.allGroups")}
              allLabel={t("app.allGroups")}
              className="w-[170px] max-sm:hidden"
            />
          </div>

          <div className="ml-auto flex items-center gap-2 lg:ml-0">
            <LangSwitch />
            <AvatarMenu
              avatar={myAvatar}
              onProfile={() => setProfileOpen(true)}
            />
          </div>
        </header>

        <div className="flex-1">
          <Routes>
            <Route path="/" element={<Navigate to="/workbench" replace />} />
            <Route path="*" element={<Navigate to="/workbench" replace />} />
            <Route path="/workbench" element={<Workbench />} />
            <Route path="/reqs" element={<Requirements />} />
            <Route path="/reqs/:id" element={<RequirementChat />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/tasks/:id" element={<TaskDetail />} />
            <Route path="/qa/tasks" element={<QaTasks />} />
            <Route path="/people" element={<People />} />
            <Route path="/groups" element={<Groups />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/systems" element={<Systems />} />
            <Route path="/systems/:id/qa" element={<QaSystem />} />
            <Route
              path="/systems/:id/qa/features/:fid"
              element={<QaFeature />}
            />
            <Route path="/qa/batches/:bid" element={<QaExecution />} />
            <Route path="/qa/workers" element={<QaWorkers />} />
            <Route path="/nodes" element={<Nodes />} />
            <Route path="/integrations" element={<Integrations />} />
          </Routes>
        </div>
      </AnimatedSidebarInset>

      <ProfileModal
        open={profileOpen}
        me={meRecord}
        onClose={() => setProfileOpen(false)}
      />
      <Toaster />
      <ConfirmHost />
    </AnimatedSidebarProvider>
  );
}

function SidebarTagline() {
  const { t } = useTranslation();
  const { state } = useAnimatedSidebar();
  if (state === "collapsed") return null;
  return (
    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <span className="size-1.5 shrink-0 rounded-full bg-[#B8893B]" />
      <span className="truncate">{t("app.tagline")}</span>
    </div>
  );
}

/** 登录态守卫：token 变化时靠 AUTH_EVENT 触发重渲染（否则元素会停留在登录前的取值）。 */
function Guard() {
  const [authed, setAuthed] = useState(() => !!token.get());
  useEffect(() => {
    const h = () => setAuthed(!!token.get());
    window.addEventListener(AUTH_EVENT, h);
    return () => window.removeEventListener(AUTH_EVENT, h);
  }, []);
  return authed ? (
    <GroupProvider>
      <Shell />
    </GroupProvider>
  ) : (
    <Navigate to="/login" replace />
  );
}

export default function App() {
  const Router = (window as any).VEGA_HASH_ROUTER ? HashRouter : BrowserRouter;
  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<Guard />} />
      </Routes>
    </Router>
  );
}