import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  createUser,
  deleteUser,
  listUsers,
  reorderUsers,
  resetUserPassword,
  updateUser,
} from "../api/client";
import type { Role, Section, UserOut } from "../api/types";
import { roleLabel, sectionLabel } from "../lib/schedule";
import { Banner, ConfirmDialog, OneTimeSecret } from "../components/ui";

interface EditState {
  full_name: string;
  section: Section;
  role: Role;
  default_kitchen_capable: boolean;
  active: boolean;
}

export default function Accounts() {
  const { t } = useTranslation();
  const [users, setUsers] = useState<UserOut[] | null>(null);
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editState, setEditState] = useState<EditState | null>(null);
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(null);
  const [confirmReset, setConfirmReset] = useState<UserOut | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<UserOut | null>(null);
  const [reordering, setReordering] = useState(false);

  async function load() {
    try {
      setUsers(await listUsers());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function startEdit(u: UserOut) {
    setEditingId(u.id);
    setEditState({
      full_name: u.full_name,
      section: u.section,
      role: u.role,
      default_kitchen_capable: u.default_kitchen_capable,
      active: u.active,
    });
  }

  async function saveEdit(id: number) {
    if (!editState) return;
    setError("");
    try {
      await updateUser(id, editState);
      setEditingId(null);
      setEditState(null);
      await load();
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function doReset(u: UserOut) {
    setConfirmReset(null);
    setError("");
    try {
      const res = await resetUserPassword(u.id);
      setSecret({
        label: t("accounts.resetDone", { name: u.full_name }),
        value: res.temporary_password,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function doDelete(u: UserOut) {
    setConfirmDelete(null);
    setError("");
    try {
      await deleteUser(u.id);
      await load();
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    }
  }

  // Swap the row at `index` with its neighbour (dir -1 up / +1 down) and PUT the
  // full new order. Sends ALL ids so the endpoint's exactly-once guard passes.
  async function moveUser(index: number, dir: -1 | 1) {
    if (!users) return;
    const target = index + dir;
    if (target < 0 || target >= users.length) return;
    const next = [...users];
    [next[index], next[target]] = [next[target], next[index]];
    setError("");
    setReordering(true);
    try {
      setUsers(await reorderUsers(next.map((u) => u.id)));
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
      await load(); // resync on failure
    } finally {
      setReordering(false);
    }
  }

  return (
    <div className="page">
      <h1>{t("accounts.title")}</h1>
      <p className="hint">{t("accounts.orderHint")}</p>
      {error && <Banner tone="error" onDismiss={() => setError("")}>{error}</Banner>}

      {secret && (
        <Banner tone="success" onDismiss={() => setSecret(null)}>
          <OneTimeSecret label={secret.label} secret={secret.value} />
          <p className="hint">{t("accounts.secretHint")}</p>
        </Banner>
      )}

      <NewAccountForm
        onCreated={(label, value) => {
          setSecret({ label, value });
          void load();
        }}
        onError={setError}
      />

      {users === null ? (
        <p className="hint">{t("common.loading")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("accounts.order")}</th>
              <th>{t("grid.nurse")}</th>
              <th>{t("accounts.username")}</th>
              <th>{t("accounts.section")}</th>
              <th>{t("accounts.role")}</th>
              <th>{t("setup.kitchen")}</th>
              <th>{t("accounts.active")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u, index) =>
              editingId === u.id && editState ? (
                <tr key={u.id} className="editing-row">
                  <td />
                  <td>
                    <input
                      value={editState.full_name}
                      onChange={(e) =>
                        setEditState({ ...editState, full_name: e.target.value })
                      }
                    />
                  </td>
                  <td>{u.username}</td>
                  <td>
                    <select
                      value={editState.section}
                      onChange={(e) =>
                        setEditState({ ...editState, section: e.target.value as Section })
                      }
                    >
                      <option value="BIG">{t("section.big")}</option>
                      <option value="SMALL">{t("section.small")}</option>
                    </select>
                  </td>
                  <td>
                    <select
                      value={editState.role}
                      onChange={(e) =>
                        setEditState({ ...editState, role: e.target.value as Role })
                      }
                    >
                      <option value="nurse">{t("role.nurse")}</option>
                      <option value="manager_nurse">{t("role.manager")}</option>
                    </select>
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={editState.default_kitchen_capable}
                      onChange={(e) =>
                        setEditState({ ...editState, default_kitchen_capable: e.target.checked })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={editState.active}
                      onChange={(e) => setEditState({ ...editState, active: e.target.checked })}
                    />
                  </td>
                  <td className="row-actions">
                    <button type="button" className="secondary small" onClick={() => saveEdit(u.id)}>
                      {t("common.save")}
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => {
                        setEditingId(null);
                        setEditState(null);
                      }}
                    >
                      {t("common.cancel")}
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={u.id} className={u.active ? "" : "muted"}>
                  <td className="order-cell">
                    <button
                      type="button"
                      className="link-button"
                      title={t("accounts.moveUp")}
                      aria-label={t("accounts.moveUp")}
                      disabled={index === 0 || reordering}
                      onClick={() => moveUser(index, -1)}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      title={t("accounts.moveDown")}
                      aria-label={t("accounts.moveDown")}
                      disabled={index === users.length - 1 || reordering}
                      onClick={() => moveUser(index, 1)}
                    >
                      ▼
                    </button>
                  </td>
                  <td>{u.full_name}</td>
                  <td>{u.username}</td>
                  <td>{sectionLabel(u.section, t)}</td>
                  <td>{roleLabel(u.role, t)}</td>
                  <td>{u.default_kitchen_capable ? "✓" : "—"}</td>
                  <td>
                    {u.active ? (
                      <span className="badge badge-approved">{t("accounts.activeYes")}</span>
                    ) : (
                      <span className="badge badge-locked">{t("accounts.activeNo")}</span>
                    )}
                  </td>
                  <td className="row-actions">
                    <button type="button" className="link-button" onClick={() => startEdit(u)}>
                      {t("common.edit")}
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setConfirmReset(u)}
                    >
                      {t("accounts.resetPassword")}
                    </button>
                    <button
                      type="button"
                      className="link-button danger"
                      onClick={() => setConfirmDelete(u)}
                    >
                      {t("common.delete")}
                    </button>
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      )}

      {confirmReset && (
        <ConfirmDialog
          title={t("accounts.resetPassword")}
          message={t("accounts.resetConfirm", { name: confirmReset.full_name })}
          confirmLabel={t("accounts.resetPassword")}
          onConfirm={() => doReset(confirmReset)}
          onCancel={() => setConfirmReset(null)}
        />
      )}

      {confirmDelete && (
        <ConfirmDialog
          title={t("accounts.deleteTitle")}
          message={t("accounts.deleteConfirm", { name: confirmDelete.full_name })}
          confirmLabel={t("common.delete")}
          onConfirm={() => doDelete(confirmDelete)}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
    </div>
  );
}

function NewAccountForm({
  onCreated,
  onError,
}: {
  onCreated: (label: string, value: string) => void;
  onError: (s: string) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [section, setSection] = useState<Section>("BIG");
  const [role, setRole] = useState<Role>("nurse");
  const [kitchen, setKitchen] = useState(false);
  const [tempPassword, setTempPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    onError("");
    setBusy(true);
    try {
      const res = await createUser({
        username: username.trim(),
        full_name: fullName.trim(),
        section,
        role,
        default_kitchen_capable: kitchen,
        temporary_password: tempPassword.trim() === "" ? undefined : tempPassword.trim(),
      });
      onCreated(t("accounts.createDone", { name: res.user.full_name }), res.temporary_password);
      setUsername("");
      setFullName("");
      setSection("BIG");
      setRole("nurse");
      setKitchen(false);
      setTempPassword("");
    } catch (e2) {
      if (e2 instanceof ApiError) onError(e2.message);
      else onError(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card-inline" onSubmit={onSubmit}>
      <h2>{t("accounts.newTitle")}</h2>
      <div className="form-row wrap">
        <div className="field">
          <label htmlFor="na-username">{t("accounts.username")}</label>
          <input
            id="na-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={3}
          />
        </div>
        <div className="field">
          <label htmlFor="na-fullname">{t("grid.nurse")}</label>
          <input
            id="na-fullname"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            required
            minLength={2}
          />
        </div>
        <div className="field">
          <label htmlFor="na-section">{t("accounts.section")}</label>
          <select
            id="na-section"
            value={section}
            onChange={(e) => setSection(e.target.value as Section)}
          >
            <option value="BIG">{t("section.big")}</option>
            <option value="SMALL">{t("section.small")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="na-role">{t("accounts.role")}</label>
          <select id="na-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="nurse">{t("role.nurse")}</option>
            <option value="manager_nurse">{t("role.manager")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="na-temp">{t("accounts.tempPassword")}</label>
          <input
            id="na-temp"
            value={tempPassword}
            onChange={(e) => setTempPassword(e.target.value)}
            minLength={tempPassword ? 8 : undefined}
            placeholder={t("accounts.tempPlaceholder")}
          />
        </div>
        <label className="checkbox-line field">
          <input type="checkbox" checked={kitchen} onChange={(e) => setKitchen(e.target.checked)} />
          {t("setup.kitchen")}
        </label>
        <div className="field field-action">
          <button type="submit" disabled={busy}>
            {busy ? t("common.working") : t("accounts.create")}
          </button>
        </div>
      </div>
    </form>
  );
}
