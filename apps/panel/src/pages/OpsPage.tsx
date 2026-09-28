import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type Summary = { sites: number; pages_estimate: number; leads: number; active_leads: number; delivery_pending: number; delivery_dead_letter: number; domains_pending_tls: number; domains_tls_error: number };
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

export function OpsPage() {
  const { token } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [observability, setObservability] = useState<Observability | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [nextSummary, nextObservability] = await Promise.all([
      api<Summary>("/api/v1/panel/reports/summary", {}, token),
      api<Observability>("/api/v1/panel/reports/observability", {}, token),
    ]);
    setSummary(nextSummary);
    setObservability(nextObservability);
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить статус"));
    const timer = window.setInterval(() => load().catch(() => undefined), 30_000);
    return () => window.clearInterval(timer);
  }, [token]);

  return (
    <div>
      <PageHeader title="Статус системы" description="Только зафиксированные в БД и локальной медиатеке наблюдения. Экран не доказывает production готовность, Docker/browser checks или восстановление." actions={<button className="btn btn-ghost" type="button" onClick={() => void load()}>Обновить</button>} />
      {error && <p className="error">{error}</p>}
      <div className="stat-grid"><div className="stat"><div className="label">Сайты</div><div className="value">{summary?.sites ?? "—"}</div></div><div className="stat"><div className="label">Страницы</div><div className="value">{summary?.pages_estimate ?? "—"}</div></div><div className="stat"><div className="label">Лиды всего</div><div className="value">{summary?.leads ?? "—"}</div></div><div className="stat"><div className="label">Активные лиды</div><div className="value">{summary?.active_leads ?? "—"}</div></div></div>
      <Surface title="Доставка лидов"><div className="row"><StatusPill tone={summary?.delivery_dead_letter ? "danger" : summary?.delivery_pending ? "warn" : "ok"}>В очереди: {summary?.delivery_pending ?? "—"}</StatusPill><StatusPill tone={summary?.delivery_dead_letter ? "danger" : "ok"}>Dead letter: {summary?.delivery_dead_letter ?? "—"}</StatusPill></div><p className="muted">Это состояние database queue; история попыток и resend доступны в inbox лидов.</p></Surface>
      <Surface title="Сборки и AI"><div className="detail-grid"><div><strong>Ошибки сборок: {observability?.builds.failed ?? "—"}</strong><p className="muted">Последняя successful build: {observability?.builds.latest_success?.build_hash?.slice(0, 12) || "нет записи"}</p></div><div><strong>AI runs: {Object.values(observability?.ai.status_counts || {}).reduce((sum, count) => sum + count, 0)}</strong><p className="muted">Recorded cost: ${observability?.ai.recorded_cost_usd.toFixed(6) ?? "—"}; reserved estimate: ${observability?.ai.reserved_estimated_usd.toFixed(6) ?? "—"}</p><p className="muted">AI DLQ: {observability?.ai.unresolved_dead_letter_jobs ?? "—"}</p></div></div></Surface>
      <Surface title="Контент и медиа"><div className="detail-grid"><div><strong>Thin pages: {observability?.content_gaps.thin_pages ?? "—"}</strong><p className="muted">Noindex pages: {observability?.content_gaps.noindex_pages ?? "—"}</p></div><div><strong>Assets: {observability?.media.assets ?? "—"}</strong><p className="muted">Отсутствуют файлы: {observability?.media.missing_files ?? "—"}; provenance gaps: {observability?.media.provenance_gaps ?? "—"}; expired: {observability?.media.expired_licenses ?? "—"}</p><p className="muted">References: {observability?.media.references.status || "—"} · assets {observability?.media.references.assets ?? "—"} · pages {observability?.media.references.pages ?? "—"}</p><p className="muted">Источник: {observability?.media.references.source || "—"}; malformed entries: {observability?.media.references.invalid_entries ?? "—"}. Это только persisted manifest snapshot materialized pages, не полный reference graph.</p></div></div></Surface>
      <Surface title="Worker и неподтверждённые сигналы"><div className="row"><StatusPill tone={workerTone(observability?.worker.status || "not_observed")}>Worker: {observability?.worker.status || "not observed"}</StatusPill></div>{observability?.worker.last_heartbeat_at ? <p className="muted">Последний database heartbeat: {new Date(observability.worker.last_heartbeat_at).toLocaleString()} · возраст {observability.worker.age_seconds ?? "—"} сек. · stale после {observability.worker.stale_after_seconds} сек.</p> : <p className="muted">{observability?.worker.reason || "Worker heartbeat пока не записан."}</p>}{observability?.worker.reason && observability.worker.last_heartbeat_at && <p className="muted">{observability.worker.reason}</p>}<p className="muted">Backups: {observability?.system.backups.status ?? "not observed"} — {observability?.system.backups.reason}</p></Surface>
      <Surface title="Домены"><div className="row"><StatusPill tone={summary?.domains_pending_tls ? "warn" : "ok"}>Ожидают TLS: {summary?.domains_pending_tls ?? "—"}</StatusPill><StatusPill tone={summary?.domains_tls_error ? "danger" : "ok"}>Ошибки TLS: {summary?.domains_tls_error ?? "—"}</StatusPill></div><p className="muted">DNS и сертификаты показывают последний persisted domain status, а не текущую внешнюю проверку.</p></Surface>
    </div>
  );
}
