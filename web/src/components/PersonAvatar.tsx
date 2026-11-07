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
  const src = avatarSrc(avatar);
  if (src)
    return (
      <img
        src={src}
        alt=""
        width={size}
        height={size}
        className="shrink-0 rounded-full object-cover"
      />
    );
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-muted font-medium text-foreground"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
    >
      {(name || "?").slice(0, 1)}
    </span>
  );
}