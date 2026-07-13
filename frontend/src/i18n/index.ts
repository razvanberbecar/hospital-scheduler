import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import ro from "./ro.json";

i18n.use(initReactI18next).init({
  resources: { ro: { translation: ro }, en: { translation: en } },
  lng: localStorage.getItem("lang") ?? "ro",
  fallbackLng: "ro",
  interpolation: { escapeValue: false },
});

export function setLanguage(lang: "ro" | "en") {
  localStorage.setItem("lang", lang);
  void i18n.changeLanguage(lang);
}

export default i18n;
