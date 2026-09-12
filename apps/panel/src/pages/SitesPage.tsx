import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Site = {
  id: string;
  domain: string;
  publish_state: string;
  build_hash: string | null;
  version: number;
};

export function SitesPage() {
  const { token } = useAuth();
  const [sites, setSites] = useState<Site[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api<Site[]>("/api/v1/sites", {}, token)
      .then(setSites)
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [token]);

  async function build(siteId: string) {
    setBusy(siteId);
    setError(null);
    try {
      await api(`/api/v1/sites/${siteId}/build`, { method: "POST" }, token);
      setSites(await api<Site[]>("/api/v1/sites", {}, token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Build failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader title="Сайты" description="Манифесты, SSG-сборка и версии." />
      {error && <p className="error">{error}</p>}
      <Surface>
        <DataTable headers={["Домен", "Статус", "Build", ""]}>
          {sites.map((s) => (
            <tr key={s.id}>
              <td>
                <strong>{s.domain}</strong>
              </td>
              <td>
                <StatusPill tone={s.publish_state === "published" ? "ok" : "accent"}>{s.publish_state}</StatusPill>
              </td>
              <td className="muted">{s.build_hash ? s.build_hash.slice(0, 12) : "—"}</td>
              <td>
                <button className="btn" type="button" disabled={busy === s.id} onClick={() => build(s.id)}>
                  {busy === s.id ? "Сборка…" : "Собрать"}
                </button>
              </td>
            </tr>
          ))}
          {sites.length === 0 && (
            <tr>
              <td colSpan={4}>
                <EmptyState title="Сайтов пока нет" hint="Пройдите онбординг или синхронизируйте комплект блоков." />
              </td>
            </tr>
          )}
        </DataTable>
      </Surface>
    </div>
  );
}
