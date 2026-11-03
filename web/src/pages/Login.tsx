import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Form, Input, message } from "antd";
import { useTranslation } from "react-i18next";
import { login } from "../api";
import Logo from "../components/Logo";
import LangSwitch from "../components/LangSwitch";

const FLOW = [
  ["01", "login.flow1"],
  ["02", "login.flow2"],
  ["03", "login.flow3"],
] as const;

export default function Login() {
  const nav = useNavigate();
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  return (
    <div className="login-wrap">
      <div className="login-brand">
        {/* 幽灵字标贴底出血，当背景不抢戏 */}
        <div className="login-ghost" aria-hidden>
          Vega<b>.</b>
        </div>
        <div>
          <div className="brand-lockup">
            <Logo size={46} />
          </div>
          <p className="tagline">{t("app.name")}</p>
        </div>
        <ul className="login-flow">
          {FLOW.map(([n, text]) => (
            <li key={n}>
              <span className="n">{n}</span>
              {t(text)}
            </li>
          ))}
        </ul>
      </div>
      <div className="login-panel">
        <div className="login-form">
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
            <LangSwitch />
          </div>
          <div className="mobile-brand">
            <Logo size={30} />
          </div>
          <h2>{t("login.title")}</h2>
          <p className="sub">{t("login.subtitle")}</p>
          <Form
            layout="vertical"
            onFinish={async (v) => {
              setBusy(true);
              try {
                await login(v.username, v.password);
                nav("/reqs");
              } catch (e: any) {
                message.error(e.message || t("login.failed"));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Form.Item
              name="username"
              label={t("login.username")}
              rules={[{ required: true, message: t("login.usernameRequired") }]}
            >
              <Input autoFocus size="large" />
            </Form.Item>
            <Form.Item
              name="password"
              label={t("login.password")}
              rules={[{ required: true, message: t("login.passwordRequired") }]}
            >
              <Input.Password size="large" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block size="large" loading={busy}>
              {t("login.submit")}
            </Button>
          </Form>
        </div>
      </div>
    </div>
  );
}
