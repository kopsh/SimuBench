import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { load, save } from "@/lib/storage";
import { en } from "./en";
import { zh } from "./zh";

export type Language = "zh" | "en";

export const language: Language = load<Language>("language", "zh");

void i18n.use(initReactI18next).init({
  resources: { zh: { translation: zh }, en: { translation: en } },
  lng: language,
  fallbackLng: "zh",
  interpolation: { escapeValue: false },
  returnNull: false,
});

document.documentElement.lang = language === "zh" ? "zh-CN" : "en";

export function setLanguage(lng: Language) {
  save("language", lng);
  void i18n.changeLanguage(lng);
  document.documentElement.lang = lng === "zh" ? "zh-CN" : "en";
}

/** Pick the label of a state variable in the current language. */
export function pickLabel(label?: string | null, labelEn?: string | null, fallback = ""): string {
  if (i18n.language === "en") return labelEn || label || fallback;
  return label || labelEn || fallback;
}

export default i18n;
