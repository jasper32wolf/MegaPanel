import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AsyncFeedback, PageHeader, StatusPill, Surface } from "../components/ui";
import { api, useAuth } from "../lib/auth";

type Session = {
  id: string;
  device_label: string | null;
  browser_name: string | null;
  language: string | null;
  ip_address: string | null;
  country: string | null;
  city: string | null;
  current: boolean;
  can_revoke: boolean;
  status: "current" | "active" | "revoked" | "expired";
  created_at: string | null;
  expires_at: string;
  revoked_at: string | null;
};

type SessionHistory = { items: Session[]; total: number; offset: number; limit: number };

function sessionTone(status: Session["status"]) {
  return status === "current" ? "ok" : status === "active" ? "accent" : status === "revoked" ? "danger" : "default";
}

function sessionLabel(status: Session["status"]) {
  return status === "current" ? "текущая" : status === "active" ? "активна" : status === "revoked" ? "отозвана" : "истекла";
}

export function SessionHistoryPage() {
  const { token } = useAuth();
  const [history, setHistory] = useState<SessionHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(offset = 0) {
    setLoading(true);
    setError(null);
    try {
      setHistory(await api<SessionHistory>(`/api/v1/security/sessions/history?limit=25&offset=${offset}`, {}, token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить журнал сессий");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [token]);

  const items = history?.items || [];
  const offset = history?.offset || 0;
  const total = history?.total || 0;

  return (
    <div>
      <PageHeader title="Журнал прошлых сессий" description="Здесь находятся сессии старше десяти последних записей из настроек. Это снимки на момент первой авторизации device family." actions={<Link className="btn btn-ghost" to="/settings">Вернуться к настройкам</Link>} />
      <AsyncFeedback error={error} />
      <Surface title="Прошлые сессии">
        {loading ? <p className="muted" aria-live="polite">Загрузка журнала…</p> : <>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Браузер и устройство</th><th>Язык</th><th>IP</th><th>Страна и город</th><th>Последняя активность</th><th>Состояние</th></tr></thead>
              <tbody>
                {items.map((session) => <tr key={session.id}>
                  <td><strong>{session.browser_name || "Не определено"}</strong><p className="muted">{session.device_label || "Прежняя сессия"}</p></td>
                  <td>{session.language || "Не определён"}</td>
                  <td>{session.ip_address || "Не определён"}</td>
                  <td>{[session.country, session.city].filter(Boolean).join(" · ") || "Не определены"}</td>
                  <td className="muted">{session.created_at?.slice(0, 19) || "—"}</td>
                  <td><StatusPill tone={sessionTone(session.status)}>{sessionLabel(session.status)}</StatusPill></td>
                </tr>)}
                {!items.length ? <tr><td colSpan={6} className="muted">Более ранних сессий нет</td></tr> : null}
              </tbody>
            </table>
          </div>
          <div className="row"><span className="muted">Показано {items.length ? offset + 1 : 0}–{Math.min(offset + items.length, total)} из {total}</span><button className="btn btn-ghost" type="button" disabled={offset === 0} onClick={() => void load(Math.max(0, offset - 25))}>Назад</button><button className="btn btn-ghost" type="button" disabled={offset + items.length >= total} onClick={() => void load(offset + 25)}>Далее</button></div>
        </>}
      </Surface>
    </div>
  );
}
