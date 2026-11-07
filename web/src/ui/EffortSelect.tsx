import { useTranslation } from "react-i18next";
import SearchSelect from "./SearchSelect";

/** LLM effort 的固定选项；空串 = 不设置（沿用全局默认）。 */
export default function EffortSelect({
  value,
  onChange,
  placeholder,
}: {
  value: string | null | undefined;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const { t } = useTranslation();
  const options = [
    { value: "", label: t("people.effortUnset") },
    { value: "low", label: t("people.effortLow") },
    { value: "medium", label: t("people.effortMedium") },
    { value: "high", label: t("people.effortHigh") },
    { value: "xhigh", label: t("people.effortXhigh") },
    { value: "max", label: t("people.effortMax") },
  ];
  return (
    <SearchSelect
      value={value ?? ""}
      onChange={(v) => onChange(v == null ? "" : String(v))}
      options={options}
      placeholder={placeholder ?? t("people.effortUnset")}
    />
  );
}