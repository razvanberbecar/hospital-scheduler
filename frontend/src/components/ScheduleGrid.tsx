// The core GRAFIC grid: nurse rows x day columns, section groups, weekend/
// holiday shading, per-cell codes, totals columns and coverage rows.
// Reused by the detail screen (editable) and the history screen (read-only,
// via an adapter that feeds snapshot assignments and totals).

import { Fragment } from "react";
import { useTranslation } from "react-i18next";

import type {
  AssignmentOut,
  DayCoverageOut,
  DayOffIn,
  NurseTotalsOut,
  RosterEntryOut,
  SnapshotAssignment,
  StaffType,
} from "../api/types";
import { dayOf, isWeekend } from "../lib/dates";
import { cellView } from "../lib/schedule";

type GridAssignment = AssignmentOut | SnapshotAssignment;

export interface ScheduleGridProps {
  year: number;
  month: number;
  days: string[]; // ISO dates, in order
  roster: RosterEntryOut[]; // pre-sorted BIG then SMALL (one pool for infirmiere)
  assignments: GridAssignment[];
  daysOff: DayOffIn[]; // empty for snapshots
  blockedDays?: DayOffIn[]; // Co-bridged off days; absent for snapshots
  totals: NurseTotalsOut[];
  perDay: DayCoverageOut[]; // empty for snapshots (no coverage rows then)
  holidays: Set<string>;
  staffType?: StaffType; // "infirmiera" -> one band, Z/N/8 coverage
  onCellClick?: (userId: number, date: string) => void; // editing entry point
  activeCell?: { userId: number; date: string } | null;
}

const WEEKDAY_KEYS: Record<number, string> = {
  1: "weekday.mon",
  2: "weekday.tue",
  3: "weekday.wed",
  4: "weekday.thu",
  5: "weekday.fri",
  6: "weekday.sat",
  0: "weekday.sun",
};

