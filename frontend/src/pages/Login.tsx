import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { changePassword, login } from "../api/client";
import LangSwitcher from "../components/LangSwitcher";

export default function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mustChange, setMustChange] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");

  async function onLogin(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const result = await login(username.trim(), password);
      if (result.must_change_password) {
        setMustChange(true);
      } else {
        navigate("/");
      }
    } catch {
      setError(t("login.error"));
    }
  }

  async function onChangePassword(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (newPassword !== confirm) {
      setError(t("login.mismatch"));
      return;
    }
    try {
      await changePassword(password, newPassword);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (mustChange) {
    return (
      <>
      <LangSwitcher />
      <form className="card" onSubmit={onChangePassword}>
        <h1>{t("login.changeTitle")}</h1>
        <p className="hint">{t("login.changeHint")}</p>
        <label htmlFor="new-password">{t("login.newPassword")}</label>
        <input
          id="new-password"
          type="password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          minLength={8}
          required
        />
        <label htmlFor="confirm-password">{t("login.confirmPassword")}</label>
        <input
          id="confirm-password"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          minLength={8}
          required
        />
        {error && <p className="error">{error}</p>}
        <button type="submit">{t("login.change")}</button>
      </form>
      </>
    );
  }

  return (
    <>
    <LangSwitcher />
    <form className="card" onSubmit={onLogin}>
      <h1>{t("app.title")}</h1>
      <p className="hint">Spitalul Judetean de Urgenta Zalau — Sectia Pediatrie</p>
      <label htmlFor="username">{t("login.username")}</label>
      <input
        id="username"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        autoComplete="username"
        required
      />
      <label htmlFor="password">{t("login.password")}</label>
      <input
        id="password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
        required
      />
      {error && <p className="error">{error}</p>}
      <button type="submit">{t("login.signIn")}</button>
    </form>
    </>
  );
}
