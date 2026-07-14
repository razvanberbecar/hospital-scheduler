import { useTranslation } from "react-i18next";

import type { StaffType } from "../api/types";

export default function Legend({ staffType }: { staffType?: StaffType }) {
  const { t } = useTranslation();
  const isAide = staffType === "infirmiera";
  // Infirmiere have no kitchen (K) or head (H) shift; their 8h slot is the "8".
  const items: { cls: string; code: string; label: string }[] = isAide
    ? [
        { cls: "c-z", code: "Z", label: t("legend.z") },
        { cls: "c-n", code: "N", label: t("legend.n") },
        { cls: "c-k", code: "8", label: t("legend.eight") },
        { cls: "c-t", code: "4/8", label: t("legend.t") },
        { cls: "c-co", code: "Co", label: t("legend.co") },
      ]
    : [
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
