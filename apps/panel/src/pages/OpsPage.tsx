import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { PageHeader, Surface } from "../components/ui";

type FinOps = {
  tenant_id: string;
  total_usd: number;
  by_kind: Record<string, number>;
  alerts: string[];
};

type Summary = {
  sites: number;
  leads: number;
  qualified_leads: number;
  conversion_hint: number;
  export: string[];
};

export function OpsPage() {
  const { token } = useAuth();
  const [finops, setFinops] = useState<FinOps | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<FinOps>("/api/v1/compliance/finops", {}, token),
      api<Summary>("/api/v1/panel/reports/summary", {}, token),
    ])
      .then(([f, s]) => {
        setFinops(f);
        setSummary(s);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [token]);

  async function downloadCsv() {
    try {
      const headers = new Headers();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      const res = await fetch("/api/v1/panel/reports/export.csv", { headers });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "sites-report.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  }

  return (
    <div>
      <PageHeader
        title="Ops / FinOps"
        description="Сводка tenant и учёт стоимости LLM."
        actions={
          <button className="btn" type="button" onClick={downloadCsv}>
            Скачать CSV
          </button>
        }
      />
      {error && <p className="error">{error}</p>}
      <div className="stat-grid">
        <div className="stat">
          <div className="label">Сайты</div>
          <div className="value">{summary?.sites ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Лиды</div>
          <div className="value">{summary?.leads ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Qualified</div>
          <div className="value">{summary?.qualified_leads ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">FinOps USD</div>
          <div className="value">{finops ? finops.total_usd.toFixed(3) : "—"}</div>
        </div>
      </div>
      <Surface title="By kind">
        {finops ? (
          <>
            <pre style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>
              {JSON.stringify(finops.by_kind, null, 2)}
            </pre>
            {finops.alerts?.length > 0 && <p className="error">Alerts: {finops.alerts.join(", ")}</p>}
          </>
        ) : (
          <p className="muted">Нет данных</p>
        )}
      </Surface>
    </div>
  );
}
