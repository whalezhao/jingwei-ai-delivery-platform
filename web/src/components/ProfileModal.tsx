import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import AvatarPicker from "./AvatarPicker";
import { Button } from "./motion/button/index";
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from "./motion/center-morph-modal";
import { Input } from "./motion/input";
import EffortSelect from "../ui/EffortSelect";
import { Field, useForm } from "../ui/form";
import { toast } from "../ui/toast";

/** 登录人自己的资料（PersonOut 的前端影子） */
export interface Me {
  id: number;
  name: string;
  username: string | null;
  avatar: string | null;
  gitlab_username: string | null;
  gitlab_email: string | null;
  octo_uid: string | null;
  llm_base_url: string | null;
  llm_model: string | null;
  llm_effort: string | null;
  has_llm_key: boolean;
  has_am_key: boolean;
  roles: string[];
}

const emptyProfile = {
  name: "",
  gitlab_email: "",
  avatar: "",
  llm_base_url: "",
  llm_api_key: "",
  llm_model: "",
  llm_effort: "",
  am_user_key: "",
};

const emptyPw = { old: "", new: "", confirm: "" };

/** 个人设置：基本信息/头像 + 个人 LLM 配置 + am key + 改密码。
 *  后端门禁：只能改自己；登录名、octo 绑定、管理员标记仅管理员可改。 */
export default function ProfileModal({
  open,
  me,
  onClose,
}: {
  open: boolean;
  me: Me | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const form = useForm(emptyProfile);
  const [pw, setPw] = useState(emptyPw);
  const [pwErr, setPwErr] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !me) return;
    form.reset({
      name: me.name,
      gitlab_email: me.gitlab_email ?? "",
      avatar: me.avatar ?? "",
      llm_base_url: me.llm_base_url ?? "",
      llm_api_key: "",
      llm_model: me.llm_model ?? "",
      llm_effort: me.llm_effort ?? "",
      am_user_key: "",
    });
    setPw(emptyPw);
    setPwErr("");
    // form 的 reset 引用稳定，不随渲染变化
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, me]);

  const save = async () => {
    if (!me) return;
    if (
      !form.validate({
        name: { required: true, message: t("profileModal.nameRequired") },
      })
    )
      return;
    const body: Record<string, unknown> = {
      name: form.values.name,
      gitlab_email: form.values.gitlab_email || null,
      avatar: form.values.avatar || "",
      llm_base_url: form.values.llm_base_url || null,
      llm_model: form.values.llm_model || null,
      llm_effort: form.values.llm_effort || "",
    };
    for (const k of ["llm_api_key", "am_user_key"] as const)
      if (form.values[k]) body[k] = form.values[k];
    setSaving(true);
    try {
      await api(`/api/people/${me.id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      toast.success(t("profileModal.saved"));
      window.dispatchEvent(new Event("vega-me-updated"));
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const changePw = async () => {
    if (!pw.old) return setPwErr(t("profileModal.oldPwRequired"));
    if (!pw.new) return setPwErr(t("profileModal.pwMin"));
    if (pw.new.length < 6) return setPwErr(t("profileModal.pwMin"));
    if (!pw.confirm) return setPwErr(t("profileModal.confirmRequired"));
    if (pw.new !== pw.confirm) return setPwErr(t("profileModal.pwMismatch"));
    try {
      await api("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({ old: pw.old, new: pw.new }),
      });
      toast.success(t("profileModal.pwChanged"));
      setPw(emptyPw);
      setPwErr("");
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const Section = ({ children }: { children: ReactNode }) => (
    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
      <span>{children}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );

  return (
    <CenterMorphModal
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <CenterMorphModalContent
        ariaLabel={t("profileModal.title")}
        className="max-w-[560px]"
      >
        <div className="flex max-h-[86vh] flex-col">
          <h2 className="m-0 px-6 pb-1 pt-5 text-lg font-semibold tracking-[-0.01em] text-foreground">
            {t("profileModal.title")}
          </h2>
          {me ? (
            <div className="flex flex-col gap-4 overflow-y-auto px-6 py-4">
              <Field label={t("people.avatar")}>
                <AvatarPicker
                  value={form.values.avatar}
                  onChange={(v) => form.set("avatar", v)}
                />
              </Field>
              <Input
                label={t("people.name")}
                value={form.values.name}
                onChange={(v) => form.set("name", v)}
                error={form.errors.name}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("people.gitlabEmail")}
                value={form.values.gitlab_email}
                onChange={(v) => form.set("gitlab_email", v)}
                placeholder={t("profileModal.emailPlaceholder")}
                classNames={{ field: "rounded-lg" }}
              />
              <div className="grid gap-1.5 rounded-lg bg-muted/50 px-3 py-2 text-[13px]">
                <div className="flex justify-between gap-3">
                  <span className="text-muted-foreground">
                    {t("profileModal.loginLabel")}
                  </span>
                  <span className="truncate">
                    {me.username || "—"} · octo:{" "}
                    {me.octo_uid || t("profileModal.octoUnbound")}
                  </span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-muted-foreground">
                    {t("profileModal.rolesLabel")}
                  </span>
                  <span className="truncate">
                    {(me.roles || []).join(t("profileModal.roleSep")) ||
                      t("people.notSet")}
                  </span>
                </div>
              </div>

              <Section>{t("profileModal.llmSection")}</Section>
              <Input
                label="LLM Base URL"
                value={form.values.llm_base_url}
                onChange={(v) => form.set("llm_base_url", v)}
                placeholder="https://…/v1"
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("profileModal.apiKeyLabel")}
                type="password"
                value={form.values.llm_api_key}
                onChange={(v) => form.set("llm_api_key", v)}
                placeholder={
                  me.has_llm_key ? t("profileModal.configuredKeep") : ""
                }
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label="LLM Model"
                value={form.values.llm_model}
                onChange={(v) => form.set("llm_model", v)}
                classNames={{ field: "rounded-lg" }}
              />
              <Field label={t("people.effort")} tip={t("people.effortTooltip")}>
                <EffortSelect
                  value={form.values.llm_effort}
                  onChange={(v) => form.set("llm_effort", v)}
                />
              </Field>
              <Input
                label={t("profileModal.amKeyLabel")}
                type="password"
                value={form.values.am_user_key}
                onChange={(v) => form.set("am_user_key", v)}
                placeholder={me.has_am_key ? t("profileModal.configuredKeep") : ""}
                classNames={{ field: "rounded-lg" }}
              />

              <Section>{t("profileModal.pwSection")}</Section>
              <Input
                label={t("profileModal.oldPw")}
                type="password"
                value={pw.old}
                onChange={(v) => setPw({ ...pw, old: v })}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("profileModal.newPw")}
                type="password"
                value={pw.new}
                onChange={(v) => setPw({ ...pw, new: v })}
                classNames={{ field: "rounded-lg" }}
              />
              <Input
                label={t("profileModal.confirmPw")}
                type="password"
                value={pw.confirm}
                onChange={(v) => setPw({ ...pw, confirm: v })}
                classNames={{ field: "rounded-lg" }}
                error={pwErr || undefined}
              />
              <div>
                <Button variant="outline" size="sm" onClick={changePw}>
                  {t("profileModal.pwSection")}
                </Button>
              </div>
            </div>
          ) : null}
          <div className="flex justify-end gap-2 border-t border-border px-6 py-3">
            <Button variant="outline" size="sm" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button size="sm" onClick={save} disabled={saving}>
              {t("profileModal.save")}
            </Button>
          </div>
        </div>
      </CenterMorphModalContent>
    </CenterMorphModal>
  );
}