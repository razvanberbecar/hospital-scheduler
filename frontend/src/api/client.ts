import type {
  ApprovalListItem,
  ApprovalOut,
  ApprovalSnapshotOut,
  AssignmentsPatch,
  HolidayItem,
  ResetPasswordOut,
  ScheduleCreate,
  ScheduleDetailOut,
  ScheduleListItem,
  ScheduleSetupPut,
  UserCreate,
  UserCreatedOut,
  UserOut,
  UserUpdate,
  ValidateOut,
} from "./types";

const ACCESS_KEY = "hs_access";
const REFRESH_KEY = "hs_refresh";

export interface Me {
  id: number;
  username: string;
  full_name: string;
  role: "nurse" | "manager_nurse";
  staff_type: "asistenta" | "infirmiera";
  section: "BIG" | "SMALL" | null;
  default_kitchen_capable: boolean;
  active: boolean;
  must_change_password: boolean;
}

export interface LoginResult {
  access_token: string;
  refresh_token: string;
  must_change_password: boolean;
}

export function tokens() {
  return {
    access: localStorage.getItem(ACCESS_KEY),
    refresh: localStorage.getItem(REFRESH_KEY),
  };
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

function storeTokens(access: string, refresh?: string) {
  localStorage.setItem(ACCESS_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}

export class ApiError extends Error {
  // `data` holds the parsed `detail` when it is an object (structured 409s from
  // generate/approve). When `detail` is a string it becomes `message` instead.
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const { access } = tokens();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (access) headers.Authorization = `Bearer ${access}`;
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401 && retry && tokens().refresh) {
    const refreshed = await tryRefresh();
    if (refreshed) return request<T>(path, init, false);
    clearTokens();
  }
  if (!res.ok) {
    let detail = res.statusText;
    let data: unknown;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (body.detail !== undefined && body.detail !== null) {
        // Structured 409 (prechecks / solver diagnosis / approve violations):
        // keep the object in `data`, give a generic message.
        data = body.detail;
        detail = "request failed";
      }
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail, data);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function tryRefresh(): Promise<boolean> {
  const { refresh } = tokens();
  if (!refresh) return false;
  const res = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) return false;
  const body = (await res.json()) as { access_token: string };
  storeTokens(body.access_token);
  return true;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new ApiError(res.status, "login failed");
  const body = (await res.json()) as LoginResult;
  storeTokens(body.access_token, body.refresh_token);
  return body;
}

export function changePassword(currentPassword: string, newPassword: string): Promise<Me> {
  return request<Me>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function me(): Promise<Me> {
  return request<Me>("/api/auth/me");
}

// --- users (manager only) --------------------------------------------------

export function listUsers(): Promise<UserOut[]> {
  return request<UserOut[]>("/api/users");
}

export function createUser(body: UserCreate): Promise<UserCreatedOut> {
  return request<UserCreatedOut>("/api/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateUser(id: number, body: UserUpdate): Promise<UserOut> {
  return request<UserOut>(`/api/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function reorderUsers(orderedIds: number[]): Promise<UserOut[]> {
  return request<UserOut[]>("/api/users/order", {
    method: "PUT",
    body: JSON.stringify({ ordered_ids: orderedIds }),
  });
}

export function resetUserPassword(id: number): Promise<ResetPasswordOut> {
  return request<ResetPasswordOut>(`/api/users/${id}/reset-password`, {
    method: "POST",
  });
}

export function deleteUser(id: number): Promise<void> {
  return request<void>(`/api/users/${id}`, { method: "DELETE" });
}

// --- holidays --------------------------------------------------------------

export function getHolidays(year: number): Promise<HolidayItem[]> {
  return request<HolidayItem[]>(`/api/holidays?year=${year}`);
}

export function putHolidays(year: number, holidays: HolidayItem[]): Promise<HolidayItem[]> {
  return request<HolidayItem[]>("/api/holidays", {
    method: "PUT",
    body: JSON.stringify({ year, holidays }),
  });
}

// --- schedules -------------------------------------------------------------

export function createSchedule(body: ScheduleCreate): Promise<ScheduleDetailOut> {
  return request<ScheduleDetailOut>("/api/schedules", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listSchedules(year?: number): Promise<ScheduleListItem[]> {
  const q = year !== undefined ? `?year=${year}` : "";
  return request<ScheduleListItem[]>(`/api/schedules${q}`);
}

export function getSchedule(id: number): Promise<ScheduleDetailOut> {
  return request<ScheduleDetailOut>(`/api/schedules/${id}`);
}

export function putSetup(id: number, body: ScheduleSetupPut): Promise<ScheduleDetailOut> {
  return request<ScheduleDetailOut>(`/api/schedules/${id}/setup`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function generateSchedule(id: number, respectLocks: boolean): Promise<ScheduleDetailOut> {
  return request<ScheduleDetailOut>(`/api/schedules/${id}/generate`, {
    method: "POST",
    body: JSON.stringify({ respect_locks: respectLocks }),
  });
}

export function validateSchedule(id: number): Promise<ValidateOut> {
  return request<ValidateOut>(`/api/schedules/${id}/validate`, { method: "POST" });
}

export function patchAssignments(id: number, body: AssignmentsPatch): Promise<ValidateOut> {
  return request<ValidateOut>(`/api/schedules/${id}/assignments`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function approveSchedule(id: number): Promise<ApprovalOut> {
  return request<ApprovalOut>(`/api/schedules/${id}/approve`, { method: "POST" });
}

export function reopenSchedule(id: number): Promise<ScheduleDetailOut> {
  return request<ScheduleDetailOut>(`/api/schedules/${id}/reopen`, { method: "POST" });
}

export function deleteSchedule(id: number): Promise<void> {
  return request<void>(`/api/schedules/${id}`, { method: "DELETE" });
}

export function listApprovals(id: number): Promise<ApprovalListItem[]> {
  return request<ApprovalListItem[]>(`/api/schedules/${id}/approvals`);
}

export function getApprovalSnapshot(id: number, version: number): Promise<ApprovalSnapshotOut> {
  return request<ApprovalSnapshotOut>(`/api/schedules/${id}/approvals/${version}`);
}

/**
 * Download the GRAFIC .xlsx for a schedule. A plain <a href> cannot carry the
 * Bearer token, so we fetch the blob with auth (one 401-refresh retry, like
 * request()) and trigger the download via a temporary object-URL anchor.
 */
export async function downloadExport(id: number, filename: string): Promise<void> {
  const fetchBlob = async (): Promise<Response> => {
    const { access } = tokens();
    const headers: Record<string, string> = {};
    if (access) headers.Authorization = `Bearer ${access}`;
    return fetch(`/api/schedules/${id}/export.xlsx`, { headers });
  };

  let res = await fetchBlob();
  if (res.status === 401 && tokens().refresh) {
    if (await tryRefresh()) res = await fetchBlob();
    else clearTokens();
  }
  if (!res.ok) throw new ApiError(res.status, "export failed");

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
