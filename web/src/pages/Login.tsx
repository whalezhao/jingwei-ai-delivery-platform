import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { login } from "../api";
import Logo, { LogoMark } from "../components/Logo";
import LangSwitch from "../components/LangSwitch";
import { Input } from "../components/motion/input";
import { Button } from "../components/motion/button/index";

const FLOW = [
  ["01", "login.flow1"],
  ["02", "login.flow2"],
  ["03", "login.flow3"],
  ["04", "login.flow4"],
  ["05", "login.flow5"],
  ["06", "login.flow6"],
] as const;

export default function Login() {
  const nav = useNavigate();
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");

  const missingUser = submitted && !username.trim();
  const missingPass = submitted && !password;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setError("");
    if (!username.trim() || !password) return;
    setBusy(true);
    try {
      await login(username.trim(), password);
      nav("/reqs");
    } catch (err: any) {
      setError(err.message || t("login.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(430px,44%)_1fr]">
      {/* 登录页是全站唯一的墨色时刻：织机网格 + 一根金线 + 幽灵 V */}
      <aside className="relative hidden overflow-hidden bg-[#12161C] px-14 py-12 text-[#F4F1E8] lg:flex lg:flex-col lg:justify-between">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(rgba(244,241,232,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(244,241,232,0.05) 1px, transparent 1px)",
            backgroundSize: "44px 44px",
            maskImage:
              "radial-gradient(120% 90% at 20% 0%, #000 28%, transparent 76%)",
            WebkitMaskImage:
              "radial-gradient(120% 90% at 20% 0%, #000 28%, transparent 76%)",
          }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-[74px] w-px bg-gradient-to-b from-transparent via-[#B8893B]/50 to-transparent"
        />
        <div aria-hidden className="pointer-events-none absolute -bottom-24 -right-16">
          <LogoMark size={360} simplified className="opacity-[0.06]" />
        </div>

        <div className="relative">
          <Logo size={44} onDark />
          <p className="mt-5 text-[13px] tracking-[0.18em] text-[#F4F1E8]/45">
            {t("app.name")}
          </p>
        </div>

        <div className="relative max-w-[460px] pt-16">
          <span className="block h-px w-12 bg-[#B8893B]" />
          <h1 className="mt-6 text-[clamp(34px,3.2vw,44px)] font-semibold leading-[1.15] tracking-[-0.02em] text-balance">
            {t("app.tagline")}
          </h1>
          <p className="mt-4 max-w-[380px] text-sm leading-relaxed text-[#F4F1E8]/50">
            {t("app.taglineDesc")}
          </p>
        </div>

        <ul className="relative mt-14 border-t border-[#F4F1E8]/10">
          {FLOW.map(([n, key]) => (
            <li
              key={n}
              className="flex items-baseline gap-5 border-b border-[#F4F1E8]/10 py-3 text-[13px] text-[#F4F1E8]/70"
            >
              <span className="font-mono text-[11px] tracking-[0.08em] text-[#B8893B]">
                {n}
              </span>
              {t(key)}
            </li>
          ))}
        </ul>
      </aside>

      <main className="flex min-h-dvh flex-col bg-background px-6 py-8 lg:py-10">
        <div className="flex justify-end">
          <LangSwitch />
        </div>

        <div className="flex flex-1 items-center justify-center py-8">
          <div className="w-full max-w-[396px] rounded-2xl border border-[rgba(20,23,28,0.08)] bg-white p-8 shadow-[0_1px_2px_rgba(20,23,28,0.04),0_28px_56px_-32px_rgba(20,23,28,0.22)] sm:p-10">
            <div className="mb-8 lg:hidden">
              <Logo size={30} />
            </div>
            <h2 className="m-0 text-[26px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
              {t("login.title")}
            </h2>
            <p className="mt-1.5 text-[13px] text-muted-foreground">
              {t("login.subtitle")}
            </p>

            <form className="mt-8 flex flex-col gap-5" onSubmit={submit} noValidate>
              <Input
                label={t("login.username")}
                value={username}
                onChange={setUsername}
                error={missingUser ? t("login.usernameRequired") : undefined}
                leftIcon={<UserRound />}
                classNames={{ field: "rounded-lg" }}
                autoFocus
                autoComplete="username"
              />
              <Input
                label={t("login.password")}
                type="password"
                value={password}
                onChange={setPassword}
                error={missingPass ? t("login.passwordRequired") : undefined}
                leftIcon={<KeyRound />}
                classNames={{ field: "rounded-lg" }}
                autoComplete="current-password"
              />

              {error ? (
                <p
                  role="alert"
                  className="m-0 rounded-lg bg-destructive/5 px-3 py-2 text-[13px] text-destructive"
                >
                  {error}
                </p>
              ) : null}

              <Button
                type="submit"
                size="lg"
                disabled={busy}
                className="mt-1 h-11 w-full rounded-lg text-[15px]"
              >
                {busy ? (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                ) : null}
                {t("login.submit")}
              </Button>
            </form>

            <p className="mt-8 text-center text-[11px] tracking-[0.14em] text-muted-foreground/70">
              JINGWEI · {t("app.name")}
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}