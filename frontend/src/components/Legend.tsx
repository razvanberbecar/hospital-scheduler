import { useTranslation } from "react-i18next";

export default function Legend() {
  const { t } = useTranslation();
  const items: { cls: string; code: string; label: string }[] = [
    { cls: "c-z", code: "Z", label: t("legend.z") },
    { cls: "c-n", code: "N", label: t("legend.n") },
    { cls: "c-k", code: "8", label: t("legend.k") },
    { cls: "c-h", code: "8", label: t("legend.h") },
    { cls: "c-t", code: "4/8", label: t("legend.t") },
    { cls: "c-co", code: "Co", label: t("legend.co") },
  ];
  return (
    <div className="legend">
      {items.map((it) => (
        <span className="legend-item" key={it.label}>
          <span className={`legend-swatch ${it.cls}`}>{it.code}</span>
          {it.label}
        </span>
      ))}
      <span className="legend-item">
        <sup className="lock-mark">▪</sup>
        {t("legend.locked")}
      </span>
    </div>
  );
}
