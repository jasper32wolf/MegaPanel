import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { AsyncFeedback, ConfirmDialog, PageHeader, StatusPill, Surface } from "../components/ui";

type DeliveryStatus = "queued" | "processing" | "retrying" | "delivered" | "dead_letter";
type DeliveryChannel = "smtp_bz_smtp" | "smtp_bz_api" | "telegram";
type Alert = {
  id: string;
  category: "security" | "site" | "system";
  signal_code: string | null;
  subject_kind: string | null;
  subject_key: string | null;
  subject_label: string | null;
  priority: string;
  title: string;
  body: string;
  read: boolean;
  created_at: string | null;
  deliveries: Partial<Record<DeliveryChannel, DeliveryStatus>>;
  delivery_errors?: Partial<Record<DeliveryChannel, string>>;
};
type AlertChannels = { enabled: boolean; email: boolean; telegram: boolean };
type AlertSummary = {
  items: Alert[];
  total: number;
  unread: number;
  channels: AlertChannels;
};
type AlertSettings = {
  channels: AlertChannels;
  recipient: { configured: boolean; masked_email: string | null };
  transport: { selected: "none" | "smtp_bz_smtp" | "smtp_bz_api"; configured: boolean };
};

function transportLabel(transport: AlertSettings["transport"]["selected"]) {
  return transport === "smtp_bz_smtp"
    ? "SMTP.bz SMTP"
    : transport === "smtp_bz_api"
      ? "SMTP.bz API"
      : "не выбран";
}

function tone(status: DeliveryStatus | undefined) {
  if (status === "delivered") return "ok" as const;
  if (status === "dead_letter") return "danger" as const;
  if (status === "retrying" || status === "processing") return "warn" as const;
  return "default" as const;
}

function categoryLabel(category: Alert["category"]) {
  return category === "security" ? "Безопасность" : category === "site" ? "Сайты" : "Система";
}

function deliveryErrorLabel(error: string | undefined) {
  const labels: Record<string, string> = {
    authentication_failed: "SMTP.bz отклонил логин или пароль",
    invalid_config: "неполная конфигурация SMTP.bz",
    legacy_transport_retired: "устаревший транспорт отключён",
    outcome_unknown: "SMTP.bz не подтвердил результат отправки",
    recipient_rejected: "сервис отклонил адрес получателя",
    sender_rejected: "SMTP.bz отклонил адрес отправителя",
    timeout: "истекло время ожидания SMTP.bz",
    tls_failed: "не удалось установить TLS-соединение",
    transport_changed: "конфигурация доставки была изменена",
    unavailable: "сервис доставки временно недоступен",
  };
  return error ? `${labels[error] || "ошибка доставки"} (${error})` : "";
}

function deliveryLabel(status: DeliveryStatus | undefined, error: string | undefined) {
  return `${status || "не настроен"}${error ? ` · ${deliveryErrorLabel(error)}` : ""}`;
}

