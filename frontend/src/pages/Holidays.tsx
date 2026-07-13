import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, getHolidays, putHolidays } from "../api/client";
import type { HolidayItem } from "../api/types";
import { Banner, ConfirmDialog } from "../components/ui";

export default function Holidays() {
  const { t } = useTranslation();
  const currentYear = new Date().getUTCFullYear();
  const [year, setYear] = useState(currentYear);
  const [rows, setRows] = useState<HolidayItem[] | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [confirmSave, setConfirmSave] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setSaved(false);
    getHolidays(year)
      .then((hs) => {
        if (!cancelled) setRows(hs);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [year]);

  function updateRow(idx: number, patch: Partial<HolidayItem>) {
    setRows((prev) => (prev ? prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)) : prev));
  }

  async function doSave() {
    setConfirmSave(false);
    if (!rows) return;
    setBusy(true);
    setError("");
    try {
      // Send only rows with both a date and a name.
      const clean = rows.filter((r) => r.date && r.name.trim() !== "");
      const result = await putHolidays(year, clean);
      setRows(result);
      setSaved(true);
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h1>{t("holidays.title")}</h1>
      <p className="hint">{t("holidays.hint")}</p>
      {error && <Banner tone="error" onDismiss={() => setError("")}>{error}</Banner>}
      {saved && (
        <Banner tone="success" onDismiss={() => setSaved(false)}>
          {t("holidays.saved")}
        </Banner>
      )}

      <div className="year-nav">
        <button type="button" className="secondary small" onClick={() => setYear((y) => y - 1)}>
          ‹
        </button>
        <input
          type="number"
          min={2000}
          max={2100}
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="year-input"
        />
        <button type="button" className="secondary small" onClick={() => setYear((y) => y + 1)}>
          ›
        </button>
      </div>

      {rows === null ? (
        <p className="hint">{t("common.loading")}</p>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("holidays.date")}</th>
                <th>{t("holidays.name")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, idx) => (
                <tr key={idx}>
                  <td>
                    <input
                      type="date"
                      min={`${year}-01-01`}
                      max={`${year}-12-31`}
                      value={r.date}
                      onChange={(e) => updateRow(idx, { date: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      value={r.name}
                      onChange={(e) => updateRow(idx, { name: e.target.value })}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="link-button danger"
                      onClick={() => setRows((prev) => (prev ? prev.filter((_, i) => i !== idx) : prev))}
                    >
                      {t("common.delete")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="action-bar">
            <button
              type="button"
              className="secondary small"
              onClick={() =>
                setRows((prev) => [...(prev ?? []), { date: `${year}-01-01`, name: "" }])
              }
            >
              {t("holidays.addRow")}
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirmSave(true)}>
              {busy ? t("common.saving") : t("holidays.save")}
            </button>
          </div>
        </>
      )}

      {confirmSave && (
        <ConfirmDialog
          title={t("holidays.save")}
          message={t("holidays.saveConfirm", { year })}
          confirmLabel={t("common.continue")}
          onConfirm={doSave}
          onCancel={() => setConfirmSave(false)}
        />
      )}
    </div>
  );
}
