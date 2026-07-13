import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import {
  getApprovalSnapshot,
  getHolidays,
  getSchedule,
  listApprovals,
  listUsers,
} from "../api/client";
import type {
  ApprovalListItem,
  ApprovalSnapshot,
  NurseTotalsOut,
  RosterEntryOut,
  ScheduleDetailOut,
  UserOut,
} from "../api/types";
import { dateTimeLabel, monthDates, monthYearLabel } from "../lib/dates";
import ScheduleGrid from "../components/ScheduleGrid";
import { Banner } from "../components/ui";

export default function ScheduleHistory() {
  const { t } = useTranslation();
  const { id } = useParams();
  const scheduleId = Number(id);

  const [detail, setDetail] = useState<ScheduleDetailOut | null>(null);
  const [approvals, setApprovals] = useState<ApprovalListItem[]>([]);
  const [users, setUsers] = useState<UserOut[]>([]);
  const [holidays, setHolidays] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<ApprovalSnapshot | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [d, apps, us] = await Promise.all([
          getSchedule(scheduleId),
          listApprovals(scheduleId),
          listUsers(),
        ]);
        setDetail(d);
        setApprovals(apps);
        setUsers(us);
        const hs = await getHolidays(d.year);
        setHolidays(new Set(hs.map((h) => h.date)));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [scheduleId]);

  const openVersion = useCallback(
    async (version: number) => {
      setSelected(version);
      setSnapshot(null);
      try {
        const res = await getApprovalSnapshot(scheduleId, version);
        setSnapshot(res.snapshot);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [scheduleId],
  );

  function approverName(userId: number): string {
    const u = users.find((x) => x.id === userId);
    return u ? u.full_name : `#${userId}`;
  }

  if (error && !detail) {
    return (
      <div className="page">
        <Banner tone="error">{error}</Banner>
        <Link to="/schedules" className="nav-link">
          {t("common.back")}
        </Link>
      </div>
    );
  }
  if (!detail) return <p className="hint page">{t("common.loading")}</p>;

  // Build the read-only grid roster from the detail's roster (names/sections),
  // and totals from the snapshot (string keys -> NurseTotalsOut list).
  const roster: RosterEntryOut[] = detail.setup.roster;
  const days = monthDates(detail.year, detail.month);

  const snapTotals: NurseTotalsOut[] = snapshot
    ? Object.entries(snapshot.totals).map(([uid, t2]) => ({
        user_id: Number(uid),
        worked_normal: t2.worked_normal,
        worked_weekend: t2.worked_weekend,
        leave_days: t2.leave_days,
        leave_credit: 0, // snapshot has no leave_credit; grid shows leave_days
        worked: 0,
        total: t2.total,
        effective_target: t2.effective_target,
        owed: t2.owed,
        nights: t2.nights,
        kitchens: t2.kitchens,
        topup_hours: 0,
      }))
    : [];

  return (
    <div className="page">
      <div className="detail-header">
        <h1>
          {t("history.title")} — {monthYearLabel(detail.year, detail.month)}
        </h1>
        <Link to={`/schedules/${detail.id}`} className="nav-link">
          {t("common.back")}
        </Link>
      </div>

      {error && <Banner tone="error" onDismiss={() => setError("")}>{error}</Banner>}

      {approvals.length === 0 ? (
        <p className="hint">{t("history.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("history.version")}</th>
              <th>{t("history.when")}</th>
              <th>{t("history.by")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {approvals.map((a) => (
              <tr key={a.version} className={selected === a.version ? "row-selected" : ""}>
                <td>v{a.version}</td>
                <td>{dateTimeLabel(a.approved_at)}</td>
                <td>{approverName(a.approved_by)}</td>
                <td>
                  <button
                    type="button"
                    className="secondary small"
                    onClick={() => openVersion(a.version)}
                  >
                    {t("history.view")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected != null && (
        <div className="panel">
          <h2>{t("history.snapshotLabel", { version: selected })}</h2>
          {!snapshot ? (
            <p className="hint">{t("common.loading")}</p>
          ) : (
            <>
              <ScheduleGrid
                year={detail.year}
                month={detail.month}
                days={days}
                roster={roster}
                assignments={snapshot.assignments}
                daysOff={[]}
                totals={snapTotals}
                perDay={[]}
                holidays={holidays}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
