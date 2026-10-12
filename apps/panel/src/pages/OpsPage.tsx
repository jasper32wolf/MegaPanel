import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, PageHeader, StatusPill, Surface } from "../components/ui";

type Summary = { sites: number; pages_estimate: number; leads: number; active_leads: number; delivery_pending: number; delivery_dead_letter: number; domains_pending_tls: number; domains_tls_error: number };
type Incident = { id: string; signal_code: string; severity: "info" | "warning" | "critical"; status: "open" | "acknowledged" | "resolved"; occurrence_count: number; opened_at: string | null; acknowledged_at: string | null; snoozed_until: string | null; resolved_at: string | null };
type OperationalEvent = { event_type: string; severity: "info" | "warning" | "critical"; outcome: string; quantity: number; occurred_at: string | null };
type VerificationCheck = {
  check_key: string;
  label: string;
  mode: "fixture" | "local_compose" | "ci" | "staging" | "vps";
  state: "passed" | "failed" | "not_observed";
  observed_at: string | null;
  coverage: string;
  limitation: string;
};
type VerificationReport = { scope: string; checks: VerificationCheck[] };
type Observability = {
  observed_at: string;
  builds: { failed: number; latest_success: { build_hash: string | null; created_at: string | null } | null };
  ai: { status_counts: Record<string, number>; failed_error_codes: Record<string, number>; reserved_estimated_usd: number; recorded_cost_usd: number; unresolved_dead_letter_jobs: number };
  media: { assets: number; missing_files: number; provenance_gaps: number; expired_licenses: number; references: { status: string; source: string; assets: number; pages: number; invalid_entries: number } };
  content_gaps: { thin_pages: number; noindex_pages: number };
  system: { backups: { status: string; reason: string } };
  worker: { status: "healthy" | "stale" | "not_observed"; last_heartbeat_at: string | null; age_seconds: number | null; stale_after_seconds: number; reason: string | null };
};

function workerTone(status: Observability["worker"]["status"]) {
  if (status === "healthy") return "ok" as const;
  if (status === "stale") return "danger" as const;
  return "warn" as const;
}

function verificationTone(state: VerificationCheck["state"]) {
  if (state === "passed") return "ok" as const;
  if (state === "failed") return "danger" as const;
  return "warn" as const;
}

function statusLabel(status: string) {
  return {
    acknowledged: "подтверждён",
    critical: "критично",
    failed: "ошибка",
    healthy: "работает",
    info: "информация",
    manifest_snapshot: "снимок манифеста",
    not_observed: "нет наблюдений",
    open: "открыт",
    passed: "пройдено",
    resolved: "закрыт",
    stale: "нет актуальных данных",
    success: "успешно",
    warning: "предупреждение",
  }[status] || status;
}

function verificationModeLabel(mode: VerificationCheck["mode"]) {
  return {
    fixture: "тестовые данные",
    local_compose: "локальный Compose",
    ci: "CI",
    staging: "предпроизводственная среда",
    vps: "VPS",
  }[mode];
}

