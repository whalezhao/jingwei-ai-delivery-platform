import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import type { DeepKeys } from "./types";
import { zh } from "./locales/zh";
import { en } from "./locales/en";

export type Lang = "zh" | "en";
export type MsgKey = DeepKeys<typeof zh>;

/** 与 group.tsx 同款持久化：存过就用存的，否则跟随浏览器（非中文浏览器给英文）。 */
const KEY = "vega_lang";
const stored = localStorage.getItem(KEY);
const initial: Lang =
  stored === "zh" || stored === "en"
    ? stored
    : (navigator.language || "").toLowerCase().startsWith("zh")
      ? "zh"
      : "en";

void i18n.use(initReactI18next).init({
  resources: { zh: { translation: zh }, en: { translation: en } },
  lng: initial,
  fallbackLng: "zh",
  supportedLngs: ["zh", "en"],
  initAsync: false,
  interpolation: { escapeValue: false },
  debug: import.meta.env.DEV,
  returnNull: false,
});

/** 一处设置语言相关的全局副作用：dayjs、<html lang>、标题。 */
function apply(lng: string) {
  const lang: Lang = lng.toLowerCase().startsWith("en") ? "en" : "zh";
  dayjs.locale(lang === "zh" ? "zh-cn" : "en");
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.title = i18n.t("app.title");
}

i18n.on("languageChanged", apply);
apply(i18n.language);

export const setLang = (lng: Lang) => {
  localStorage.setItem(KEY, lng);
  void i18n.changeLanguage(lng);
};

export const getLang = (): Lang => (i18n.resolvedLanguage === "en" ? "en" : "zh");

export default i18n;