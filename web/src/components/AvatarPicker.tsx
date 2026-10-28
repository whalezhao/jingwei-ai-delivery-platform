import { Avatar } from "antd";
import { PRESET_AVATARS } from "./PersonAvatar";

/** 内置头像选择器（Form.Item 注入 value/onChange；顶栏弹窗直接受控使用）；不提供上传入口。 */
export default function AvatarPicker({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
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
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>仅支持内置头像，不支持上传</div>
    </>
  );
}
