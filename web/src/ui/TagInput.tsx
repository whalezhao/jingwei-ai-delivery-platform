import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import Badge from "./Badge";

/** 自由标签输入：回车/中英文逗号追加，退格删最后一枚，失焦提交草稿。 */
export default function TagInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");

  const commit = (raw: string) => {
    const added = raw
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean)
      .filter((tag) => !value.includes(tag));
    if (added.length) onChange([...value, ...added]);
    setDraft("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === "," || e.key === "，") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && !draft && value.length) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border border-border bg-card px-2 py-1.5 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/15">
      {value.map((tag) => (
        <Badge key={tag} className="gap-1 pr-1">
          {tag}
          <button
            type="button"
            aria-label={`${t("common.delete")} ${tag}`}
            onClick={() => onChange(value.filter((x) => x !== tag))}
            className="text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => commit(draft)}
        placeholder={value.length ? undefined : placeholder}
        className="h-6 min-w-24 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}