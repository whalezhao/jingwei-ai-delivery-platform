import { useTranslation } from "react-i18next";
import { PRESET_AVATARS } from "./PersonAvatar";
import { cn } from "@/lib/utils";

/** 内置头像选择器（受控 value/onChange）；不提供上传入口。 */
export default function AvatarPicker({
  value,
  onChange,
}: {
  value?: string;
  onChange?: (v: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-1.5">
        {PRESET_AVATARS.map((key) => (
          <button
            key={key}
            type="button"
            aria-label={key}
            aria-pressed={value === key}
            onClick={() => onChange?.(key)}
            className={cn(
              "rounded-full p-0.5 ring-2 transition-[box-shadow]",
              value === key
                ? "ring-primary"
                : "ring-transparent hover:ring-(--color-border-strong)",
            )}
          >
            <img
              src={`/avatars/${key}.svg`}
              alt=""
              className="size-9 rounded-full"
            />
          </button>
        ))}
      </div>
      <span className="px-1 text-xs text-muted-foreground">
        {t("avatarPicker.builtinOnly")}
      </span>
    </div>
  );
}