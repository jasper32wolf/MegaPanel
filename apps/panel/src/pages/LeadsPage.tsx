import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Lead = {
  id: string;
  status: string;
  phone: string | null;
  qualification: string | null;
  page_slug: string | null;
  created_at: string | null;
};

export function LeadsPage() {
  const { token } = useAuth();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Lead[]>("/api/v1/leads/inbox", {}, token)
      .then(setLeads)
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [token]);

  return (
    <div>
      <PageHeader title="Лиды" description="Inbox с расшифровкой PII для вашей роли." />
      {error && <p className="error">{error}</p>}
      <Surface>
        <DataTable headers={["Телефон", "Статус", "Квалификация", "Страница", "Когда"]}>
          {leads.map((l) => (
            <tr key={l.id}>
              <td>{l.phone || "—"}</td>
              <td>
                <StatusPill tone={l.status === "qualified" || l.status === "sent" ? "ok" : "default"}>
                  {l.status}
                </StatusPill>
              </td>
              <td>{l.qualification || "—"}</td>
              <td className="muted">{l.page_slug || "—"}</td>
              <td className="muted">{l.created_at?.slice(0, 19) || "—"}</td>
            </tr>
          ))}
          {leads.length === 0 && (
            <tr>
              <td colSpan={5}>
                <EmptyState title="Лидов пока нет" />
              </td>
            </tr>
          )}
        </DataTable>
      </Surface>
    </div>
  );
}
