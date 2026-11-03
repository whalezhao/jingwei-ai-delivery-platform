import { Segmented } from "antd";
import { useTranslation } from "react-i18next";
import { setLang, type Lang } from "../i18n";

/** 语言名用各自语言写（中文 / EN），不参与翻译。 */
export default function LangSwitch() {
  const { i18n } = useTranslation();
  const lang: Lang = i18n.resolvedLanguage === "en" ? "en" : "zh";
  return (
    <Segmented
      size="small"
      value={lang}
      onChange={(v) => setLang(v as Lang)}
      options={[
        { value: "zh", label: "中文" },
        { value: "en", label: "EN" },
      ]}
    />
  );
}