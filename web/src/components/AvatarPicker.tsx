import { Avatar } from "antd";
import { useTranslation } from "react-i18next";
import { PRESET_AVATARS } from "./PersonAvatar";

/** 内置头像选择器（Form.Item 注入 value/onChange；顶栏弹窗直接受控使用）；不提供上传入口。 */
export default function AvatarPicker({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
  const { t } = useTranslation();
  const pick = (v: string) => onChange?.(v);
  return (
    <>
      <div className="avatar-picker">
        {PRESET_AVATARS.map((k) => (
          <a key={k} className={"avatar-opt" + (value === k ? " on" : "")} onClick={() => pick(k)}>
            <Avatar size={36} src={`/avatars/${k}.svg`} />
          </a>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>{t("avatarPicker.builtinOnly")}</div>
    </>
  );
}
