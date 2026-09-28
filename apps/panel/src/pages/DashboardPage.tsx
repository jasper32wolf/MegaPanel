import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { HelpTip, InlineAlert, PageHeader, StatusPill, Surface } from "../components/ui";

type Health = { status: string; version: string; env: string };
type Readiness = { status: string };
type Alert = {
  code: string;
  severity: "info" | "warning" | "critical";
  title: string;
  detail: string;
  count: number;
  route: string;
};
type Summary = {
  observed_at: string;
  sites: number;
  published_sites: number;
  pages_estimate: number;
  leads: number;
  active_leads: number;
  delivery_pending: number;
  delivery_dead_letter: number;
  delivery_oldest_at: string | null;
  domains_pending_tls: number;
  domains_tls_error: number;
  project_statuses: Record<string, number>;
  page_plan_statuses: Record<string, number>;
  page_draft_statuses: Record<string, number>;
  build_statuses: Record<string, number>;
  system_operation_statuses: Record<string, number>;
  alerts: Alert[];
};

function formatMoment(value: string | null | undefined) {
  return value ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "—";
}

export function DashboardPage() {
  const { token } = useAuth();
  const [health, setHealth] = useState<Health | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const [nextHealth, nextReadiness, nextSummary] = await Promise.all([
        api<Health>("/api/v1/health/live", {}, token),
        api<Readiness>("/api/v1/health/ready", {}, token),
        api<Summary>("/api/v1/panel/reports/summary", {}, token),
      ]);
      setHealth(nextHealth);
      setReadiness(nextReadiness);
      setSummary(nextSummary);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось обновить состояние панели");
    } finally {
      setRefreshing(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(interval);
  }, [load]);

  const criticalAlerts = summary?.alerts.filter((alert) => alert.severity === "critical") || [];
  const otherAlerts = summary?.alerts.filter((alert) => alert.severity !== "critical") || [];
  const deliveryTone = summary?.delivery_dead_letter ? "danger" : summary?.delivery_pending ? "warn" : "ok";
  const domainTone = summary?.domains_tls_error ? "danger" : summary?.domains_pending_tls ? "warn" : "ok";

  return (
    <div>
      <PageHeader
        title="Обзор"
        description="Текущее измеренное состояние системы, сайтов и очередей. Обновляется каждые 30 секунд и не заменяет external production monitoring."
        actions={
          <>
            <button className="btn btn-ghost" type="button" onClick={() => void load(true)} disabled={refreshing}>
              {refreshing ? "Обновление…" : "Обновить"}
            </button>
            <Link className="btn btn-ghost" to="/projects">Новый проект</Link>
            <Link className="btn" to="/sites">Сайты</Link>
          </>
        }
      />
      {error ? <InlineAlert severity="critical" title="Состояние не обновлено">{error}. Последние успешно загруженные показатели остаются на экране.</InlineAlert> : null}
      {criticalAlerts.map((alert) => (
        <InlineAlert key={alert.code} severity={alert.severity} title={`${alert.title}: ${alert.count}`}>
          {alert.detail} <Link to={alert.route}>Открыть раздел</Link>
        </InlineAlert>
      ))}
      <div className="stat-grid">
        <div className="stat"><div className="label">Сайты</div><div className="value">{summary?.sites ?? "—"}</div><p className="muted">{summary ? `${summary.published_sites} опубликовано · ${summary.pages_estimate} страниц` : "Загрузка…"}</p></div>
        <div className="stat"><div className="label">Активные лиды</div><div className="value">{summary?.active_leads ?? "—"}</div><p className="muted">Всего: {summary?.leads ?? "—"}</p></div>
        <div className="stat"><div className="label">Delivery queue</div><div className="value">{summary?.delivery_pending ?? "—"}</div><p><StatusPill tone={deliveryTone}>{summary?.delivery_dead_letter ? `${summary.delivery_dead_letter} dead letter` : "без dead letter"}</StatusPill></p></div>
        <div className="stat"><div className="label">TLS / DNS</div><div className="value">{summary ? summary.domains_pending_tls + summary.domains_tls_error : "—"}</div><p><StatusPill tone={domainTone}>{summary?.domains_tls_error ? `${summary.domains_tls_error} ошибок` : "проверьте pending"}</StatusPill></p></div>
      </div>
      <Surface title="Системное состояние">
        <div className="row">
          <StatusPill tone={health?.status === "ok" ? "ok" : "danger"}>API: {health?.status || "недоступен"}</StatusPill>
          <StatusPill tone={readiness?.status === "ok" ? "ok" : "danger"}>PostgreSQL / Redis: {readiness?.status || "недоступны"}</StatusPill>
          {health ? <span className="muted">v{health.version} · {health.env}</span> : null}
          <span className="muted">Последняя сводка: {formatMoment(summary?.observed_at)}</span>
          <HelpTip label="Readiness">Readiness проверяет доступность PostgreSQL и Redis. Она не подтверждает TLS, Caddy, restore drill или внешний production hostname.</HelpTip>
        </div>
      </Surface>
      <Surface title="Внимание оператора">
        {summary === null ? <p className="muted">Загрузка alert-очереди…</p> : otherAlerts.length === 0 && criticalAlerts.length === 0 ? <p className="muted">Открытых серверных alert-условий нет.</p> : <div className="stack">{otherAlerts.map((alert) => <InlineAlert key={alert.code} severity={alert.severity} title={`${alert.title}: ${alert.count}`}>{alert.detail} <Link to={alert.route}>Перейти</Link></InlineAlert>)}</div>}
      </Surface>
      <Surface title="Сайты и workflow">
        <div className="row">
          <StatusPill tone={summary?.page_plan_statuses.review ? "warn" : "ok"}>Планы на проверке: {summary?.page_plan_statuses.review || 0}</StatusPill>
          <StatusPill tone={summary?.page_draft_statuses.review ? "warn" : "ok"}>Черновики на проверке: {summary?.page_draft_statuses.review || 0}</StatusPill>
          <StatusPill tone={summary?.page_draft_statuses.failed ? "danger" : "ok"}>Ошибки черновиков: {summary?.page_draft_statuses.failed || 0}</StatusPill>
          <StatusPill tone={summary?.build_statuses.ready ? "accent" : "default"}>Готовые candidate: {summary?.build_statuses.ready || 0}</StatusPill>
        </div>
        <p className="muted">Одобрение plan/draft и готовая candidate-сборка не публикуют сайт автоматически. Публикация остаётся отдельным подтверждаемым действием.</p>
      </Surface>
      <Surface title="Доставка заявок">
        <div className="row">
          <StatusPill tone={deliveryTone}>Ожидают обработки: {summary?.delivery_pending ?? "—"}</StatusPill>
          <StatusPill tone={summary?.delivery_dead_letter ? "danger" : "ok"}>Dead letter: {summary?.delivery_dead_letter ?? "—"}</StatusPill>
          <span className="muted">Самая ранняя ожидающая: {formatMoment(summary?.delivery_oldest_at)}</span>
          <HelpTip label="Dead letter">Delivery попадает сюда после исчерпания попыток или отсутствия корректного получателя. Откройте inbox, исправьте конфигурацию и повторите доставку вручную.</HelpTip>
        </div>
        <Link className="btn btn-ghost" to="/leads">Открыть inbox лидов</Link>
      </Surface>
    </div>
  );
}
