import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, getHolidays, getSchedule, listUsers, putSetup } from "../api/client";
import type {
  RequestKind,
  ScheduleDetailOut,
  ScheduleSetupPut,
  UserOut,
} from "../api/types";
import { useUser } from "../context/UserContext";
import {
  dayOf,
  isWeekend,
  monthDates,
  monthYearLabel,
  shortDayMonth,
  suggestedTarget,
  weekdayIndex,
} from "../lib/dates";
import { sectionLabel } from "../lib/schedule";
import { Banner, ConfirmDialog } from "../components/ui";

// Local editable models.
interface RosterRow {
  user: UserOut;
  rostered: boolean;
  kitchen: boolean; // explicit boolean always sent
}
interface RequestRow {
  user_id: number | "";
  date: string;
  kind: RequestKind;
  hard: boolean;
  granted: boolean | null;
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

export default function ScheduleSetup() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const scheduleId = Number(id);
  const { me } = useUser();

  const [detail, setDetail] = useState<ScheduleDetailOut | null>(null);
  const [holidays, setHolidays] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");

  // Editable local state.
  const [target, setTarget] = useState<number | "">("");
  const [maxTime, setMaxTime] = useState<number | "">("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [rosterRows, setRosterRows] = useState<RosterRow[]>([]);
  const [daysOff, setDaysOff] = useState<Set<string>>(new Set()); // "userId|date"
  const [requests, setRequests] = useState<RequestRow[]>([]);
  const [confirmSave, setConfirmSave] = useState(false);
  const [saving, setSaving] = useState(false);

  // Redirect non-managers away (route already guards, but be safe).
  useEffect(() => {
    if (me && me.role !== "manager_nurse") navigate(`/schedules/${scheduleId}`);
  }, [me, navigate, scheduleId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await getSchedule(scheduleId);
        if (cancelled) return;
        if (d.status !== "draft") {
          // Setup only makes sense on drafts.
          navigate(`/schedules/${d.id}`, { state: { notice: "setupOnlyDraft" } });
          return;
        }
        const [users, hs] = await Promise.all([listUsers(), getHolidays(d.year)]);
        if (cancelled) return;
        setDetail(d);
        setHolidays(new Set(hs.map((h) => h.date)));
        initFromDetail(d, users);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scheduleId]);

  function initFromDetail(d: ScheduleDetailOut, users: UserOut[]) {
    setTarget(d.setup.target_hours);
    const cfgMax = (d.setup.config as { max_time_s?: number }).max_time_s;
    setMaxTime(cfgMax ?? "");
    if (cfgMax != null) setAdvancedOpen(true);

    const rosterById = new Map(d.setup.roster.map((r) => [r.user_id, r]));
    const active = users.filter((u) => u.active);
    setRosterRows(
      active.map((u) => {
        const existing = rosterById.get(u.id);
        return {
          user: u,
          rostered: Boolean(existing),
          kitchen: existing ? existing.kitchen_capable : u.default_kitchen_capable,
        };
      }),
    );

    setDaysOff(new Set(d.setup.days_off.map((o) => `${o.user_id}|${o.date}`)));

    setRequests(
      d.setup.requests.map((r) => ({
        user_id: r.user_id,
        date: r.date,
        kind: r.kind,
        hard: r.hard,
        granted: r.granted,
      })),
    );
  }

  const days = useMemo(
    () => (detail ? monthDates(detail.year, detail.month) : []),
    [detail],
  );

  const rosteredUsers = useMemo(
    () => rosterRows.filter((r) => r.rostered).map((r) => r.user),
    [rosterRows],
  );

  const suggested = useMemo(() => {
    if (!detail) return 0;
    return suggestedTarget(detail.year, detail.month, holidays);
  }, [detail, holidays]);

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

  function toggleDayOff(userId: number, date: string) {
    if (isWeekend(date) || holidays.has(date)) return; // Co only on Mon-Fri working days
    const key = `${userId}|${date}`;
    setDaysOff((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function buildPayload(): ScheduleSetupPut {
    const roster = rosterRows
      .filter((r) => r.rostered)
      .map((r) => ({ user_id: r.user.id, kitchen_capable: r.kitchen }));

    const rosteredIds = new Set(roster.map((r) => r.user_id));

    const days_off = [...daysOff]
      .map((k) => {
        const [uid, date] = k.split("|");
        return { user_id: Number(uid), date };
      })
      // Only keep Co for users still on the roster.
      .filter((o) => rosteredIds.has(o.user_id));

    const outRequests = requests
      .filter((r) => r.user_id !== "" && r.date && rosteredIds.has(Number(r.user_id)))
      .map((r) => ({
        user_id: Number(r.user_id),
        date: r.date,
        kind: r.kind,
        hard: r.hard,
      }));

    // Echo prev_tail unchanged (string keys already).
    const prev_tail = detail!.setup.prev_tail;

    // Merge max_time_s into the existing config object.
    const config: Record<string, unknown> = { ...detail!.setup.config };
    if (maxTime === "") delete config.max_time_s;
    else config.max_time_s = maxTime;

    return {
      target_hours: typeof target === "number" ? target : 0,
      roster,
      days_off,
      requests: outRequests,
      prev_tail,
      config,
    };
  }

  async function doSave() {
    setConfirmSave(false);
    setSaving(true);
    setError("");
    try {
      await putSetup(scheduleId, buildPayload());
      navigate(`/schedules/${scheduleId}`);
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  function onSaveClick() {
    if (detail && detail.assignments.length > 0) setConfirmSave(true);
    else void doSave();
  }

  return (
    <div className="page setup-page">
      <div className="detail-header">
        <h1>
          {t("setup.title")} — {monthYearLabel(detail.year, detail.month)}
        </h1>
        <Link to={`/schedules/${detail.id}`} className="nav-link">
          {t("common.back")}
        </Link>
      </div>

      {error && <Banner tone="error" onDismiss={() => setError("")}>{error}</Banner>}

      {/* 1. Parametri */}
      <section className="panel">
        <h2>{t("setup.parameters")}</h2>
        <div className="field">
          <label htmlFor="su-target">{t("schedules.target")}</label>
          <input
            id="su-target"
            type="number"
            min={0}
            value={target}
            onChange={(e) => setTarget(e.target.value === "" ? "" : Number(e.target.value))}
          />
          <p className="hint">{t("setup.targetSuggested", { hours: suggested })}</p>
        </div>
        <button type="button" className="link-button" onClick={() => setAdvancedOpen((o) => !o)}>
          {advancedOpen ? t("setup.advancedHide") : t("setup.advancedShow")}
        </button>
        {advancedOpen && (
          <div className="field">
            <label htmlFor="su-maxtime">{t("setup.maxTime")}</label>
            <input
              id="su-maxtime"
              type="number"
              min={1}
              placeholder="30"
              value={maxTime}
              onChange={(e) => setMaxTime(e.target.value === "" ? "" : Number(e.target.value))}
            />
          </div>
        )}
      </section>

      {/* 2. Echipa */}
      <section className="panel">
        <h2>{t("setup.team")}</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("setup.rostered")}</th>
              <th>{t("grid.nurse")}</th>
              <th>{t("accounts.section")}</th>
              <th>{t("setup.kitchen")}</th>
            </tr>
          </thead>
          <tbody>
            {rosterRows.map((row) => (
              <tr key={row.user.id} className={row.rostered ? "" : "muted"}>
                <td>
                  <input
                    type="checkbox"
                    checked={row.rostered}
                    onChange={(e) =>
                      setRosterRows((prev) =>
                        prev.map((r) =>
                          r.user.id === row.user.id ? { ...r, rostered: e.target.checked } : r,
                        ),
                      )
                    }
                  />
                </td>
                <td>{row.user.full_name}</td>
                <td>{sectionLabel(row.user.section, t)}</td>
                <td>
                  <input
                    type="checkbox"
                    checked={row.kitchen}
                    onChange={(e) =>
                      setRosterRows((prev) =>
                        prev.map((r) =>
                          r.user.id === row.user.id ? { ...r, kitchen: e.target.checked } : r,
                        ),
                      )
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">{t("setup.singleManager")}</p>
      </section>

      {/* 3. Zile de concediu (Co) */}
      <section className="panel">
        <h2>{t("setup.leave")}</h2>
        <div className="grid-scroll">
          <table className="grid setup-matrix">
            <thead>
              <tr>
                <th className="name-col">{t("grid.nurse")}</th>
                {days.map((d) => (
                  <th
                    key={d}
                    className={`day-col ${holidays.has(d) ? "col-holiday" : isWeekend(d) ? "col-weekend" : ""}`}
                  >
                    {dayOf(d)}
                    <br />
                    <span className="wd">{t(WEEKDAY_KEYS[weekdayIndex(d)])}</span>
                  </th>
                ))}
                <th className="tot-col">Co</th>
              </tr>
            </thead>
            <tbody>
              {rosteredUsers.map((u) => {
                let count = 0;
                return (
                  <tr key={u.id}>
                    <td className="name-col">{u.full_name}</td>
                    {days.map((d) => {
                      const off = daysOff.has(`${u.id}|${d}`);
                      if (off) count++;
                      const blocked = isWeekend(d) || holidays.has(d);
                      return (
                        <td
                          key={d}
                          className={[
                            "cell",
                            off ? "c-co" : "",
                            holidays.has(d) ? "col-holiday" : isWeekend(d) ? "col-weekend" : "",
                            blocked ? "" : "clickable",
                          ]
                            .filter(Boolean)
                            .join(" ")}
                          title={blocked ? t("setup.coWeekendBlocked") : undefined}
                          onClick={blocked ? undefined : () => toggleDayOff(u.id, d)}
                        >
                          {off ? "Co" : ""}
                        </td>
                      );
                    })}
                    <td className="tot-col">{count}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* 4. Cereri */}
      <section className="panel">
        <h2>{t("setup.requests")}</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("grid.nurse")}</th>
              <th>{t("request.date")}</th>
              <th>{t("request.kind")}</th>
              <th>{t("request.hard")}</th>
              <th>{t("request.granted")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {requests.map((r, idx) => (
              <tr key={idx}>
                <td>
                  <select
                    value={r.user_id}
                    onChange={(e) =>
                      updateRequest(idx, {
                        user_id: e.target.value === "" ? "" : Number(e.target.value),
                      })
                    }
                  >
                    <option value="">—</option>
                    {rosteredUsers.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.full_name}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    type="date"
                    min={days[0]}
                    max={days[days.length - 1]}
                    value={r.date}
                    onChange={(e) => updateRequest(idx, { date: e.target.value })}
                  />
                </td>
                <td>
                  <select
                    value={r.kind}
                    onChange={(e) => updateRequest(idx, { kind: e.target.value as RequestKind })}
                  >
                    <option value="Z">{t("request.kindDay")}</option>
                    <option value="N">{t("request.kindNight")}</option>
                    <option value="K">{t("request.kindKitchen")}</option>
                    <option value="FREE">{t("request.kindFree")}</option>
                  </select>
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={r.hard}
                    onChange={(e) => updateRequest(idx, { hard: e.target.checked })}
                  />
                </td>
                <td>
                  {r.granted === true
                    ? t("request.grantedYes")
                    : r.granted === false
                      ? t("request.grantedNo")
                      : "—"}
                </td>
                <td>
                  <button
                    type="button"
                    className="link-button danger"
                    onClick={() => setRequests((prev) => prev.filter((_, i) => i !== idx))}
                  >
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          type="button"
          className="secondary small"
          onClick={() =>
            setRequests((prev) => [
              ...prev,
              { user_id: "", date: days[0] ?? "", kind: "Z", hard: false, granted: null },
            ])
          }
        >
          {t("setup.addRequest")}
        </button>
      </section>

      {/* 5. prev_tail (read-only) */}
      <section className="panel">
        <h2>{t("setup.prevTail")}</h2>
        <p className="hint">{t("setup.prevTailHint")}</p>
        <div className="chip-list">
          {rosteredUsers.map((u) => {
            const tail = detail.setup.prev_tail[String(u.id)];
            if (!tail || Object.keys(tail).length === 0) return null;
            return (
              <div className="tail-nurse" key={u.id}>
                <span className="tail-name">{u.full_name}:</span>
                {Object.entries(tail)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([date, code]) => (
                    <span className="chip static" key={date}>
                      {shortDayMonth(date)}: {code}
                    </span>
                  ))}
              </div>
            );
          })}
        </div>
      </section>

      <div className="action-bar sticky-save">
        <button type="button" disabled={saving} onClick={onSaveClick}>
          {saving ? t("common.saving") : t("setup.save")}
        </button>
      </div>

      {confirmSave && (
        <ConfirmDialog
          title={t("setup.save")}
          message={t("setup.wipeConfirm")}
          confirmLabel={t("common.continue")}
          onConfirm={doSave}
          onCancel={() => setConfirmSave(false)}
        />
      )}
    </div>
  );

  function updateRequest(idx: number, patch: Partial<RequestRow>) {
    setRequests((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }
}
