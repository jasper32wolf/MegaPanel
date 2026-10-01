import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { id: string; name: string };
type FactValue = string | number | boolean | null | FactValue[] | { [key: string]: FactValue };
type FactRevision = {
  id: string;
  version: number;
  state: string;
  facts: Record<string, FactValue>;
  has_private_lead_email: boolean;
  facts_hash: string;
  confirmed_at: string | null;
  created_at: string | null;
};

const factLabels: Record<string, string> = {
  organization: "Организация",
  service: "Основная услуга",
  contacts: "Публичные контакты",
  legal: "Юридические сведения",
  company_history: "История компании",
  mission: "Миссия",
  legal_entities: "Для юридических лиц",
  payment_terms: "Оплата",
  allowed_claims: "Подтверждённые claims",
  phone: "Телефон",
  address: "Адрес",
  work_hours: "Часы работы",
  org: "Юридическое наименование",
  inn: "ИНН",
  privacy_email: "Публичный email для privacy/DSAR",
  jurisdiction: "Юрисдикция",
};

function formatFactValue(value: FactValue): string {
  if (value === null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(formatFactValue).join(", ") || "—";
  return Object.entries(value).map(([key, item]) => `${factLabels[key] || key}: ${formatFactValue(item)}`).join(" · ") || "—";
}

export function ProjectFactsPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [revisions, setRevisions] = useState<FactRevision[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<FactRevision[]>(`/api/v1/projects/${projectId}/facts`, {}, token),
    ]).then(([nextProject, nextRevisions]) => {
      setProject(nextProject);
      setRevisions(nextRevisions);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить facts"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `Facts · ${project.name}` : "Project facts"}
        description="Read-only history of public business facts. The private lead recipient is never displayed; edits and confirmation remain in the candidate-first workspace."
        actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть workspace</Link>}
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!revisions && !error ? <p className="muted" aria-live="polite">Загрузка facts…</p> : null}
      {revisions && <>
        <Surface title="Версии facts">
          {revisions.length === 0 ? <EmptyState title="Facts ещё не сохранены" hint="Создайте и подтвердите первую версию в полном workspace." /> : <DataTable headers={["Версия", "Статус", "Подтверждена", "Private recipient", "Hash"]}>{revisions.map((revision) => <tr key={revision.id}><td>v{revision.version}</td><td><StatusPill tone={revision.state === "confirmed" ? "ok" : "warn"}>{revision.state}</StatusPill></td><td>{revision.confirmed_at ? new Date(revision.confirmed_at).toLocaleString() : "—"}</td><td><StatusPill tone={revision.has_private_lead_email ? "ok" : "warn"}>{revision.has_private_lead_email ? "configured" : "not configured"}</StatusPill></td><td className="muted">{revision.facts_hash.slice(0, 16)}</td></tr>)}</DataTable>}
        </Surface>
        {revisions[0] && <Surface title={`Публичные facts · v${revisions[0].version}`}>
          <p className="muted">Показываются только публичные поля текущей версии. Private lead email, webhook URLs и secrets не загружаются в этот экран.</p>
          {Object.keys(revisions[0].facts).length === 0 ? <EmptyState title="В версии нет публичных facts" /> : <DataTable headers={["Поле", "Значение"]}>{Object.entries(revisions[0].facts).map(([key, value]) => <tr key={key}><td>{factLabels[key] || key}</td><td>{formatFactValue(value)}</td></tr>)}</DataTable>}
        </Surface>}
      </>}
    </ProjectWorkspaceLayout>
  );
}
