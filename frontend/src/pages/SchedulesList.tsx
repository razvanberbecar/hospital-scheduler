import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ApiError, createSchedule, getHolidays, listSchedules, listUsers } from "../api/client";
import type { ScheduleListItem } from "../api/types";
import { useUser } from "../context/UserContext";
import { monthYearLabel, nextMonth, suggestedTarget } from "../lib/dates";
import { Banner, StatusBadge } from "../components/ui";

export default function SchedulesList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { me } = useUser();
  const isManager = me?.role === "manager_nurse";

  const [rows, setRows] = useState<ScheduleListItem[] | null>(null);
  const [error, setError] = useState("");

  async function load() {
    try {
      const list = await listSchedules();
      setRows(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Default new-month = the month after the latest listed schedule, else current.
  const defaultYM = useMemo(() => {
    if (rows && rows.length > 0) {
      const latest = rows.reduce((a, b) =>
        a.year > b.year || (a.year === b.year && a.month > b.month) ? a : b,
      );
      return nextMonth(latest.year, latest.month);
    }
    const now = new Date();
    return { year: now.getUTCFullYear(), month: now.getUTCMonth() + 1 };
  }, [rows]);

  return (
    <div className="page">
      <h1>{t("schedules.title")}</h1>
      {error && <Banner tone="error">{error}</Banner>}

      {isManager && <NewMonthForm defaultYM={defaultYM} />}

      {rows === null ? (
        <p className="hint">{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="hint">{t("schedules.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("schedules.month")}</th>
              <th>{t("schedules.status")}</th>
              <th>{t("schedules.target")}</th>
              <th>{t("schedules.approvals")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {[...rows]
              .sort((a, b) => b.year - a.year || b.month - a.month)
              .map((s) => (
                <tr key={s.id}>
                  <td>{monthYearLabel(s.year, s.month)}</td>
                  <td>
                    <StatusBadge status={s.status} />
                  </td>
                  <td>{s.target_hours}</td>
                  <td>{s.approval_count}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary small"
                      onClick={() => navigate(`/schedules/${s.id}`)}
                    >
                      {t("schedules.open")}
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function NewMonthForm({ defaultYM }: { defaultYM: { year: number; month: number } }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [year, setYear] = useState(defaultYM.year);
  const [month, setMonth] = useState(defaultYM.month);
  const [target, setTarget] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Keep the form defaults synced to the latest schedule once it loads.
  useEffect(() => {
    setYear(defaultYM.year);
    setMonth(defaultYM.month);
  }, [defaultYM.year, defaultYM.month]);

  // Recompute the suggested target whenever year/month changes (fetch holidays).
  useEffect(() => {
    let cancelled = false;
    getHolidays(year)
      .then((hs) => {
        if (cancelled) return;
        const set = new Set(hs.map((h) => h.date));
        setTarget(suggestedTarget(year, month, set));
      })
      .catch(() => {
        if (!cancelled) setTarget(suggestedTarget(year, month, new Set()));
      });
    return () => {
      cancelled = true;
    };
  }, [year, month]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const users = await listUsers();
      const active = users.filter((u) => u.active);
      const detail = await createSchedule({
        year,
        month,
        target_hours: typeof target === "number" ? target : 0,
        roster: active.map((u) => ({ user_id: u.id, kitchen_capable: null })),
        days_off: [],
        requests: [],
        config: {},
      });
      navigate(`/schedules/${detail.id}/setup`);
    } catch (e2) {
      if (e2 instanceof ApiError && e2.status === 409) setError(t("schedules.exists"));
      else setError(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card-inline new-month" onSubmit={onSubmit}>
      <h2>{t("schedules.newTitle")}</h2>
      <div className="form-row">
        <div className="field">
          <label htmlFor="nm-year">{t("schedules.year")}</label>
          <input
            id="nm-year"
            type="number"
            min={2000}
            max={2100}
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="nm-month">{t("schedules.monthField")}</label>
          <input
            id="nm-month"
            type="number"
            min={1}
            max={12}
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="nm-target">{t("schedules.target")}</label>
          <input
            id="nm-target"
            type="number"
            min={0}
            value={target}
            onChange={(e) => setTarget(e.target.value === "" ? "" : Number(e.target.value))}
          />
        </div>
        <div className="field field-action">
          <button type="submit" disabled={busy}>
            {busy ? t("common.working") : t("schedules.create")}
          </button>
        </div>
      </div>
      <p className="hint">{t("schedules.targetHint")}</p>
      {error && <p className="error">{error}</p>}
    </form>
  );
}
