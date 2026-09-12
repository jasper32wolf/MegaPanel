import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type Health = { status: string; version: string; env: string };
type Summary = { sites: number; leads: number; qualified_leads: number; conversion_hint: number };

export function DashboardPage() {
  const { token } = useAuth();
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  useEffect(() => {
    api<Health>("/api/v1/health", {}, token)
      .then(setHealth)
      .catch(() => setHealth(null));
    api<Summary>("/api/v1/panel/reports/summary", {}, token)
      .then(setSummary)
      .catch(() => setSummary(null));
  }, [token]);

  return (
    <div>
      <PageHeader
        title="Обзор"
        description="Статус платформы и сводка по tenant."
        actions={
          <>
            <Link className="btn btn-ghost" to="/onboarding">
              Новый сайт
            </Link>
            <Link className="btn" to="/blocks">
              Библиотека блоков
            </Link>
          </>
        }
      />

      <div className="stat-grid">
        <div className="stat">
          <div className="label">API</div>
          <div className="value" style={{ fontSize: "1.25rem" }}>
            {health ? (
              <StatusPill tone="ok">{health.status}</StatusPill>
            ) : (
              <StatusPill tone="danger">offline</StatusPill>
            )}
          </div>
          {health && (
            <p className="muted" style={{ margin: "0.5rem 0 0", fontSize: "0.85rem" }}>
              v{health.version} · {health.env}
            </p>
          )}
        </div>
        <div className="stat">
          <div className="label">Сайты</div>
          <div className="value">{summary?.sites ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Лиды</div>
          <div className="value">{summary?.leads ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Conv</div>
          <div className="value">{summary ? summary.conversion_hint : "—"}</div>
        </div>
      </div>

      <Surface title="Быстрые действия">
        <div className="row">
          <Link className="btn btn-ghost" to="/sites">
            Собрать сайты
          </Link>
          <Link className="btn btn-ghost" to="/leads">
            Inbox лидов
          </Link>
          <Link className="btn btn-ghost" to="/ops">
            FinOps
          </Link>
        </div>
      </Surface>
    </div>
  );
}
