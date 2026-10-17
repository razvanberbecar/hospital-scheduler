// Shared API interfaces mirroring backend/app/models/schemas.py.
// NOTE on JSON key gotchas: prev_tail, snapshot.totals and relaxed_nights
// arrive with STRING keys (user ids as strings). They are typed as
// Record<string, ...> and converted deliberately.

export type Role = "nurse" | "manager_nurse";
export type Section = "BIG" | "SMALL";
export type StaffType = "asistenta" | "infirmiera";
export type ScheduleStatus = "draft" | "approved" | "locked";
// "8" requests the 8h day shift (the daily slot for infirmiere; a request-only
// 8h shift for asistente). "K" (kitchen) stays asistente-only.
export type RequestKind = "Z" | "N" | "K" | "8" | "FREE";
// H is head-only; "8" is infirmiere-only (the 8h daytime shift).
// "Co" is not a shift: it upserts a leave day rather than an assignment, but the
// grid edits it through the same cell editor as any shift.
export type EditShift = "Z" | "N" | "K" | "T" | "H" | "8" | "Co";
// The shift codes that can appear in an assignment row (H is auto-managed).
export type AssignmentShift = "Z" | "N" | "K" | "H" | "T" | "8";

export interface UserOut {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  staff_type: StaffType;
  section: Section | null; // null for infirmiere (one ward-wide pool)
  default_kitchen_capable: boolean;
  active: boolean;
  must_change_password: boolean;
  position: number;
  created_at: string;
}

export interface UserCreate {
  username: string;
  full_name: string;
  role: Role;
  staff_type: StaffType;
  section: Section | null;
  default_kitchen_capable: boolean;
  temporary_password?: string;
}

export interface UserCreatedOut {
  user: UserOut;
  temporary_password: string;
}

export interface UserUpdate {
  full_name?: string;
  role?: Role;
  section?: Section | null;
  default_kitchen_capable?: boolean;
  active?: boolean;
  // staff_type is immutable after creation and is deliberately absent here.
}

export interface ResetPasswordOut {
  temporary_password: string;
}

export interface HolidayItem {
  date: string; // ISO YYYY-MM-DD
  name: string;
}

// --- schedules -------------------------------------------------------------

export interface RosterItemIn {
  user_id: number;
  kitchen_capable: boolean | null; // null -> use the user's default
}

export interface DayOffIn {
  user_id: number;
  date: string;
}

export interface RequestIn {
  user_id: number;
  date: string;
  kind: RequestKind;
  hard: boolean;
}

export interface ScheduleCreate {
  year: number;
  month: number;
  staff_type: StaffType;
  target_hours: number;
  roster: RosterItemIn[];
  days_off: DayOffIn[];
  requests: RequestIn[];
  config: Record<string, unknown>;
}

export interface ScheduleSetupPut {
  target_hours: number;
  roster: RosterItemIn[];
  days_off: DayOffIn[];
  requests: RequestIn[];
  prev_tail: Record<string, Record<string, string>>; // userId -> date -> code
  config: Record<string, unknown>;
}

export interface GenerateIn {
  respect_locks: boolean;
}

export interface AssignmentEdit {
  user_id: number;
  date: string;
  shift: EditShift | null; // null clears the cell
  duration_hours?: number; // top-ups only (4|8)
  locked: boolean;
}

export interface AssignmentsPatch {
  edits: AssignmentEdit[];
}

export interface ScheduleListItem {
  id: number;
  year: number;
  month: number;
  staff_type: StaffType;
  status: ScheduleStatus;
  target_hours: number;
  approval_count: number;
}

export interface RosterEntryOut {
  user_id: number;
  full_name: string;
  section: Section | null; // null for infirmiere
  role: Role;
  kitchen_capable: boolean;
}

export interface RequestOut {
  user_id: number;
  date: string;
  kind: RequestKind;
  hard: boolean;
  granted: boolean | null;
}

export interface SetupOut {
  target_hours: number;
  roster: RosterEntryOut[]; // in the manager's display order (users.position)
  days_off: DayOffIn[];
  // Derived server-side: off days a nurse's own Co brackets on both sides. She
  // is away across them and cannot be worked, but they are not Co and earn no
  // credit. Shown greyed so an empty weekend explains itself.
  blocked_days: DayOffIn[];
  requests: RequestOut[];
  prev_tail: Record<string, Record<string, string>>;
  config: Record<string, unknown>;
}

export interface AssignmentOut {
  user_id: number;
  date: string;
  shift: AssignmentShift;
  duration_hours: number;
  normal_hours: number;
  weekend_hours: number;
  locked: boolean;
}

export interface NurseTotalsOut {
  user_id: number;
  worked_normal: number;
  worked_weekend: number;
  leave_days: number;
  leave_credit: number;
  worked: number;
  total: number;
  effective_target: number;
  owed: number;
  nights: number;
  kitchens: number;
  topup_hours: number;
}

export interface IssueOut {
  level: string;
  code: string;
  message: string;
  user_id: number | null;
  date: string | null;
}

export interface DayCoverageOut {
  // All optional: asistente report Z_big/Z_small/N_big/N_small/K; infirmiere Z/N/E8.
  date: string;
  Z_big?: number | null;
  Z_small?: number | null;
  N_big?: number | null;
  N_small?: number | null;
  K?: number | null;
  Z?: number | null;
  N?: number | null;
  E8?: number | null;
}

export interface ScheduleDetailOut {
  id: number;
  year: number;
  month: number;
  staff_type: StaffType;
  status: ScheduleStatus;
  setup: SetupOut;
  assignments: AssignmentOut[];
  totals: NurseTotalsOut[];
  per_day: DayCoverageOut[];
  violations: IssueOut[];
  warnings: IssueOut[];
  approval_count: number;
  // solver echo, present only on the /generate response
  solver_status?: string;
  objective?: number;
  relaxed_nights?: Record<string, number>; // string user ids
}

export interface ValidateOut {
  ok: boolean;
  violations: IssueOut[];
  warnings: IssueOut[];
  totals: NurseTotalsOut[];
  per_day: DayCoverageOut[];
}

export interface ApprovalOut {
  schedule_id: number;
  version: number;
  approved_by: number;
  approved_at: string;
}

export interface ApprovalListItem {
  version: number;
  approved_by: number;
  approved_at: string;
}

export interface SnapshotTotals {
  worked_normal: number;
  worked_weekend: number;
  leave_days: number;
  total: number;
  effective_target: number;
  owed: number;
  nights: number;
  kitchens: number;
}

export interface SnapshotAssignment {
  user_id: number;
  date: string;
  shift: AssignmentShift;
  duration_hours: number;
  normal_hours: number;
  weekend_hours: number;
}

export interface ApprovalSnapshot {
  assignments: SnapshotAssignment[];
  totals: Record<string, SnapshotTotals>; // string user ids
  overridden_violations?: IssueOut[]; // present (possibly empty) on force-approved months
}

export interface ApprovalSnapshotOut {
  version: number;
  approved_by: number;
  approved_at: string;
  snapshot: ApprovalSnapshot;
}

// Shapes of the structured 409 error bodies from generate/approve.
export interface PrechecksError {
  prechecks: IssueOut[];
}

export interface SolverError {
  solver_status: string;
  diagnosis: string[];
}

export interface ApproveViolationsError {
  message: string;
  violations: IssueOut[];
}
