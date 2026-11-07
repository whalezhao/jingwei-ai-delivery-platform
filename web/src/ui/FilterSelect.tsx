import { Combobox } from "../components/motion/combobox/context";
import { ComboboxContent } from "../components/motion/combobox/content";
import {
  ComboboxInput,
  ComboboxTrigger,
  ComboboxValue,
} from "../components/motion/combobox/trigger";
import { ComboboxItem, ComboboxList } from "../components/motion/combobox/list";
import { cn } from "@/lib/utils";

const ALL = "__all__";

/** 顶栏全局筛选：可搜索、带「全部」项，值仍以 number | null 对外。 */
export default function FilterSelect({
  value,
  onChange,
  options,
  placeholder,
  allLabel,
  className,
}: {
  value: number | null;
  onChange: (value: number | null) => void;
  options: { value: number; label: string }[];
  placeholder: string;
  allLabel: string;
  className?: string;
}) {
  const items = [{ value: ALL, label: allLabel }, ...options.map((o) => ({ value: String(o.value), label: o.label }))];

  return (
    <Combobox
      value={value == null ? ALL : String(value)}
      onValueChange={(v) => onChange(v === ALL ? null : Number(v))}
    >
      <ComboboxTrigger
        className={cn(
          "h-9 min-w-0 rounded-lg bg-card px-2.5 text-[13px]",
          className,
        )}
      >
        <ComboboxValue placeholder={placeholder} />
      </ComboboxTrigger>
      <ComboboxContent>
        <div className="border-b border-border p-1.5">
          <ComboboxInput
            placeholder={placeholder}
            wrapperClassName="rounded-lg bg-muted/50 px-2"
            className="h-8 text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <ComboboxList>
          {items.map((item) => (
            <ComboboxItem
              key={item.value}
              value={item.value}
              textValue={item.label}
            >
              {item.label}
            </ComboboxItem>
          ))}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}