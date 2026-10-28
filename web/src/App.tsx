import { useEffect, useMemo, useState } from "react";
import { HashRouter, BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  ApartmentOutlined,
  ApiOutlined,
  ClusterOutlined,
  CodeOutlined,
  DashboardOutlined,
  LaptopOutlined,
  ProjectOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { ProLayout } from "@ant-design/pro-components";
import { Dropdown, Select } from "antd";
import Logo from "./components/Logo";
import ProfileModal, { Me } from "./components/ProfileModal";
import { avatarSrc } from "./components/PersonAvatar";
import { GroupProvider, useGroup } from "./group";
import { AUTH_EVENT, api, goLogin, pingAuth, token } from "./api";
import Login from "./pages/Login";
import People from "./pages/People";
import Groups from "./pages/Groups";
import Projects from "./pages/Projects";
import Requirements from "./pages/Requirements";
import RequirementChat from "./pages/RequirementChat";
import Tasks from "./pages/Tasks";
import TaskDetail from "./pages/TaskDetail";
import Workbench from "./pages/Workbench";
import Nodes from "./pages/Nodes";
import Integrations from "./pages/Integrations";

/** 菜单序：工作台总览 → 业务流（需求→任务）→ 配置（项目→组→人员）→ 系统（节点→集成） */
const MENU = [
  { path: "/workbench", name: "工作台", icon: <DashboardOutlined /> },
  { path: "/reqs", name: "需求池", icon: <ProjectOutlined /> },
  { path: "/tasks", name: "开发任务", icon: <CodeOutlined /> },
  { path: "/projects", name: "项目", icon: <ClusterOutlined /> },
  { path: "/groups", name: "研发组", icon: <ApartmentOutlined /> },
  { path: "/people", name: "人员", icon: <TeamOutlined /> },
  { path: "/nodes", name: "Runner 节点", icon: <LaptopOutlined /> },
  { path: "/integrations", name: "集成中心", icon: <ApiOutlined /> },
];

function Shell() {
  const loc = useLocation();
  const items = useMemo(() => MENU.map((m) => ({ path: m.path, name: m.name, icon: m.icon })), []);
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
          <span className="app-brand-name">研发过程AI管理平台</span>
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
        <Select
          key="group-switch"
          style={{ width: 180 }}
          allowClear
          placeholder="全部研发组"
          value={groupId ?? undefined}
          onChange={(v) => setGroupId(v ?? null)}
          options={groups.map((g) => ({ value: g.id, label: g.name }))}
        />,
      ]}
      avatarProps={{
        title: localStorage.getItem("vega_user") || "账号",
        src: avatarSrc(myAvatar),
        render: (_p, dom) => (
          <Dropdown
            menu={{
              items: [
                { key: "profile", label: "个人设置" },
                { type: "divider" },
                { key: "logout", label: "退出登录" },
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
