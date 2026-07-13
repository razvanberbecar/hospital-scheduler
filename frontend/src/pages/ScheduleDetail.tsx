import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  approveSchedule,
  deleteSchedule,
  downloadExport,
  generateSchedule,
  getHolidays,
  getSchedule,
  patchAssignments,
  reopenSchedule,
  validateSchedule,
} from "../api/client";
import type {
  AssignmentOut,
  EditShift,
  IssueOut,
  RequestOut,
  ScheduleDetailOut,
  ValidateOut,
} from "../api/types";
import type { ApproveViolationsError, PrechecksError, SolverError } from "../api/types";
import { useUser } from "../context/UserContext";
import { monthDates, monthYearLabel } from "../lib/dates";
import { nameOf, requestKindLabel } from "../lib/schedule";
import Legend from "../components/Legend";
import ScheduleGrid from "../components/ScheduleGrid";
import { Banner, ConfirmDialog, IssueList, StatusBadge } from "../components/ui";

export default function ScheduleDetail() {
  const { t } = useTranslation();
  const { id } = useParams();
  const scheduleId = Number(id);
  const { me } = useUser();
  const isManager = me?.role === "manager_nurse";
  const location = useLocation();
  const redirectNotice = (location.state as { notice?: string } | null)?.notice;

  const [detail, setDetail] = useState<ScheduleDetailOut | null>(null);
  const [holidays, setHolidays] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(
    redirectNotice === "setupOnlyDraft" ? "setupOnlyDraft" : "",
  );
  // Assignments live here (not only inside `detail`) so a cell edit updates
  // both the grid and the action bar's lock awareness without a refetch.
  // Keyed on the server array's identity: applyValidate() spreads the old
  // detail and keeps the same assignments reference, so local upserts survive;
  // a real refetch produces a new array and resets the local copy.
  const serverAssignments = detail ? detail.assignments : null;
  const [assignments, setAssignments] = useState<AssignmentOut[]>([]);
  useEffect(() => {
    setAssignments(serverAssignments ?? []);
  }, [serverAssignments]);

  const load = useCallback(async () => {
    try {
      const d = await getSchedule(scheduleId);
      setDetail(d);
      const hs = await getHolidays(d.year);
      setHolidays(new Set(hs.map((h) => h.date)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [scheduleId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Merge a ValidateOut (from validate / edit) into the current detail without
  // a full refetch: totals/per_day/violations/warnings change, grid stays.
  function applyValidate(v: ValidateOut) {
    setDetail((prev) =>
      prev
        ? {
            ...prev,
            totals: v.totals,
            per_day: v.per_day,
            violations: v.violations,
            warnings: v.warnings,
          }
        : prev,
    );
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

  const roster = detail.setup.roster;
  const canEdit = isManager && detail.status === "draft";

  return (
    <div className="page detail-page">
      <div className="detail-header">
        <h1>
          {t("detail.month")} {monthYearLabel(detail.year, detail.month)}
        </h1>
        <StatusBadge status={detail.status} />
      </div>

      {error && <Banner tone="error" onDismiss={() => setError("")}>{error}</Banner>}
      {notice === "setupOnlyDraft" && (
        <Banner tone="info" onDismiss={() => setNotice("")}>
          {t("detail.setupOnlyDraft")}
        </Banner>
      )}

      {isManager && (
        <ActionBar
          detail={detail}
          assignments={assignments}
          onReload={load}
          onValidate={applyValidate}
          setError={setError}
        />
      )}

      <ScheduleGridEditable
        detail={detail}
        assignments={assignments}
        setAssignments={setAssignments}
        holidays={holidays}
        canEdit={canEdit}
        onValidate={applyValidate}
        setError={setError}
      />

      <Legend />

      {isManager && detail.setup.requests.length > 0 && (
        <RequestsPanel requests={detail.setup.requests} roster={roster} />
      )}

      {isManager && (detail.violations.length > 0 || detail.warnings.length > 0) && (
        <div className="panel">
          <h2>{t("detail.issues")}</h2>
          <IssueList issues={detail.violations} roster={roster} kind="violation" />
          <IssueList issues={detail.warnings} roster={roster} kind="warning" />
        </div>
      )}
    </div>
  );
}

// --- grid + inline cell editor ---------------------------------------------

function ScheduleGridEditable({
  detail,
  assignments,
  setAssignments,
  holidays,
  canEdit,
  onValidate,
  setError,
}: {
  detail: ScheduleDetailOut;
  assignments: AssignmentOut[];
  setAssignments: React.Dispatch<React.SetStateAction<AssignmentOut[]>>;
  holidays: Set<string>;
  canEdit: boolean;
  onValidate: (v: ValidateOut) => void;
  setError: (s: string) => void;
}) {
  const { t } = useTranslation();
  const days = monthDates(detail.year, detail.month);
  const [active, setActive] = useState<{ userId: number; date: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setActive(null);
  }, [detail]);

  const activeAssignment = active
    ? assignments.find((a) => a.user_id === active.userId && a.date === active.date)
    : undefined;
  const activeIsHead = active
    ? detail.setup.roster.find((r) => r.user_id === active.userId)?.role === "manager_nurse"
    : false;

  // keepOpen: the lock-checkbox path re-saves the same cell but leaves the
  // editor open, so the manager can keep working on it.
  async function saveEdit(
    shift: EditShift | null,
    durationHours: number | undefined,
    locked: boolean,
    keepOpen = false,
  ) {
    if (!active) return;
    setSaving(true);
    setError("");
    try {
      const res = await patchAssignments(detail.id, {
        edits: [
          {
            user_id: active.userId,
            date: active.date,
            shift,
            duration_hours: durationHours,
            locked,
          },
        ],
      });
      // Update the cell locally (clear vs upsert).
      setAssignments((prev) => {
        const rest = prev.filter((a) => !(a.user_id === active.userId && a.date === active.date));
        if (shift === null) return rest;
        const dur =
          shift === "T" ? durationHours ?? 4 : shift === "Z" || shift === "N" ? 12 : 8;
        return [
          ...rest,
          {
            user_id: active.userId,
            date: active.date,
            shift,
            duration_hours: dur,
            normal_hours: 0,
            weekend_hours: 0,
            locked,
          },
        ];
      });
      onValidate(res);
      if (!keepOpen) setActive(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <ScheduleGrid
        year={detail.year}
        month={detail.month}
        days={days}
        roster={detail.setup.roster}
        assignments={assignments}
        daysOff={detail.setup.days_off}
        totals={detail.totals}
        perDay={detail.per_day}
        holidays={holidays}
        onCellClick={canEdit ? (userId, date) => setActive({ userId, date }) : undefined}
        activeCell={active}
      />
      {canEdit && active && (
        <CellEditor
          nurseName={nameOf(detail.setup.roster, active.userId)}
          date={active.date}
          current={activeAssignment}
          isHead={activeIsHead}
          saving={saving}
          onSave={saveEdit}
          onClose={() => setActive(null)}
        />
      )}
      {canEdit && !active && <p className="hint">{t("detail.editHint")}</p>}
    </>
  );
}

function CellEditor({
  nurseName,
  date,
  current,
  isHead,
  saving,
  onSave,
  onClose,
}: {
  nurseName: string;
  date: string;
  current: AssignmentOut | undefined;
  isHead: boolean;
  saving: boolean;
  onSave: (
    shift: EditShift | null,
    durationHours: number | undefined,
    locked: boolean,
    keepOpen?: boolean,
  ) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [locked, setLocked] = useState(current?.locked ?? false);

  useEffect(() => {
    setLocked(current?.locked ?? false);
  }, [current]);

  // Toggling the lock on a cell that already has an assignment persists
  // immediately (same shift + duration) and keeps the editor open, so the
  // checkbox alone is enough to lock a cell before the next generate.
  function onToggleLock(next: boolean) {
    setLocked(next);
    if (current) {
      const shift = current.shift as EditShift; // H included; head-only enforced server-side
      const dur = current.shift === "T" ? current.duration_hours : undefined;
      onSave(shift, dur, next, true);
    }
  }

  return (
    <div className="cell-editor">
      <div className="cell-editor-head">
        <strong>{nurseName}</strong>
        <span className="hint">{date}</span>
        <button type="button" className="link-button" onClick={onClose}>
          {t("common.close")}
        </button>
      </div>
      <div className="cell-editor-buttons">
        <button type="button" className="chip" disabled={saving} onClick={() => onSave("Z", undefined, locked)}>
          Z
        </button>
        <button type="button" className="chip" disabled={saving} onClick={() => onSave("N", undefined, locked)}>
          N
        </button>
        <button type="button" className="chip" disabled={saving} onClick={() => onSave("K", undefined, locked)}>
          8 ({t("editor.kitchen")})
        </button>
        {isHead && (
          <button type="button" className="chip" disabled={saving} onClick={() => onSave("H", undefined, locked)}>
            8 ({t("editor.head")})
          </button>
        )}
        <button type="button" className="chip" disabled={saving} onClick={() => onSave("T", 4, locked)}>
          4h
        </button>
        <button type="button" className="chip" disabled={saving} onClick={() => onSave("T", 8, locked)}>
          8h
        </button>
        <button
          type="button"
          className="chip danger"
          disabled={saving}
          onClick={() => onSave(null, undefined, locked)}
        >
          {t("editor.clear")}
        </button>
      </div>
      <label className="checkbox-line">
        <input
          type="checkbox"
          checked={locked}
          disabled={saving}
          onChange={(e) => onToggleLock(e.target.checked)}
        />
        {t("editor.locked")}
      </label>
      {saving && <p className="hint">{t("common.saving")}</p>}
    </div>
  );
}

// --- action bar ------------------------------------------------------------

function ActionBar({
  detail,
  assignments,
  onReload,
  onValidate,
  setError,
}: {
  detail: ScheduleDetailOut;
  assignments: AssignmentOut[];
  onReload: () => Promise<void>;
  onValidate: (v: ValidateOut) => void;
  setError: (s: string) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const anyLocked = assignments.some((a) => a.locked);
  const [respectLocks, setRespectLocks] = useState(anyLocked);
  const [generating, setGenerating] = useState(false);
  const [checking, setChecking] = useState(false);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [solverReport, setSolverReport] = useState<ScheduleDetailOut | null>(null);
  const [genError, setGenError] = useState<PrechecksError | SolverError | string | null>(null);
  const [approveMsg, setApproveMsg] = useState("");
  const [approveErr, setApproveErr] = useState<ApproveViolationsError | string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportErr, setExportErr] = useState("");
  const [confirmReopen, setConfirmReopen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    setRespectLocks(assignments.some((a) => a.locked));
  }, [assignments]);

  async function onExport() {
    setExporting(true);
    setExportErr("");
    try {
      const mm = String(detail.month).padStart(2, "0");
      await downloadExport(detail.id, `GRAFIC_Pediatrie_${detail.year}-${mm}.xlsx`);
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  const roster = detail.setup.roster;

  async function onGenerate() {
    setGenerating(true);
    setGenError(null);
    setSolverReport(null);
    try {
      const res = await generateSchedule(detail.id, respectLocks);
      setSolverReport(res);
      await onReload();
    } catch (e) {
      if (e instanceof ApiError && e.data) setGenError(e.data as PrechecksError | SolverError);
      else setGenError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function onCheck() {
    setChecking(true);
    setError("");
    try {
      const res = await validateSchedule(detail.id);
      onValidate(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(false);
    }
  }

  async function onReopen() {
    setConfirmReopen(false);
    setError("");
    try {
      await reopenSchedule(detail.id);
      await onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onDelete() {
    setConfirmDelete(false);
    setError("");
    try {
      await deleteSchedule(detail.id);
      navigate("/schedules");
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onApprove() {
    setConfirmApprove(false);
    setApproveErr(null);
    setApproveMsg("");
    try {
      const res = await approveSchedule(detail.id);
      setApproveMsg(t("detail.approvedVersion", { version: res.version }));
      await onReload();
    } catch (e) {
      if (e instanceof ApiError && e.data) setApproveErr(e.data as ApproveViolationsError);
      else if (e instanceof ApiError) setApproveErr(e.message);
      else setApproveErr(e instanceof Error ? e.message : String(e));
    }
  }

  const grantedSummary = (() => {
    const reqs = detail.setup.requests;
    if (reqs.length === 0) return null;
    const granted = reqs.filter((r) => r.granted === true).length;
    return t("detail.requestsSummary", { granted, total: reqs.length });
  })();

  return (
    <div className="action-area">
      <div className="action-bar">
        {detail.status === "draft" && (
          <>
            <Link to={`/schedules/${detail.id}/setup`} className="btn-link">
              {t("detail.setup")}
            </Link>
            <label className="checkbox-line inline">
              <input
                type="checkbox"
                checked={respectLocks}
                onChange={(e) => setRespectLocks(e.target.checked)}
              />
              {t("detail.respectLocks")}
            </label>
            <button type="button" disabled={generating} onClick={onGenerate}>
              {generating ? t("detail.generating") : t("detail.generate")}
            </button>
            <button type="button" className="secondary" disabled={checking} onClick={onCheck}>
              {checking ? t("common.working") : t("detail.check")}
            </button>
            <button type="button" onClick={() => setConfirmApprove(true)}>
              {t("detail.approve")}
            </button>
            <button type="button" className="danger" onClick={() => setConfirmDelete(true)}>
              {t("detail.delete")}
            </button>
          </>
        )}
        {detail.status === "approved" && (
          <>
            <button type="button" onClick={() => setConfirmApprove(true)}>
              {t("detail.reapprove")}
            </button>
            <button type="button" className="secondary" onClick={() => setConfirmReopen(true)}>
              {t("detail.reopen")}
            </button>
            <Link to={`/schedules/${detail.id}/history`} className="btn-link">
              {t("detail.history")}
            </Link>
          </>
        )}
        {detail.status === "locked" && (
          <Link to={`/schedules/${detail.id}/history`} className="btn-link">
            {t("detail.history")}
          </Link>
        )}
        <button type="button" className="secondary" disabled={exporting} onClick={onExport}>
          {exporting ? t("detail.exporting") : t("detail.exportExcel")}
        </button>
      </div>

      {exportErr && (
        <Banner tone="error" onDismiss={() => setExportErr("")}>
          {exportErr}
        </Banner>
      )}

      {detail.status === "approved" && <p className="hint">{t("detail.approvedHint")}</p>}
      {detail.status === "locked" && <p className="hint">{t("detail.lockedHint")}</p>}

      {generating && <p className="hint">{t("detail.generatingLong")}</p>}

      {solverReport && (
        <Banner tone="success" onDismiss={() => setSolverReport(null)}>
          <SolverReport report={solverReport} grantedSummary={grantedSummary} />
        </Banner>
      )}

      {genError && (
        <Banner tone="error" onDismiss={() => setGenError(null)}>
          <GenerateError error={genError} roster={roster} />
        </Banner>
      )}

      {approveMsg && (
        <Banner tone="success" onDismiss={() => setApproveMsg("")}>
          {approveMsg}
        </Banner>
      )}

      {approveErr && (
        <Banner tone="error" onDismiss={() => setApproveErr(null)}>
          {typeof approveErr === "string" ? (
            approveErr
          ) : (
            <>
              <p>{approveErr.message}</p>
              <IssueList issues={approveErr.violations} roster={roster} kind="violation" />
            </>
          )}
        </Banner>
      )}

      {confirmApprove && (
        <ConfirmDialog
          title={t("detail.approveConfirmTitle")}
          message={t("detail.approveConfirmBody")}
          extra={
            detail.warnings.length > 0 ? (
              <p className="hint">{t("detail.approveWarnCount", { count: detail.warnings.length })}</p>
            ) : undefined
          }
          confirmLabel={t("detail.approve")}
          onConfirm={onApprove}
          onCancel={() => setConfirmApprove(false)}
        />
      )}

      {confirmReopen && (
        <ConfirmDialog
          title={t("detail.reopenConfirmTitle")}
          message={t("detail.reopenConfirmBody")}
          confirmLabel={t("detail.reopen")}
          onConfirm={onReopen}
          onCancel={() => setConfirmReopen(false)}
        />
      )}

      {confirmDelete && (
        <ConfirmDialog
          title={t("detail.deleteConfirmTitle")}
          message={t("detail.deleteConfirmBody")}
          extra={
            detail.approval_count > 0 ? (
              <p className="hint">{t("detail.deleteApprovalsWarn")}</p>
            ) : undefined
          }
          confirmLabel={t("detail.delete")}
          onConfirm={onDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

function SolverReport({
  report,
  grantedSummary,
}: {
  report: ScheduleDetailOut;
  grantedSummary: string | null;
}) {
  const { t } = useTranslation();
  const roster = report.setup.roster;
  const relaxed = report.relaxed_nights ?? {};
  const relaxedEntries = Object.entries(relaxed).filter(([, n]) => n > 0);
  return (
    <div className="solver-report">
      <p>
        <strong>{t("detail.solverStatus")}:</strong> {report.solver_status}
        {report.objective != null && ` · ${t("detail.objective")}: ${report.objective}`}
      </p>
      {grantedSummary && <p>{grantedSummary}</p>}
      {relaxedEntries.length > 0 && (
        <p>
          {t("detail.relaxedNights")}:{" "}
          {relaxedEntries
            .map(([uid, n]) => `${nameOf(roster, Number(uid))} (${n})`)
            .join(", ")}
        </p>
      )}
    </div>
  );
}

function GenerateError({
  error,
  roster,
}: {
  error: PrechecksError | SolverError | string;
  roster: ScheduleDetailOut["setup"]["roster"];
}) {
  const { t } = useTranslation();
  if (typeof error === "string") return <p>{error}</p>;
  if ("prechecks" in error) {
    return (
      <>
        <p>{t("detail.prechecksFailed")}</p>
        <IssueList issues={error.prechecks as IssueOut[]} roster={roster} kind="violation" />
      </>
    );
  }
  return (
    <>
      <p>
        {t("detail.solverFailed")} ({error.solver_status})
      </p>
      <ul className="issue-list issue-violation">
        {error.diagnosis.map((d, idx) => (
          <li key={`diag-${idx}`}>{d}</li>
        ))}
      </ul>
    </>
  );
}

// --- requests panel --------------------------------------------------------

function RequestsPanel({
  requests,
  roster,
}: {
  requests: RequestOut[];
  roster: ScheduleDetailOut["setup"]["roster"];
}) {
  const { t } = useTranslation();
  return (
    <div className="panel">
      <h2>{t("detail.requests")}</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>{t("grid.nurse")}</th>
            <th>{t("request.date")}</th>
            <th>{t("request.kind")}</th>
            <th>{t("request.hard")}</th>
            <th>{t("request.granted")}</th>
          </tr>
        </thead>
        <tbody>
          {requests.map((r, idx) => (
            <tr key={`${r.user_id}-${r.date}-${idx}`}>
              <td>{nameOf(roster, r.user_id)}</td>
              <td>{r.date}</td>
              <td>{requestKindLabel(r.kind, t)}</td>
              <td>{r.hard ? t("request.mandatory") : "—"}</td>
              <td>
                {r.granted === true
                  ? t("request.grantedYes")
                  : r.granted === false
                    ? t("request.grantedNo")
                    : t("request.grantedPending")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
