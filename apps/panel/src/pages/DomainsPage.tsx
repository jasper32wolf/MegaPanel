import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Domain = {
  id: string;
  hostname: string;
  site_id: string | null;
  ssl_status: string;
  dns_status: string;
  registrar: string | null;
};

type Health = {
  hostname: string;
  ssl_status: string;
  dns_status: string;
  caddy_reachable: boolean;
  probe?: unknown;
};

export function DomainsPage() {
  const { token } = useAuth();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [hostname, setHostname] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setDomains(await api<Domain[]>("/api/v1/domains", {}, token));
  }

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [token]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/api/v1/domains", { method: "POST", body: JSON.stringify({ hostname }) }, token);
      setHostname("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error");
    }
  }

  async function checkHealth(id: string) {
    setError(null);
    try {
      setHealth(await api<Health>(`/api/v1/domains/health/${id}`, {}, token));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error");
    }
  }

  return (
    <div>
      <PageHeader title="Домены" description="DNS/TLS health и привязка к сайтам." />
      {error && <p className="error">{error}</p>}
      <Surface title="Добавить hostname">
        <form onSubmit={onCreate} className="row">
          <input
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            placeholder="example.ru"
            style={{ flex: 1, minWidth: 200 }}
            required
          />
          <button className="btn" type="submit">
            Создать
          </button>
        </form>
      </Surface>
      <Surface>
        <DataTable headers={["Hostname", "DNS", "SSL", ""]}>
          {domains.map((d) => (
            <tr key={d.id}>
              <td>
                <strong>{d.hostname}</strong>
              </td>
              <td>
                <StatusPill>{d.dns_status}</StatusPill>
              </td>
              <td>
                <StatusPill tone={d.ssl_status === "ok" ? "ok" : "warn"}>{d.ssl_status}</StatusPill>
              </td>
              <td>
                <button className="btn btn-ghost" type="button" onClick={() => checkHealth(d.id)}>
                  Health
                </button>
              </td>
            </tr>
          ))}
          {domains.length === 0 && (
            <tr>
              <td colSpan={4}>
                <EmptyState title="Доменов пока нет" />
              </td>
            </tr>
          )}
        </DataTable>
      </Surface>
      {health && (
        <Surface title={`Health: ${health.hostname}`}>
          <p className="muted">
            DNS {health.dns_status} · SSL {health.ssl_status} · Caddy {health.caddy_reachable ? "ok" : "down"}
          </p>
          <pre style={{ fontSize: 13, whiteSpace: "pre-wrap", color: "var(--muted)", margin: 0 }}>
            {JSON.stringify(health.probe, null, 2)}
          </pre>
        </Surface>
      )}
    </div>
  );
}
