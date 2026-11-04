import { useEffect, useMemo, useState } from "react";
import { HashRouter, BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  ApartmentOutlined,
  ApiOutlined,
  ClusterOutlined,
  CodeOutlined,
  DashboardOutlined,
  FileTextOutlined,
  LaptopOutlined,
  ProjectOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { ProLayout } from "@ant-design/pro-components";
import { Dropdown, Select } from "antd";
import { useTranslation } from "react-i18next";
import Logo from "./components/Logo";
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

/** 菜单序：工作台总览 → 业务流（需求→任务）→ 组织（项目→研发组→系统→人员）→ 节点/集成 */
const MENU = [
  { path: "/workbench", key: "nav.workbench", icon: <DashboardOutlined /> },
  { path: "/reqs", key: "nav.requirements", icon: <FileTextOutlined /> },
  { path: "/tasks", key: "nav.tasks", icon: <CodeOutlined /> },
  { path: "/projects", key: "nav.projects", icon: <ProjectOutlined /> },
  { path: "/groups", key: "nav.groups", icon: <ApartmentOutlined /> },
  { path: "/systems", key: "nav.systems", icon: <ClusterOutlined /> },
  { path: "/people", key: "nav.people", icon: <TeamOutlined /> },
  { path: "/nodes", key: "nav.nodes", icon: <LaptopOutlined /> },
  { path: "/integrations", key: "nav.integrations", icon: <ApiOutlined /> },
] as const;

function Shell() {
  const loc = useLocation();
  const { t } = useTranslation();
  const items = useMemo(() => MENU.map((m) => ({ path: m.path, name: t(m.key), icon: m.icon })), [t]);
  const { groups, groupId, setGroupId } = useGroup();
  const [myAvatar, setMyAvatar] = useState<string | null>(null);
  const [meRecord, setMeRecord] = useState<Me | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);

  // 顶栏头像跟随「我的」人员记录；导航或人员页改到自己时刷新（无需重新登录）
  useEffect(() => {
    const refreshMe = () =>
      api<any[]>("/api/people")
        .then((ps) => {
          const me = ps.find((p) => String(p.id) === localStorage.getItem("vega_me"));
          setMyAvatar(me?.avatar || null);
          setMeRecord((me as Me) || null);
        })
        .catch(() => {});
    refreshMe();
    window.addEventListener("vega-me-updated", refreshMe);
    return () => window.removeEventListener("vega-me-updated", refreshMe);
  }, [loc.pathname]);

  return (
    <ProLayout
      title={false}
      logo={
        <span className="app-brand">
          <Logo size={26} />
          <span className="app-brand-name">{t("app.name")}</span>
        </span>
      }
      layout="mix"
      navTheme="light"
      siderWidth={226}
      location={{ pathname: loc.pathname }}
      route={{ path: "/", routes: items }}
      menuItemRender={(item, dom) => <Link to={item.path || "/"}>{dom}</Link>}
      fixSiderbar
      fixedHeader
      breadcrumbRender={false}
      contentStyle={{ padding: 0, minHeight: "calc(100dvh - 56px)" }}
      token={{
        sider: {
          colorMenuBackground: "#ffffff",
          colorTextMenu: "rgba(0, 0, 0, 0.65)",
          colorTextMenuSelected: "#1677ff",
          colorTextMenuActive: "#1677ff",
          colorBgMenuItemSelected: "#e6f4ff",
          colorBgMenuItemHover: "rgba(0, 0, 0, 0.03)",
        },
        header: { colorBgHeader: "#ffffff" },
      }}
      actionsRender={() => [
        <LangSwitch key="lang-switch" />,
        <Select
          key="group-switch"
          style={{ width: 180 }}
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder={t("app.allGroups")}
          value={groupId ?? undefined}
          onChange={(v) => setGroupId(v ?? null)}
          options={groups.map((g) => ({ value: g.id, label: g.name }))}
        />,
      ]}
      avatarProps={{
        title: localStorage.getItem("vega_user") || t("app.account"),
        src: avatarSrc(myAvatar),
        render: (_p, dom) => (
          <Dropdown
            menu={{
              items: [
                { key: "profile", label: t("app.profile") },
                { type: "divider" },
                { key: "logout", label: t("app.logout") },
              ],
              onClick: ({ key }) => {
                if (key === "profile") {
                  setProfileOpen(true);
                  return;
                }
                if (key === "logout") {
                  token.clear();
                  localStorage.removeItem("vega_admin");
                  localStorage.removeItem("vega_user");
                  pingAuth();
                  goLogin();
                }
              },
            }}
          >
            {dom}
          </Dropdown>
        ),
      }}
    >
      <ProfileModal open={profileOpen} me={meRecord} onClose={() => setProfileOpen(false)} />
      <Routes>
        <Route path="/" element={<Navigate to="/workbench" replace />} />
        <Route path="*" element={<Navigate to="/workbench" replace />} />
        <Route path="/workbench" element={<Workbench />} />
        <Route path="/reqs" element={<Requirements />} />
        <Route path="/reqs/:id" element={<RequirementChat />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
        <Route path="/people" element={<People />} />
        <Route path="/groups" element={<Groups />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/systems" element={<Systems />} />
        <Route path="/nodes" element={<Nodes />} />
        <Route path="/integrations" element={<Integrations />} />
      </Routes>
    </ProLayout>
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
