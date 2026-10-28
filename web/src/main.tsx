import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import "./global.css";
import App from "./App";

dayjs.locale("zh-cn");

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

createRoot(document.getElementById("root")!).render(
  <ConfigProvider locale={zhCN} theme={theme}>
    <App />
  </ConfigProvider>,
);
