import { Avatar } from "antd";

/** 内置头像 key，必须与 public/avatars/aN.svg 及后端 people.AVATARS 白名单一致。 */
export const PRESET_AVATARS = Array.from({ length: 12 }, (_, i) => `a${i + 1}`);

export const avatarSrc = (key?: string | null) =>
  key && PRESET_AVATARS.includes(key) ? `/avatars/${key}.svg` : undefined;

export default function PersonAvatar({
  name,
  avatar,
  size = 28,
}: {
  name?: string | null;
  avatar?: string | null;
  size?: number;
}) {
  return (
    <Avatar size={size} src={avatarSrc(avatar)}>
      {(name || "?").slice(0, 1)}
    </Avatar>
  );
}
