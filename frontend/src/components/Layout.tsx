import { useTranslation } from "react-i18next";
import { NavLink, useNavigate } from "react-router-dom";

import { useUser } from "../context/UserContext";
import LangSwitcher from "./LangSwitcher";

export default function Layout({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { me, logout: clearSession } = useUser();
  const isManager = me?.role === "manager_nurse";

  function logout() {
    clearSession();
    navigate("/login");
  }

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    isActive ? "nav-link active" : "nav-link";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-left">
          <span className="brand">{t("app.title")}</span>
          <nav className="nav-links">
            <NavLink to="/schedules" className={linkClass}>
              {t("nav.schedules")}
            </NavLink>
            {isManager && (
              <NavLink to="/accounts" className={linkClass}>
                {t("nav.accounts")}
              </NavLink>
            )}
            {isManager && (
              <NavLink to="/holidays" className={linkClass}>
                {t("nav.holidays")}
              </NavLink>
            )}
          </nav>
        </div>
        <div className="topbar-right">
          {me && <span className="me-name">{me.full_name}</span>}
          <LangSwitcher />
          <button type="button" className="link-button" onClick={logout}>
            {t("nav.logout")}
          </button>
        </div>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
