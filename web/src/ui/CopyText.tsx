import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/** 一次性显示、可复制的命令/密钥：左等宽文本，右复制按钮。 */
export default function CopyText({
  value,
  className,
}: {
  value: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板被浏览器拒绝时不谎报成功；用户仍可手动选中文本
    }
  };

  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-2.5 py-2",
        className,
      )}
    >
      <code className="min-w-0 flex-1 break-all font-mono text-[12px] leading-5 text-foreground">
        {value}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label={t("common.copy")}
        className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        {copied ? (
          <Check className="size-3.5 text-success" />
        ) : (
          <Copy className="size-3.5" />
        )}
      </button>
    </div>
  );
}