export function AlertsPage() {
  const { token } = useAuth();
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [alertSettings, setAlertSettings] = useState<AlertSettings | null>(null);
  const [category, setCategory] = useState<"" | Alert["category"]>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmTest, setConfirmTest] = useState(false);

  async function load() {
    const query = category ? `?category=${category}` : "";
    const [nextSummary, nextSettings] = await Promise.all([
      api<AlertSummary>(`/api/v1/panel/alerts${query}`, {}, token),
      api<AlertSettings>("/api/v1/panel/alert-settings", {}, token),
    ]);
    setSummary(nextSummary);
    setAlertSettings(nextSettings);
  }

  useEffect(() => {
    void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить оповещения"));
  }, [token, category]);

  async function markRead(alert: Alert) {
    if (alert.read) return;
    setBusy(alert.id);
    setError(null);
    try {
      await api(`/api/v1/panel/alerts/${alert.id}/read`, { method: "POST" }, token);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось отметить оповещение прочитанным");
    } finally {
      setBusy(null);
    }
  }

  async function sendTest() {
    setConfirmTest(false);
    setBusy("test");
    setError(null);
    try {
      await api("/api/v1/panel/alerts/test", { method: "POST" }, token);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Проверочное оповещение не создано");
    } finally {
      setBusy(null);
    }
  }

  const channels = alertSettings?.channels || summary?.channels;
  const recipientConfigured = Boolean(alertSettings?.recipient.configured);
  return (
    <div>
      <PageHeader
        title="Оповещения"
        description="Инциденты сохраняются в панели независимо от доставки. Email и Telegram получают только чувствительные события, сбои сайтов и сообщения о восстановлении."
        actions={<button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось обновить оповещения"))}>Обновить</button>}
      />
      <AsyncFeedback error={error} />
      <Surface title="Каналы доставки">
        <div className="row">
          <StatusPill tone={channels?.enabled ? "ok" : "warn"}>Оповещения: {channels?.enabled ? "включены" : "выключены"}</StatusPill>
          <StatusPill tone={channels?.email ? "ok" : "warn"}>Email: {channels?.email ? transportLabel(alertSettings?.transport?.selected || "none") : "не настроен"}</StatusPill>
          <StatusPill tone={channels?.telegram ? "ok" : "warn"}>Telegram: {channels?.telegram ? "готов" : "не настроен"}</StatusPill>
          <StatusPill tone={recipientConfigured ? "ok" : "warn"}>Получатель: {recipientConfigured ? "настроен" : "не указан"}</StatusPill>
        </div>
        <p className="muted">Получатель задаётся в настройках панели: {alertSettings?.recipient.masked_email || "не указан"}. SMTP-параметры, Telegram bot token и chat ID не выводятся в панели.</p>
        {!recipientConfigured && <Link className="btn btn-ghost" to="/settings">Указать email получателя</Link>}
        <button className="btn" type="button" disabled={busy !== null || !channels?.enabled || !recipientConfigured} onClick={() => setConfirmTest(true)}>{busy === "test" ? "Отправка…" : "Отправить проверочное оповещение"}</button>
      </Surface>
      <Surface title={`Входящие (${summary?.unread ?? 0} непрочитанных)`}>
        <div className="row" role="group" aria-label="Фильтр оповещений">
          {(["", "security", "site", "system"] as const).map((value) => <button key={value || "all"} className={category === value ? "btn" : "btn btn-ghost"} type="button" onClick={() => setCategory(value)}>{value ? categoryLabel(value) : "Все"}</button>)}
        </div>
        {!summary ? <p className="muted" aria-live="polite">Загрузка оповещений…</p> : summary.items.length === 0 ? <p className="muted">Оповещений по выбранному фильтру пока нет.</p> : <div className="stack">{summary.items.map((alert) => <article key={alert.id} className="surface" aria-label={alert.title}>
          <div className="row"><StatusPill tone={alert.category === "security" ? "danger" : alert.category === "site" ? "warn" : "accent"}>{categoryLabel(alert.category)}</StatusPill><strong>{alert.title}</strong>{!alert.read && <StatusPill tone="warn">не прочитано</StatusPill>}</div>
          <p>{alert.body}</p>
          {alert.subject_label && <p className="muted">Объект: {alert.subject_label}</p>}
          <p className="muted">{alert.created_at ? new Date(alert.created_at).toLocaleString() : "—"} · код: {alert.signal_code || "—"}</p>
          <div className="row"><StatusPill tone={tone(alert.deliveries.smtp_bz_smtp || alert.deliveries.smtp_bz_api)}>Email: {deliveryLabel(alert.deliveries.smtp_bz_smtp || alert.deliveries.smtp_bz_api, alert.delivery_errors?.smtp_bz_smtp || alert.delivery_errors?.smtp_bz_api)}</StatusPill><StatusPill tone={tone(alert.deliveries.telegram)}>Telegram: {deliveryLabel(alert.deliveries.telegram, alert.delivery_errors?.telegram)}</StatusPill>{!alert.read && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void markRead(alert)}>{busy === alert.id ? "Сохранение…" : "Прочитано"}</button>}</div>
        </article>)}</div>}
      </Surface>
      <ConfirmDialog
        open={confirmTest}
        title="Отправить проверочное оповещение?"
        description="Будет создано тестовое inbox-сообщение и отправлено через настроенные каналы. Оно не меняет сайты, публикации или инфраструктуру."
        confirmLabel="Отправить"
        onCancel={() => setConfirmTest(false)}
        onConfirm={() => void sendTest()}
      />
    </div>
  );
}
