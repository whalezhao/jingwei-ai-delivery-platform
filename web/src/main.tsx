import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import { useTranslation } from "react-i18next";
import "./i18n";
import "./global.css";
import App from "./App";

// 主题与 aster-qa 对齐：antd 默认蓝 + 圆角 10，浅色侧栏
const theme = {
  token: {
    colorPrimary: "#1677ff",
    colorInfo: "#1677ff",
    colorLink: "#1677ff",
    colorBgLayout: "#f5f5f5",
    borderRadius: 10,
  },
  components: {
    Card: { borderRadiusLG: 14 },
    Modal: { borderRadiusLG: 14 },
  },
};

/** ConfigProvider 必须包住 Router（登录页在 Shell 之外），所以留在入口这层。 */
function Root() {
  const { i18n } = useTranslation();
  const lang = i18n.resolvedLanguage === "en" ? "en" : "zh";
  return (
    <ConfigProvider locale={lang === "zh" ? zhCN : enUS} theme={theme}>
      <App />
    </ConfigProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Root />);