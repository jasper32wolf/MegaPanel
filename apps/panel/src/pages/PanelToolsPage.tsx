import { useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, PageHeader, Surface } from "../components/ui";

type SearchResult = {
  sites: { id: string; domain: string }[];
  domains: { id: string; hostname: string }[];
  leads: { id: string; status: string }[];
};

type AuditRow = {
  id: number;
  action: string;
  record_hash: string;
  created_at: string | null;
};

type Summary = {
  sites: number;
  leads: number;
  qualified_leads: number;
  conversion_hint: number;
};

export function PanelToolsPage() {
  const { token } = useAuth();
  const [q, setQ] = useState("");
  const [search, setSearch] = useState<SearchResult | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSearch(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setSearch(await api<SearchResult>(`/api/v1/panel/search?q=${encodeURIComponent(q)}`, {}, token));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error");
    }
  }

  async function loadAudit() {
    setError(null);
    try {
      setAudit(await api<AuditRow[]>("/api/v1/panel/audit", {}, token));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error");
    }
  }

  async function loadSummary() {
    setError(null);
    try {
      setSummary(await api<Summary>("/api/v1/panel/reports/summary", {}, token));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error");
    }
  }

  return (
    <div>
      <PageHeader title="Инструменты" description="Поиск, отчёты и audit hash-chain." />
      {error && <p className="error">{error}</p>}

      <Surface title="Command Palette">
        <form onSubmit={onSearch} className="row">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск…" style={{ flex: 1 }} />
          <button className="btn" type="submit">
            Найти
          </button>
        </form>
        {search && (
          <pre style={{ color: "var(--muted)", fontSize: 13, whiteSpace: "pre-wrap", marginTop: "1rem" }}>
            {JSON.stringify(search, null, 2)}
          </pre>
        )}
      </Surface>

      <Surface
        title="Отчёт"
      >
        <button className="btn btn-ghost" type="button" onClick={loadSummary}>
          Сводка
        </button>
        {summary && (
          <p style={{ marginTop: "0.75rem" }}>
            Сайты: {summary.sites} · Лиды: {summary.leads} · Квал.: {summary.qualified_leads} · Conv:{" "}
            {summary.conversion_hint}
          </p>
        )}
      </Surface>

      <Surface title="Audit Trail">
        <button className="btn btn-ghost" type="button" onClick={loadAudit} style={{ marginBottom: "0.75rem" }}>
          Загрузить
        </button>
        <DataTable headers={["ID", "Action", "Hash", "When"]}>
          {audit.map((a) => (
            <tr key={a.id}>
              <td>{a.id}</td>
              <td>{a.action}</td>
              <td className="muted">{a.record_hash.slice(0, 12)}</td>
              <td className="muted">{a.created_at?.slice(0, 19)}</td>
            </tr>
          ))}
        </DataTable>
      </Surface>
    </div>
  );
}
