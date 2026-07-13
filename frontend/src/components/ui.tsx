import { useTranslation } from "react-i18next";

import type { IssueOut, RosterEntryOut } from "../api/types";
import { issueText, statusLabel } from "../lib/schedule";

/** Coloured status badge. */
export function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  return <span className={`badge badge-${status}`}>{statusLabel(status, t)}</span>;
}

/** A list of issues (violations or warnings) with nurse name + date prefix. */
export function IssueList({
  issues,
  roster,
  kind,
}: {
  issues: IssueOut[];
  roster: RosterEntryOut[];
  kind: "violation" | "warning";
}) {
  if (issues.length === 0) return null;
  return (
    <ul className={`issue-list issue-${kind}`}>
      {issues.map((i, idx) => (
        <li key={`${i.code}-${i.user_id}-${i.date}-${idx}`}>{issueText(i, roster)}</li>
      ))}
    </ul>
  );
}

/** A blocking confirm dialog. */
export function ConfirmDialog({
  title,
  message,
  extra,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  extra?: React.ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p>{message}</p>
        {extra}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button type="button" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** A one-time secret box (temporary passwords shown once). */
export function OneTimeSecret({ label, secret }: { label: string; secret: string }) {
  return (
    <div className="secret-box">
      <div className="secret-label">{label}</div>
      <code className="secret-value">{secret}</code>
    </div>
  );
}

/** A dismissible inline banner. */
export function Banner({
  tone,
  children,
  onDismiss,
}: {
  tone: "info" | "success" | "error";
  children: React.ReactNode;
  onDismiss?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={`banner banner-${tone}`}>
      <div className="banner-body">{children}</div>
      {onDismiss && (
        <button type="button" className="link-button banner-dismiss" onClick={onDismiss}>
          {t("common.dismiss")}
        </button>
      )}
    </div>
  );
}
