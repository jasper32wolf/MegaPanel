import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
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
type SessionSummary = { items: Session[]; total: number; older_total: number };
type AlertChannels = { enabled: boolean; email: boolean; telegram: boolean };
type AlertSettings = {
  channels: AlertChannels;
  recipient: { configured: boolean; masked_email: string | null };
};

function alertChannelLabel(configured: boolean) {
  return configured ? "готов" : "не настроен";
}


function sessionTone(status: Session["status"]) {
  return status === "current" ? "ok" : status === "active" ? "accent" : status === "revoked" ? "danger" : "default";
}

function sessionLabel(status: Session["status"]) {
  return status === "current" ? "текущая" : status === "active" ? "активна" : status === "revoked" ? "отозвана" : "истекла";
}

export function SettingsPage() {
  const { token } = useAuth();
  const [operator, setOperator] = useState<Operator | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [olderSessionTotal, setOlderSessionTotal] = useState(0);
  const [alertChannels, setAlertChannels] = useState<AlertChannels | null>(null);
  const [alertRecipient, setAlertRecipient] = useState<{ configured: boolean; masked_email: string | null } | null>(null);
  const [recipientEmail, setRecipientEmail] = useState("");
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sessionToRevoke, setSessionToRevoke] = useState<Session | null>(null);

  async function load() {
    const [me, apiHealth, apiReadiness, sessionSummary, alertSettings] = await Promise.all([
      api<Operator>("/api/v1/security/me", {}, token),
      api<Health>("/api/v1/health/live", {}, token),
      api<Readiness>("/api/v1/health/ready", {}, token).catch(() => null),
      api<SessionSummary>("/api/v1/security/sessions", {}, token),
      api<AlertSettings>("/api/v1/panel/alert-settings", {}, token),
    ]);
    setOperator(me);
    setHealth(apiHealth);
    setReadiness(apiReadiness);
    setSessions(sessionSummary.items.slice(0, 10));
    setOlderSessionTotal(sessionSummary.older_total);
    setAlertChannels(alertSettings.channels);
    setAlertRecipient(alertSettings.recipient);
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить настройки"));
  }, [token]);

  async function saveAlertRecipient(event: FormEvent) {
    event.preventDefault();
    setBusy("alert-recipient");
    setError(null);
    try {
      await api(
        "/api/v1/panel/alert-settings/recipient",
        { method: "PUT", body: JSON.stringify({ email: recipientEmail }) },
        token,
      );
      setRecipientEmail("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить адрес получателя");
    } finally {
      setBusy(null);
    }
  }

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
      <Surface title="Оповещения">
        <div className="row"><StatusPill tone={alertChannels?.enabled ? "ok" : "warn"}>Оповещения: {alertChannels?.enabled ? "включены" : "выключены"}</StatusPill><StatusPill tone={alertChannels?.email ? "ok" : "warn"}>SMTP: {alertChannelLabel(Boolean(alertChannels?.email))}</StatusPill><StatusPill tone={alertChannels?.telegram ? "ok" : "warn"}>Telegram: {alertChannelLabel(Boolean(alertChannels?.telegram))}</StatusPill><StatusPill tone={alertRecipient?.configured ? "ok" : "warn"}>Получатель: {alertRecipient?.configured ? "настроен" : "не указан"}</StatusPill></div>
        <p className="muted">Адрес получателя задаётся только здесь, в панели, и хранится зашифрованно. Текущий: {alertRecipient?.masked_email || "не указан"}. SMTP и Telegram-секреты остаются только на VPS и в панели не показываются.</p>
        <form className="stack" onSubmit={saveAlertRecipient}><label className="field">Email для оповещений<input type="email" value={recipientEmail} onChange={(event) => setRecipientEmail(event.target.value)} autoComplete="email" placeholder="alerts@example.com" required /></label><button className="btn" type="submit" disabled={busy !== null || !recipientEmail}>{busy === "alert-recipient" ? "Сохранение…" : alertRecipient?.configured ? "Заменить адрес" : "Сохранить адрес"}</button></form>
        <p className="muted">Этот адрес используется только для получения security, site и system alerts; он не обязан совпадать с email входа в панель.</p>
        <Link className="btn btn-ghost" to="/alerts">Открыть оповещения</Link>
      </Surface>
      <Surface title="Сессии">
        <p className="muted">Показаны не более десяти последних сессий. Каждая строка — одна device family: обновление refresh token не создаёт дубликат. IP и место фиксируются при входе локальной GeoIP-базой и могут быть не определены или неточны. Источник геоданных: <a href="https://db-ip.com" target="_blank" rel="noreferrer">DB-IP Lite</a> · CC BY 4.0.</p>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Браузер и устройство</th><th>Язык</th><th>IP</th><th>Страна и город</th><th>Последняя активность</th><th>Состояние</th><th></th></tr></thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.id}>
                  <td><strong>{session.browser_name || "Не определено"}</strong><p className="muted">{session.device_label || "Прежняя сессия"}</p></td>
                  <td>{session.language || "Не определён"}</td>
                  <td>{session.ip_address || "Не определён"}</td>
                  <td>{[session.country, session.city].filter(Boolean).join(" · ") || "Не определены"}</td>
                  <td className="muted">{session.created_at?.slice(0, 19) || "—"}</td>
                  <td><StatusPill tone={sessionTone(session.status)}>{sessionLabel(session.status)}</StatusPill></td>
                  <td>{session.can_revoke ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setSessionToRevoke(session)}>{busy === `session:${session.id}` ? "Отзыв…" : "Отозвать"}</button> : null}</td>
                </tr>
              ))}
              {sessions.length === 0 ? <tr><td colSpan={7} className="muted">Сессий пока нет</td></tr> : null}
            </tbody>
          </table>
        </div>
        {olderSessionTotal > 0 ? <Link className="btn btn-ghost" to="/settings/sessions">{`Открыть журнал прошлых сессий (${olderSessionTotal})`}</Link> : null}
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
