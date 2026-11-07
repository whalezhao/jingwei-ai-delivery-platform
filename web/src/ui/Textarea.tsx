import type { TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export interface TextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange" | "className"> {
  label?: string;
  value?: string;
  onChange?: (value: string) => void;
  error?: string;
  className?: string;
}

/** beUI Input 没有 textarea 形态：补一个同样外观的多行输入。 */
export default function Textarea({
  label,
  value = "",
  onChange,
  error,
  className,
  rows = 4,
  ...rest
}: TextareaProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {label ? (
        <span className="px-1 text-sm font-medium text-foreground">{label}</span>
      ) : null}
      <textarea
        {...rest}
        rows={rows}
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        className={cn(
          "w-full resize-y rounded-lg border border-border bg-card px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-muted-foreground focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/15",
          error && "border-destructive",
        )}
      />
      {error ? <span className="px-1 text-xs text-destructive">{error}</span> : null}
    </div>
  );
}