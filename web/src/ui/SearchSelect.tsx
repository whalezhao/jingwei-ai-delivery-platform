import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Combobox } from "../components/motion/combobox/context";
import { ComboboxContent } from "../components/motion/combobox/content";
import {
  ComboboxInput,
  ComboboxTrigger,
  ComboboxValue,
} from "../components/motion/combobox/trigger";
import {
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
} from "../components/motion/combobox/list";
import { cn } from "@/lib/utils";

/** 空值也必须走受控字符串，否则 Combobox 会切回非受控内部状态。 */
const CLEAR = "__clear__";

export interface SearchSelectOption {
  value: string | number;
  label: string;
}

/** 表单里的单选下拉：可搜索、可清空，值保持原始 number/string。 */
export default function SearchSelect({
  value,
  onChange,
  options,
  placeholder,
  clearable = false,
  disabled = false,
  onOpen,
  leftIcon,
  className,
  style,
}: {
  value: string | number | null | undefined;
  onChange: (value: string | number | null) => void;
  options: SearchSelectOption[];
  placeholder?: string;
  clearable?: boolean;
  disabled?: boolean;
  onOpen?: () => void;
  leftIcon?: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  const { t } = useTranslation();
  return (
    <div className="min-w-0" style={style}>
      <Combobox
        value={value == null ? "" : String(value)}
        onValueChange={(v) => {
          if (v === CLEAR) return onChange(null);
          const option = options.find((o) => String(o.value) === v);
          onChange(option ? option.value : v);
        }}
        onOpenChange={(open) => open && onOpen?.()}
        disabled={disabled}
      >
        <ComboboxTrigger
          className={cn(
            "h-9 min-w-0 rounded-lg bg-card px-2.5 text-[13px]",
            className,
          )}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            {leftIcon ? (
              <span className="shrink-0 text-muted-foreground [&_svg]:size-4">
                {leftIcon}
              </span>
            ) : null}
            <ComboboxValue>
              {(current, label) =>
                label != null ? (
                  label
                ) : current ? (
                  <span className="font-mono text-[12px]">{current}</span>
                ) : (
                  <span className="text-muted-foreground">{placeholder}</span>
                )
              }
            </ComboboxValue>
          </span>
        </ComboboxTrigger>
        <ComboboxContent>
          <div className="border-b border-border p-1.5">
            <ComboboxInput
              placeholder={placeholder ?? t("common.search")}
              wrapperClassName="rounded-lg bg-muted/50 px-2"
              className="h-8 text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <ComboboxList>
            {clearable ? (
              <ComboboxItem value={CLEAR} textValue={t("common.clear")}>
                {t("common.clear")}
              </ComboboxItem>
            ) : null}
            {options.map((option) => (
              <ComboboxItem
                key={String(option.value)}
                value={String(option.value)}
                textValue={option.label}
              >
                {option.label}
              </ComboboxItem>
            ))}
            <ComboboxEmpty>{t("common.noData")}</ComboboxEmpty>
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
    </div>
  );
}