import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type Summary = {
  sites: number;
  pages_estimate: number;
  leads: number;
  active_leads: number;
  delivery_pending: number;
  delivery_dead_letter: number;
  domains_pending_tls: number;
  domains_tls_error: number;
};

export function OpsPage() {
  const { token } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Summary>("/api/v1/panel/reports/summary", {}, token)
      .then(setSummary)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить статус"));
  }, [token]);

  return (
    <div>
      <PageHeader title="Статус системы" description="Только подтверждённые состояния системы. Экран не означает готовность к production: Docker, браузерные проверки и тестовое восстановление ещё должны быть выполнены отдельно." />
      {error && <p className="error">{error}</p>}
      <div className="stat-grid"><div className="stat"><div className="label">Сайты</div><div className="value">{summary?.sites ?? "—"}</div></div><div className="stat"><div className="label">Страницы</div><div className="value">{summary?.pages_estimate ?? "—"}</div></div><div className="stat"><div className="label">Лиды всего</div><div className="value">{summary?.leads ?? "—"}</div></div><div className="stat"><div className="label">Активные лиды</div><div className="value">{summary?.active_leads ?? "—"}</div></div></div>
      <Surface title="Доставка лидов"><div className="row"><StatusPill tone={summary?.delivery_dead_letter ? "danger" : summary?.delivery_pending ? "warn" : "ok"}>В очереди: {summary?.delivery_pending ?? "—"}</StatusPill><StatusPill tone={summary?.delivery_dead_letter ? "danger" : "ok"}>Dead letter: {summary?.delivery_dead_letter ?? "—"}</StatusPill></div><p className="muted" style={{ marginBottom: 0 }}>Откройте inbox лидов, чтобы увидеть историю попыток и вручную повторить dead-letter delivery.</p></Surface>
      <Surface title="Домены"><div className="row"><StatusPill tone={summary?.domains_pending_tls ? "warn" : "ok"}>Ожидают TLS: {summary?.domains_pending_tls ?? "—"}</StatusPill><StatusPill tone={summary?.domains_tls_error ? "danger" : "ok"}>Ошибки TLS: {summary?.domains_tls_error ?? "—"}</StatusPill></div><p className="muted" style={{ marginBottom: 0 }}>DNS и сертификаты подтверждаются только проверкой конкретного домена в разделе «Домены».</p></Surface>
    </div>
  );
}
