import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type Health = { status: string; version: string; env: string };
type Summary = {
  sites: number;
  pages_estimate: number;
  active_leads: number;
  delivery_pending: number;
  delivery_dead_letter: number;
  domains_pending_tls: number;
  domains_tls_error: number;
};

export function DashboardPage() {
  const { token } = useAuth();
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  useEffect(() => {
    api<Health>("/api/v1/health", {}, token).then(setHealth).catch(() => setHealth(null));
    api<Summary>("/api/v1/panel/reports/summary", {}, token).then(setSummary).catch(() => setSummary(null));
  }, [token]);

  const deliveryTone = summary?.delivery_dead_letter ? "danger" : summary?.delivery_pending ? "warn" : "ok";
  const domainTone = summary?.domains_tls_error ? "danger" : summary?.domains_pending_tls ? "warn" : "ok";

  return (
    <div>
      <PageHeader title="Обзор" description="Текущие действия, требующие внимания оператора." actions={<><Link className="btn btn-ghost" to="/projects">Новый проект</Link><Link className="btn btn-ghost" to="/system">Обновить панель</Link><Link className="btn" to="/sites">Сайты</Link></>} />
      <div className="stat-grid">
        <div className="stat"><div className="label">Сайты</div><div className="value">{summary?.sites ?? "—"}</div><p className="muted" style={{ marginBottom: 0 }}>{summary ? `${summary.pages_estimate} страниц` : "Загрузка…"}</p></div>
        <div className="stat"><div className="label">Активные лиды</div><div className="value">{summary?.active_leads ?? "—"}</div><p className="muted" style={{ marginBottom: 0 }}>new и qualified</p></div>
        <div className="stat"><div className="label">Delivery queue</div><div className="value">{summary?.delivery_pending ?? "—"}</div><p style={{ marginBottom: 0 }}><StatusPill tone={deliveryTone}>{summary?.delivery_dead_letter ? `${summary.delivery_dead_letter} dead letter` : "без ошибок"}</StatusPill></p></div>
        <div className="stat"><div className="label">TLS / DNS</div><div className="value">{summary ? summary.domains_pending_tls + summary.domains_tls_error : "—"}</div><p style={{ marginBottom: 0 }}><StatusPill tone={domainTone}>{summary?.domains_tls_error ? `${summary.domains_tls_error} ошибка TLS` : "проверить pending"}</StatusPill></p></div>
      </div>
      <Surface title="Системное состояние"><div className="row"><StatusPill tone={health?.status === "ok" ? "ok" : "danger"}>API: {health?.status || "недоступен"}</StatusPill>{health && <span className="muted">v{health.version} · {health.env}</span>}</div></Surface>
      <Surface title="Быстрые действия"><div className="row"><Link className="btn btn-ghost" to="/leads">Открыть inbox</Link><Link className="btn btn-ghost" to="/domains">Проверить домены</Link><Link className="btn btn-ghost" to="/settings">Настройки защиты</Link></div></Surface>
    </div>
  );
}
