import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { AsyncFeedback, ConfirmDialog, PageHeader, StatusPill, Surface } from "../components/ui";

type Operator = {
  id: string;
  email: string;
  mfa_enabled: boolean;
  mfa_pending: boolean;
};

type Health = { status: string; version: string; env: string };
type Readiness = { status: string };
type TotpSetup = { secret: string; otpauth_url: string; pending: boolean };
type Session = { id: string; family_id: string; device_label: string | null; current: boolean; created_at: string | null; expires_at: string; revoked_at: string | null };
type SessionSummary = { active: Session[]; recent: Session[]; history_total: number };
type SessionHistory = { items: Session[]; total: number; offset: number; limit: number };

export function SettingsPage() {
  const { token } = useAuth();
  const [operator, setOperator] = useState<Operator | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [history, setHistory] = useState<Session[] | null>(null);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sessionToRevoke, setSessionToRevoke] = useState<Session | null>(null);

  async function load() {
    const [me, apiHealth, apiReadiness, sessionSummary] = await Promise.all([
      api<Operator>("/api/v1/security/me", {}, token),
      api<Health>("/api/v1/health/live", {}, token),
      api<Readiness>("/api/v1/health/ready", {}, token).catch(() => null),
      api<SessionSummary>("/api/v1/security/sessions", {}, token),
    ]);
    setOperator(me);
    setHealth(apiHealth);
    setReadiness(apiReadiness);
    setSessions([...sessionSummary.active, ...sessionSummary.recent]);
    setHistoryTotal(sessionSummary.history_total);
    setHistory(null);
    setHistoryOffset(0);
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить настройки"));
  }, [token]);

  async function revokeSession(session: Session) {
    setBusy(`session:${session.id}`);
    setError(null);
    try {
      await api(`/api/v1/security/sessions/${session.id}/revoke`, { method: "POST" }, token);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось отозвать сессию");
    } finally {
      setBusy(null);
    }
  }

  async function loadSessionHistory(offset = 0) {
    setHistoryLoading(true);
    setError(null);
    try {
      const result = await api<SessionHistory>(`/api/v1/security/sessions/history?limit=25&offset=${offset}`, {}, token);
      setHistory(result.items);
      setHistoryOffset(result.offset);
      setHistoryTotal(result.total);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить историю сессий");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function beginTotp() {
    setBusy("setup");
    setError(null);
    try {
      setSetup(await api<TotpSetup>("/api/v1/security/totp/setup", { method: "POST" }, token));
      setCode("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось начать настройку MFA");
    } finally {
      setBusy(null);
    }
  }

  async function confirmTotp(event: FormEvent) {
    event.preventDefault();
    setBusy("confirm");
    setError(null);
    try {
      await api("/api/v1/security/totp/confirm", { method: "POST", body: JSON.stringify({ code }) }, token);
      setSetup(null);
      setCode("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Код не подтверждён");
    } finally {
      setBusy(null);
    }
  }

  async function disableTotp(event: FormEvent) {
    event.preventDefault();
    setBusy("disable");
    setError(null);
    try {
      await api("/api/v1/security/totp/disable", { method: "POST", body: JSON.stringify({ code }) }, token);
      setCode("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось отключить MFA");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader title="Настройки" description="Управляйте защитой единственного оператора. Секреты окружения и пароли никогда не выводятся в панели." />
      <AsyncFeedback error={error} />
      <Surface title="Оператор">
        <div className="detail-grid"><div><strong>Email</strong><p>{operator?.email || "—"}</p></div><div><strong>Доступ</strong><p>{operator ? "единственный оператор" : "—"}</p></div><div><strong>Двухфакторная защита</strong><p><StatusPill tone={operator?.mfa_enabled ? "ok" : "warn"}>{operator?.mfa_enabled ? "включена" : "не включена"}</StatusPill></p></div></div>
      </Surface>
      <Surface title="TOTP / приложение-аутентификатор">
        {!operator?.mfa_enabled && !setup && <div className="stack"><p className="muted" style={{ margin: 0 }}>Подключите приложение-аутентификатор до публикации рабочих сайтов. После подтверждения код потребуется при каждом новом входе.</p><button className="btn" type="button" disabled={busy !== null} onClick={beginTotp}>{busy === "setup" ? "Подготовка…" : "Настроить TOTP"}</button></div>}
        {setup && <form onSubmit={confirmTotp} className="stack"><p className="muted" style={{ margin: 0 }}>Добавьте этот ключ в приложение-аутентификатор. Он показывается только до подтверждения, не передавайте его третьим лицам.</p><label className="field">Секретный ключ<input value={setup.secret} readOnly /></label><label className="field">Одноразовый код<input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))} inputMode="numeric" autoComplete="one-time-code" required /></label><div className="row"><button className="btn" type="submit" disabled={busy !== null || code.length < 6}>{busy === "confirm" ? "Проверка…" : "Подтвердить"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => { setSetup(null); setCode(""); }}>Отмена</button></div></form>}
        {operator?.mfa_enabled && <form onSubmit={disableTotp} className="stack"><p className="muted" style={{ margin: 0 }}>Чтобы отключить TOTP, подтвердите текущий одноразовый код. Это действие записывается в audit log.</p><label className="field">Текущий код<input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))} inputMode="numeric" autoComplete="one-time-code" required /></label><button className="btn btn-ghost" type="submit" disabled={busy !== null || code.length < 6}>{busy === "disable" ? "Отключение…" : "Отключить TOTP"}</button></form>}
      </Surface>
      <Surface title="Сессии">
        <p className="muted">Показываются все активные device sessions и максимум десять завершённых device families. Refresh rotations не создают отдельные видимые сессии. Отзыв другой family прекращает обновление токена на этом устройстве; уже выданный access token может действовать до 15 минут.</p>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Устройство</th><th>Создана</th><th>Истекает</th><th>Состояние</th><th></th></tr></thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.id}>
                  <td>{session.device_label || "Неизвестное / прежняя сессия"}</td>
                  <td className="muted">{session.created_at?.slice(0, 19) || "—"}</td>
                  <td className="muted">{session.expires_at.slice(0, 19)}</td>
                  <td><StatusPill tone={session.revoked_at ? "danger" : session.current ? "ok" : "accent"}>{session.revoked_at ? "отозвана" : session.current ? "текущая" : "активна"}</StatusPill></td>
                  <td>{!session.current && !session.revoked_at ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setSessionToRevoke(session)}>{busy === `session:${session.id}` ? "Отзыв…" : "Отозвать"}</button> : null}</td>
                </tr>
              ))}
              {sessions.length === 0 ? <tr><td colSpan={5} className="muted">Активных или недавних device sessions нет</td></tr> : null}
            </tbody>
          </table>
        </div>
        {historyTotal > 10 ? <button className="btn btn-ghost" type="button" disabled={historyLoading} onClick={() => void loadSessionHistory()}>{historyLoading ? "Загрузка истории…" : `Открыть историю (${historyTotal})`}</button> : null}
        {history ? <>
          <div className="table-wrap"><table className="table"><thead><tr><th colSpan={3}>История завершённых сессий</th></tr></thead><tbody>{history.map((session) => <tr key={session.id}><td className="muted">{session.created_at?.slice(0, 19) || "—"}</td><td className="muted">{session.expires_at.slice(0, 19)}</td><td><StatusPill tone={session.revoked_at ? "danger" : "default"}>{session.revoked_at ? "отозвана" : "истекла"}</StatusPill></td></tr>)}{history.length === 0 ? <tr><td colSpan={3} className="muted">Более ранних сессий нет</td></tr> : null}</tbody></table></div>
          <div className="row"><span className="muted">Показано {history.length ? historyOffset + 1 : 0}–{Math.min(historyOffset + history.length, historyTotal)} из {historyTotal}</span><button className="btn btn-ghost" type="button" disabled={historyLoading || historyOffset === 0} onClick={() => void loadSessionHistory(Math.max(0, historyOffset - 25))}>Назад</button><button className="btn btn-ghost" type="button" disabled={historyLoading || historyOffset + history.length >= historyTotal} onClick={() => void loadSessionHistory(historyOffset + 25)}>Далее</button></div>
        </> : null}
      </Surface>
      <Surface title="Системная диагностика"><div className="row"><StatusPill tone={health?.status === "ok" ? "ok" : "danger"}>API: {health?.status || "недоступен"}</StatusPill><StatusPill tone={readiness?.status === "ok" ? "ok" : "danger"}>PostgreSQL / Redis: {readiness?.status || "недоступны"}</StatusPill><span className="muted">Версия: {health?.version || "—"}</span><span className="muted">Режим: {health?.env || "—"}</span></div><p className="muted" style={{ marginBottom: 0 }}>Резервные копии и внешние сервисы проверяются только на сервере; панель не показывает и не хранит их секреты.</p></Surface>
      <ConfirmDialog
        open={sessionToRevoke !== null}
        title="Отозвать сессию?"
        description="Эта device family больше не сможет обновить access token. Уже выданный access token может действовать до 15 минут."
        confirmLabel="Отозвать"
        dangerous
        onCancel={() => setSessionToRevoke(null)}
        onConfirm={() => {
          const session = sessionToRevoke;
          setSessionToRevoke(null);
          if (session) void revokeSession(session);
        }}
      />
    </div>
  );
}
