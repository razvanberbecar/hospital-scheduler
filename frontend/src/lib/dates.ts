// Date helpers. Everything is ISO "YYYY-MM-DD" strings. Weekday math ALWAYS
// goes through UTC to avoid the local-timezone off-by-one:
//   new Date(Date.UTC(y, m-1, d)).getUTCDay()   NOT new Date("YYYY-MM-DD").getDay()

import i18n from "../i18n";

export function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

export function iso(year: number, month: number, day: number): string {
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

/** Number of days in month (1..12). */
export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** All ISO dates of a month, in order. */
export function monthDates(year: number, month: number): string[] {
  const n = daysInMonth(year, month);
  const out: string[] = [];
  for (let d = 1; d <= n; d++) out.push(iso(year, month, d));
  return out;
}

/** UTC weekday for an ISO date: 0 = Sunday .. 6 = Saturday. */
export function weekdayIndex(isoDate: string): number {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

/** Saturday or Sunday. */
export function isWeekend(isoDate: string): boolean {
  const w = weekdayIndex(isoDate);
  return w === 0 || w === 6;
}

/** Monday..Friday. */
export function isWorkingDay(isoDate: string): boolean {
  return !isWeekend(isoDate);
}

/** Day-of-month number from an ISO date. */
export function dayOf(isoDate: string): number {
  return Number(isoDate.slice(8, 10));
}

/** Count of Mon-Fri days in the month that are not holidays. */
export function workingDays(year: number, month: number, holidays: Set<string>): number {
  return monthDates(year, month).filter((d) => isWorkingDay(d) && !holidays.has(d)).length;
}

/** Suggested target hours = 8 * (Mon-Fri days not on a holiday). */
export function suggestedTarget(year: number, month: number, holidays: Set<string>): number {
  return 8 * workingDays(year, month, holidays);
}

/** Localized "Iulie 2026" using the active i18n language. */
export function monthYearLabel(year: number, month: number): string {
  const name = new Intl.DateTimeFormat(i18n.language, { month: "long" }).format(
    new Date(Date.UTC(year, month - 1, 1)),
  );
  const capital = name.charAt(0).toLocaleUpperCase(i18n.language) + name.slice(1);
  return `${capital} ${year}`;
}

/** Localized date-time for an ISO timestamp (approvals). */
export function dateTimeLabel(isoTimestamp: string): string {
  const d = new Date(isoTimestamp);
  return new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

/** "30.06" short day.month label for a prev_tail chip. */
export function shortDayMonth(isoDate: string): string {
  const [, m, d] = isoDate.split("-");
  return `${d}.${m}`;
}

/** The month after a given (year, month). */
export function nextMonth(year: number, month: number): { year: number; month: number } {
  return month === 12 ? { year: year + 1, month: 1 } : { year, month: month + 1 };
}
