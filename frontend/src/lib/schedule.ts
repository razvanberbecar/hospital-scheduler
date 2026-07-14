// Domain-level helpers shared by the schedule screens: cell rendering rules,
// issue-message prefixing, and small lookups.

import type { TFunction } from "i18next";

import type {
  AssignmentOut,
  AssignmentShift,
  IssueOut,
  RosterEntryOut,
  SnapshotAssignment,
} from "../api/types";

/** What a single grid cell shows and how it is coloured. */
export interface CellView {
  text: string;
  cls: string; // "" | "c-z" | "c-n" | "c-k" | "c-h" | "c-t" | "c-co"
  locked: boolean;
  editable: boolean; // false only for Co; blank cells accept a new shift
  reason: "assignment" | "co" | "empty";
  shift: AssignmentShift | null;
  duration: number | null;
}

const EMPTY_CELL: CellView = {
  text: "",
  cls: "",
  locked: false,
  editable: true,
  reason: "empty",
  shift: null,
  duration: null,
};

/**
 * Resolve the display for one nurse/day.
 * Priority: an assignment wins; else a days_off entry renders "Co"; else blank.
 * Text: Z->"Z", N->"N", K->"8", H->"8", T->its duration ("4"/"8"), Co->"Co".
 */
export function cellView(
  assignment: AssignmentOut | SnapshotAssignment | undefined,
  isDayOff: boolean,
): CellView {
  if (assignment) {
    const shift = assignment.shift;
    const locked = "locked" in assignment ? assignment.locked : false;
    let text: string;
    let cls: string;
    switch (shift) {
      case "Z":
        text = "Z";
        cls = "c-z";
        break;
      case "N":
        text = "N";
        cls = "c-n";
        break;
      case "K":
        text = "8";
        cls = "c-k";
        break;
      case "8":
        text = "8";
        cls = "c-k"; // infirmiere 8h daytime slot, shares the 8h swatch
        break;
      case "H":
        text = "8";
        cls = "c-h";
        break;
      case "T":
        text = String(assignment.duration_hours);
        cls = "c-t";
        break;
      default:
        text = shift;
        cls = "";
    }
    return {
      text,
      cls,
      locked,
      editable: true,
      reason: "assignment",
      shift,
      duration: assignment.duration_hours,
    };
  }
  if (isDayOff) {
    return { ...EMPTY_CELL, text: "Co", cls: "c-co", reason: "co", editable: false };
  }
  return EMPTY_CELL;
}

/** Nurse full name for a user id, resolved via the roster. */
export function nameOf(roster: RosterEntryOut[], userId: number | null | undefined): string {
  if (userId == null) return "";
  const found = roster.find((r) => r.user_id === userId);
  return found ? found.full_name : `#${userId}`;
}

/**
 * An issue's message prefixed with the nurse name and date when present.
 * Messages themselves are English tech-speak, displayed as-is (v1).
 */
export function issueText(issue: IssueOut, roster: RosterEntryOut[]): string {
  const bits: string[] = [];
  if (issue.user_id != null) {
    const name = nameOf(roster, issue.user_id);
    // Backend messages often already lead with the nurse's name.
    if (!issue.message.startsWith(name)) bits.push(name);
  }
  if (issue.date) bits.push(issue.date);
  const prefix = bits.join(" · ");
  return prefix ? `${prefix}: ${issue.message}` : issue.message;
}

/** Localized section label. */
export function sectionLabel(section: string, t: TFunction): string {
  return section === "BIG" ? t("section.big") : t("section.small");
}

/** Localized staff-type label (singular). */
export function staffTypeLabel(staffType: string, t: TFunction): string {
  return staffType === "infirmiera" ? t("staff.infirmiera") : t("staff.asistenta");
}

/** Localized staff-type label (plural, for tabs/headings). */
export function staffTypePlural(staffType: string, t: TFunction): string {
  return staffType === "infirmiera" ? t("staff.infirmierePlural") : t("staff.asistentePlural");
}

/** Localized role label. */
export function roleLabel(role: string, t: TFunction): string {
  return role === "manager_nurse" ? t("role.manager") : t("role.nurse");
}

/** Localized status badge label. */
export function statusLabel(status: string, t: TFunction): string {
  switch (status) {
    case "draft":
      return t("status.draft");
    case "approved":
      return t("status.approved");
    case "locked":
      return t("status.locked");
    default:
      return status;
  }
}

/** Localized request-kind label. */
export function requestKindLabel(kind: string, t: TFunction): string {
  switch (kind) {
    case "Z":
      return t("request.kindDay");
    case "N":
      return t("request.kindNight");
    case "K":
      return t("request.kindKitchen");
    case "FREE":
      return t("request.kindFree");
    default:
      return kind;
  }
}