export function OpsPage() {
  const { token } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [observability, setObservability] = useState<Observability | null>(null);
  const [verification, setVerification] = useState<VerificationReport | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [events, setEvents] = useState<OperationalEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [verificationError, setVerificationError] = useState<string | null>(null);

  async function load() {
    const [nextSummary, nextObservability, nextIncidents, nextEvents] = await Promise.all([
      api<Summary>("/api/v1/panel/reports/summary", {}, token),
      api<Observability>("/api/v1/panel/reports/observability", {}, token),
      api<Incident[]>("/api/v1/panel/incidents", {}, token),
      api<OperationalEvent[]>("/api/v1/panel/events", {}, token),
    ]);
    setSummary(nextSummary);
    setObservability(nextObservability);
    setIncidents(nextIncidents);
    setEvents(nextEvents);
    setError(null);
    try {
      setVerification(await api<VerificationReport>("/api/v1/panel/reports/verification", {}, token));
      setVerificationError(null);
    } catch (cause) {
      setVerificationError(cause instanceof Error ? cause.message : "Доказательства недоступны");
    }
  }

  function showLoadError(cause: unknown) {
    setError(cause instanceof Error ? cause.message : "Не удалось загрузить статус");
  }

  async function updateIncident(
    incident: Incident,
    action: "acknowledge" | "resolve" | "snooze",
    snoozeMinutes?: 60 | 240 | 1440,
  ) {
    let updated: Incident;
    try {
      updated = await api<Incident>(`/api/v1/panel/incidents/${incident.id}`, {
        method: "PATCH",
        body: JSON.stringify({ action, ...(snoozeMinutes ? { snooze_minutes: snoozeMinutes } : {}) }),
      }, token);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Решение по инциденту не сохранено");
      return;
    }
    setIncidents((current) => current.map((item) => item.id === updated.id ? updated : item));
    try {
      await load();
      setIncidents((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (cause) {
      setError(`Решение по инциденту сохранено, но статус не обновился. ${cause instanceof Error ? cause.message : "Повторите загрузку позже."}`);
    }
  }

  useEffect(() => {
    void load().catch(showLoadError);
    const timer = window.setInterval(() => void load().catch(showLoadError), 30_000);
    return () => window.clearInterval(timer);
  }, [token]);

  return (
    <div>
      <PageHeader title="Статус системы" description="Только зафиксированные в БД и локальной медиатеке наблюдения. Экран не подтверждает готовность рабочей среды, проверки Docker или браузера либо восстановление." actions={<button className="btn btn-ghost" type="button" onClick={() => void load().catch(showLoadError)}>Обновить</button>} />
      {error && <p className="error" role="alert">{error}</p>}
      <div className="stat-grid"><div className="stat"><div className="label">Сайты</div><div className="value">{summary?.sites ?? "—"}</div></div><div className="stat"><div className="label">Страницы</div><div className="value">{summary?.pages_estimate ?? "—"}</div></div><div className="stat"><div className="label">Лиды всего</div><div className="value">{summary?.leads ?? "—"}</div></div><div className="stat"><div className="label">Активные лиды</div><div className="value">{summary?.active_leads ?? "—"}</div></div></div>
      <Surface title="Доставка лидов"><div className="row"><StatusPill tone={summary?.delivery_dead_letter ? "danger" : summary?.delivery_pending ? "warn" : "ok"}>В очереди: {summary?.delivery_pending ?? "—"}</StatusPill><StatusPill tone={summary?.delivery_dead_letter ? "danger" : "ok"}>Недоставленные: {summary?.delivery_dead_letter ?? "—"}</StatusPill></div><p className="muted">Это состояние очереди базы данных; история попыток и повторная отправка доступны во входящих заявках.</p></Surface>
      <Surface title="Сборки и ИИ"><div className="detail-grid"><div><strong>Ошибки сборок: {observability?.builds.failed ?? "—"}</strong><p className="muted">Последняя готовая сборка-кандидат (не публикация): {observability?.builds.latest_success?.build_hash?.slice(0, 12) || "нет записи"}</p></div><div><strong>Запуски ИИ: {Object.values(observability?.ai.status_counts || {}).reduce((sum, count) => sum + count, 0)}</strong><p className="muted">Учтённая стоимость: ${observability?.ai.recorded_cost_usd.toFixed(6) ?? "—"}; зарезервированная оценка: ${observability?.ai.reserved_estimated_usd.toFixed(6) ?? "—"}</p><p className="muted">Очередь недоставленных задач ИИ: {observability?.ai.unresolved_dead_letter_jobs ?? "—"}</p></div></div></Surface>
      <Surface title="Контент и медиа"><div className="detail-grid"><div><strong>Страницы с недостаточным содержанием: {observability?.content_gaps.thin_pages ?? "—"}</strong><p className="muted">Страницы noindex: {observability?.content_gaps.noindex_pages ?? "—"}</p></div><div><strong>Файлы медиа: {observability?.media.assets ?? "—"}</strong><p className="muted">Отсутствуют файлы: {observability?.media.missing_files ?? "—"}; пробелы в происхождении: {observability?.media.provenance_gaps ?? "—"}; истёкшие лицензии: {observability?.media.expired_licenses ?? "—"}</p><p className="muted">Ссылки: {statusLabel(observability?.media.references.status || "")} · файлов {observability?.media.references.assets ?? "—"} · страниц {observability?.media.references.pages ?? "—"}</p><p className="muted">Источник: {observability?.media.references.source || "—"}; некорректные записи: {observability?.media.references.invalid_entries ?? "—"}. Это только сохранённый снимок манифеста материализованных страниц, а не полный граф ссылок.</p></div></div></Surface>
      <Surface title="Границы доказательств"><p className="muted">Это зафиксированная классификация ограниченных доказательств, а не проверка рабочей среды в реальном времени и не подтверждение выпуска. Тестовые данные, локальный Compose и CI не подтверждают предпродакшен или работу на VPS.</p>{verificationError ? <p className="muted" role="status">Доказательства недоступны: {verificationError}</p> : !verification ? <p className="muted" aria-live="polite">Загрузка доказательств…</p> : <DataTable headers={["Проверка", "Режим", "Состояние", "Наблюдалось", "Охват и граница"]}>{verification.checks.map((check) => <tr key={check.check_key}><td>{check.label}</td><td>{verificationModeLabel(check.mode)}</td><td><StatusPill tone={verificationTone(check.state)}>{statusLabel(check.state)}</StatusPill></td><td>{check.observed_at ? new Date(check.observed_at).toLocaleString() : "—"}</td><td><span>{check.coverage}</span><p className="muted">Не доказывает: {check.limitation}</p></td></tr>)}</DataTable>}</Surface>
      <Surface title="Инциденты">
        <p className="muted">Только зафиксированные сигналы без персональных данных, URL, секретов, хешей и текстов свободного формата. Подтверждение, отложение и закрытие не меняют исходные данные доставки, проверки качества или выпуска. Отложение влияет только на внимание во входящих.</p>
        {incidents.length === 0 ? <p className="muted">Зафиксированных инцидентов пока нет.</p> : <DataTable headers={["Сигнал", "Критичность", "Статус", "Срабатывания", "Действия"]}>{incidents.map((incident) => {
          const snoozed = Boolean(incident.snoozed_until && new Date(incident.snoozed_until) > new Date());
          return <tr key={incident.id} className={snoozed ? "incident-snoozed" : undefined}>
            <td>{incident.signal_code}{snoozed && <p className="muted">Отложен до {new Date(incident.snoozed_until!).toLocaleString()}</p>}</td>
            <td><StatusPill tone={incident.severity === "critical" ? "danger" : incident.severity === "warning" ? "warn" : "accent"}>{statusLabel(incident.severity)}</StatusPill></td>
            <td><StatusPill tone={snoozed ? "default" : incident.status === "resolved" ? "ok" : incident.status === "open" ? "danger" : "warn"}>{snoozed ? "отложен" : statusLabel(incident.status)}</StatusPill></td>
            <td>{incident.occurrence_count}</td>
            <td className="row">{incident.status === "open" && <button className="btn btn-ghost" type="button" onClick={() => void updateIncident(incident, "acknowledge")}>Подтвердить</button>}{incident.status !== "resolved" && <><button className="btn btn-ghost" type="button" onClick={() => void updateIncident(incident, "snooze", 60)}>Отложить 1 ч</button><button className="btn btn-ghost" type="button" onClick={() => void updateIncident(incident, "snooze", 240)}>4 ч</button><button className="btn btn-ghost" type="button" onClick={() => void updateIncident(incident, "snooze", 1440)}>1 день</button><button className="btn btn-ghost" type="button" onClick={() => void updateIncident(incident, "resolve")}>Закрыть</button></>}</td>
          </tr>;
        })}</DataTable>}
      </Surface>
      <Surface title="Последние операционные события"><p className="muted">События содержат только тип, критичность, результат, количество и время; они не являются журналом запросов или доказательством проверки рабочей среды.</p>{events.length === 0 ? <p className="muted">Событий пока нет.</p> : <DataTable headers={["Тип", "Критичность", "Результат", "Количество", "Время"]}>{events.map((event, index) => <tr key={`${event.event_type}-${event.occurred_at || index}`}><td>{event.event_type}</td><td>{statusLabel(event.severity)}</td><td>{statusLabel(event.outcome)}</td><td>{event.quantity}</td><td>{event.occurred_at ? new Date(event.occurred_at).toLocaleString() : "—"}</td></tr>)}</DataTable>}</Surface>
      <Surface title="Обработчик и неподтверждённые сигналы"><div className="row"><StatusPill tone={workerTone(observability?.worker.status || "not_observed")}>Обработчик: {statusLabel(observability?.worker.status || "not_observed")}</StatusPill></div>{observability?.worker.last_heartbeat_at ? <p className="muted">Последняя отметка активности в базе данных: {new Date(observability.worker.last_heartbeat_at).toLocaleString()} · возраст {observability.worker.age_seconds ?? "—"} сек. · данные считаются устаревшими через {observability.worker.stale_after_seconds} сек.</p> : <p className="muted">{observability?.worker.reason || "Отметка активности обработчика пока не записана."}</p>}{observability?.worker.reason && observability.worker.last_heartbeat_at && <p className="muted">{observability.worker.reason}</p>}<p className="muted">Резервные копии: {statusLabel(observability?.system.backups.status || "not_observed")} — {observability?.system.backups.reason}</p></Surface>
      <Surface title="Домены"><div className="row"><StatusPill tone={summary?.domains_pending_tls ? "warn" : "ok"}>Ожидают TLS: {summary?.domains_pending_tls ?? "—"}</StatusPill><StatusPill tone={summary?.domains_tls_error ? "danger" : "ok"}>Ошибки TLS: {summary?.domains_tls_error ?? "—"}</StatusPill></div><p className="muted">DNS и сертификаты показывают последнее сохранённое состояние домена, а не результат текущей внешней проверки.</p></Surface>
    </div>
  );
}
