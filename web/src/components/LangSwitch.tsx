import { useTranslation } from "react-i18next";
import { setLang, type Lang } from "../i18n";
import { cn } from "@/lib/utils";

/** 语言名用各自语言写（中文 / EN），不参与翻译。 */
export default function LangSwitch() {
  const { i18n } = useTranslation();
  const lang: Lang = i18n.resolvedLanguage === "en" ? "en" : "zh";
  return (
    <div className="inline-flex items-center rounded-lg border border-border bg-card p-0.5 text-[13px]">
      {(
        [
          ["zh", "中文"],
          ["en", "EN"],
        ] as const
      ).map(([key, label]) => (
        <button
          key={key}
          type="button"
          onClick={() => setLang(key)}
          aria-pressed={lang === key}
          className={cn(
            "rounded-md px-2 py-0.5 transition-colors",
            lang === key
              ? "bg-foreground text-background"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}