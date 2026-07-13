import { useTranslation } from "react-i18next";

import { setLanguage } from "../i18n";

export default function LangSwitcher() {
  const { i18n } = useTranslation();
  return (
    <div className="lang">
      {(["ro", "en"] as const).map((lang) => (
        <button
          key={lang}
          type="button"
          className={i18n.language === lang ? "active" : ""}
          onClick={() => setLanguage(lang)}
        >
          {lang.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
