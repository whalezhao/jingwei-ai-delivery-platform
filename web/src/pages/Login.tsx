import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Form, Input, message } from "antd";
import { login } from "../api";
import Logo from "../components/Logo";

const FLOW = [
  ["01", "上传需求文档，认领进池"],
  ["02", "与 AI 多轮对话，确认执行方案"],
  ["03", "任务派发 runner，MR 待人审核"],
];

export default function Login() {
  const nav = useNavigate();
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
          <p className="tagline">研发过程AI管理平台</p>
        </div>
        <ul className="login-flow">
          {FLOW.map(([n, t]) => (
            <li key={n}>
              <span className="n">{n}</span>
              {t}
            </li>
          ))}
        </ul>
      </div>
      <div className="login-panel">
        <div className="login-form">
          <div className="mobile-brand">
            <Logo size={30} />
          </div>
          <h2>登录</h2>
          <p className="sub">使用 GitLab 账号（用户名 + 平台密码）</p>
          <Form
            layout="vertical"
            onFinish={async (v) => {
              setBusy(true);
              try {
                await login(v.username, v.password);
                nav("/reqs");
              } catch (e: any) {
                message.error(e.message || "登录失败");
              } finally {
                setBusy(false);
              }
            }}
          >
            <Form.Item name="username" label="账号" rules={[{ required: true, message: "请输入用户名" }]}>
              <Input autoFocus size="large" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password size="large" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block size="large" loading={busy}>
              登录
            </Button>
          </Form>
        </div>
      </div>
    </div>
  );
}