export default function ScheduleGrid(props: ScheduleGridProps) {
  const { t } = useTranslation();
  const {
    days,
    roster,
    assignments,
    daysOff,
    blockedDays,
    totals,
    perDay,
    holidays,
    staffType,
    onCellClick,
    activeCell,
  } = props;
  const isAide = staffType === "infirmiera";

  // Fast lookups.
  const asgMap = new Map<string, GridAssignment>();
  for (const a of assignments) asgMap.set(`${a.user_id}|${a.date}`, a);
  const offSet = new Set<string>();
  for (const d of daysOff) offSet.add(`${d.user_id}|${d.date}`);
  const blockedSet = new Set<string>();
  for (const d of blockedDays ?? []) blockedSet.add(`${d.user_id}|${d.date}`);
  const totalsMap = new Map<number, NurseTotalsOut>();
  for (const tt of totals) totalsMap.set(tt.user_id, tt);
  const coverageMap = new Map<string, DayCoverageOut>();
  for (const c of perDay) coverageMap.set(c.date, c);

  // Group the (already sorted) roster by section, preserving order. Infirmiere
  // have no section (null) so they form a single band.
  const groups: { section: string | null; nurses: RosterEntryOut[] }[] = [];
  for (const n of roster) {
    let g = groups[groups.length - 1];
    if (!g || g.section !== n.section) {
      g = { section: n.section, nurses: [] };
      groups.push(g);
    }
    g.nurses.push(n);
  }

  function dayClass(date: string): string {
    if (holidays.has(date)) return "col-holiday"; // rose wins over grey
    if (isWeekend(date)) return "col-weekend";
    return "";
  }

  const showCoverage = perDay.length > 0;
  const totalColSpan = 6; // number of totals columns

  return (
    <div className="grid-scroll">
      <table className="grid">
        <thead>
          <tr>
            <th className="name-col" rowSpan={2}>
              {isAide ? t("staff.infirmiera") : t("grid.nurse")}
            </th>
            {days.map((d) => (
              <th key={d} className={`day-col ${dayClass(d)}`}>
                {dayOf(d)}
              </th>
            ))}
            <th className="tot-col" rowSpan={2} title={t("totals.totalHours")}>
              {t("totals.total")}
            </th>
            <th className="tot-col" rowSpan={2} title={t("totals.weekendHours")}>
              {t("totals.weekend")}
            </th>
            <th className="tot-col" rowSpan={2} title={t("totals.normalHours")}>
              {t("totals.normal")}
            </th>
            <th className="tot-col" rowSpan={2} title={t("totals.leaveTitle")}>
              {t("totals.leave")}
            </th>
            <th className="tot-col" rowSpan={2} title={t("totals.targetTitle")}>
              {t("totals.target")}
            </th>
            <th className="tot-col" rowSpan={2} title={t("totals.nightsTitle")}>
              {t("totals.nights")}
            </th>
          </tr>
          <tr>
            {days.map((d) => {
              const w = new Date(
                Date.UTC(Number(d.slice(0, 4)), Number(d.slice(5, 7)) - 1, Number(d.slice(8, 10))),
              ).getUTCDay();
              return (
                <th key={`wd-${d}`} className={`day-col wd ${dayClass(d)}`}>
                  {t(WEEKDAY_KEYS[w])}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {groups.map((g, gi) => (
            // Key includes the group index: with free ordering the same section
            // may appear as more than one band, so `sec-${section}` alone would
            // collide.
            <Fragment key={`sec-${gi}-${g.section}`}>
              {/* Infirmiere are one ward-wide pool: keep their single band
                  header. Asistente drop the section labels entirely - a single
                  thick rule (below) marks the first section boundary instead. */}
              {isAide && (
                <tr className="section-row">
                  <td className="name-col section-label">{t("staff.infirmierePlural")}</td>
                  <td colSpan={days.length + totalColSpan} className="section-fill" />
                </tr>
              )}
              {g.nurses.map((n, ni) => {
                const tot = totalsMap.get(n.user_id);
                // One thick divider at the first section change (Copii Mari ->
                // Sugari): the second band's first row. Infirmiere have no
                // sections, so this never applies to them.
                const sectionDivider = !isAide && gi === 1 && ni === 0;
                return (
                  <tr key={n.user_id} className={sectionDivider ? "section-divider" : undefined}>
                    <td className="name-col">{n.full_name}</td>
                    {days.map((d) => {
                      const cv = cellView(
                        asgMap.get(`${n.user_id}|${d}`),
                        offSet.has(`${n.user_id}|${d}`),
                        blockedSet.has(`${n.user_id}|${d}`),
                      );
                      const clickable = Boolean(onCellClick) && cv.editable;
                      const isActive =
                        activeCell && activeCell.userId === n.user_id && activeCell.date === d;
                      let title: string | undefined;
                      if (cv.reason === "blocked") title = t("grid.coBridge");
                      return (
                        <td
                          key={d}
                          className={[
                            "cell",
                            cv.cls,
                            dayClass(d),
                            clickable ? "clickable" : "",
                            isActive ? "active-cell" : "",
                          ]
                            .filter(Boolean)
                            .join(" ")}
                          title={title}
                          onClick={clickable ? () => onCellClick?.(n.user_id, d) : undefined}
                        >
                          {cv.text}
                          {cv.locked && <sup className="lock-mark">▪</sup>}
                        </td>
                      );
                    })}
                    {tot ? (
                      <>
                        <td className="tot-col">{tot.total}</td>
                        <td className="tot-col">{tot.worked_weekend}</td>
                        <td className="tot-col">{tot.worked_normal}</td>
                        <td className="tot-col" title={t("totals.leaveCredit", { hours: tot.leave_credit })}>
                          {tot.leave_days}
                        </td>
                        <td className="tot-col">{tot.effective_target}</td>
                        <td className="tot-col">{tot.nights}</td>
                      </>
                    ) : (
                      Array.from({ length: totalColSpan }).map((_, i) => (
                        <td className="tot-col muted" key={`empty-tot-${n.user_id}-${i}`}>
                          —
                        </td>
                      ))
                    )}
                  </tr>
                );
              })}
            </Fragment>
          ))}
          {showCoverage && isAide && (
            <>
              <CoverageRow
                label={t("coverage.day")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.Z ?? 0}
                bad={(v) => v < 1}
                totalColSpan={totalColSpan}
              />
              <CoverageRow
                label={t("coverage.night")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.N ?? 0}
                bad={(v) => v < 1}
                totalColSpan={totalColSpan}
              />
              <CoverageRow
                label={t("coverage.eight")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.E8 ?? 0}
                bad={(v) => v > 1}
                totalColSpan={totalColSpan}
              />
            </>
          )}
          {showCoverage && !isAide && (
            <>
              <CoverageRow
                label={t("coverage.dayBig")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.Z_big ?? 0}
                bad={(v) => v < 1}
                totalColSpan={totalColSpan}
              />
              <CoverageRow
                label={t("coverage.daySmall")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.Z_small ?? 0}
                bad={(v) => v < 1}
                totalColSpan={totalColSpan}
              />
              <CoverageRow
                label={t("coverage.nightBig")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.N_big ?? 0}
                bad={(v) => v < 1}
                totalColSpan={totalColSpan}
              />
              <CoverageRow
                label={t("coverage.nightSmall")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.N_small ?? 0}
                bad={(v) => v < 1}
                totalColSpan={totalColSpan}
              />
              <CoverageRow
                label={t("coverage.kitchen")}
                days={days}
                dayClass={dayClass}
                value={(d) => coverageMap.get(d)?.K ?? 0}
                bad={(v) => v !== 1}
                totalColSpan={totalColSpan}
              />
            </>
          )}
        </tbody>
      </table>
    </div>
  );
}

function CoverageRow({
  label,
  days,
  dayClass,
  value,
  bad,
  totalColSpan,
}: {
  label: string;
  days: string[];
  dayClass: (d: string) => string;
  value: (d: string) => number;
  bad: (v: number) => boolean;
  totalColSpan: number;
}) {
  return (
    <tr className="coverage-row">
      <td className="name-col coverage-label">{label}</td>
      {days.map((d) => {
        const v = value(d);
        return (
          <td key={d} className={`cell ${dayClass(d)} ${bad(v) ? "coverage-bad" : ""}`}>
            {v}
          </td>
        );
      })}
      {Array.from({ length: totalColSpan }).map((_, i) => (
        <td className="tot-col" key={`cov-pad-${label}-${i}`} />
      ))}
    </tr>
  );
}
