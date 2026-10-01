import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { id: string; name: string };
type Destination = { id: string; target_key: string; channel: "email" | "webhook"; required: boolean; configured: boolean };
type Policy = {
  id: string;
  version: number;
  state: string;
  destinations: Destination[];
  submitted_at: string | null;
  reviewed_at: string | null;
  decision_reason: string | null;
  created_at: string | null;
};
type Policies = { items: Policy[] };

function tone(state: string) {
  if (state === "active") return "ok" as const;
  if (state === "rejected") return "danger" as const;
  return "warn" as const;
}

export function ProjectRoutingPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<Policies>(`/api/v1/projects/${projectId}/lead-routing`, {}, token),
    ]).then(([nextProject, nextPolicies]) => {
      setProject(nextProject);
      setPolicies(nextPolicies.items);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить routing policies"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `Routing · ${project.name}` : "Lead routing"}
        description="Read-only routing-policy history. Recipient addresses, webhook URLs and secrets are never returned or displayed; policy changes remain explicit workspace actions."
        actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть workspace</Link>}
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!policies && !error ? <p className="muted" aria-live="polite">Загрузка routing policies…</p> : null}
      {policies && <Surface title="Маршрутизация заявок">
        {policies.length === 0 ? <EmptyState title="Routing policy пока нет" hint="Создайте draft policy и отдельно проведите её через review/activation в полном workspace." /> : <DataTable headers={["Версия", "Статус", "Каналы", "Review", "Создана"]}>{policies.map((policy) => <tr key={policy.id}><td>v{policy.version}</td><td><StatusPill tone={tone(policy.state)}>{policy.state}</StatusPill></td><td>{policy.destinations.map((destination) => <div className="row" key={destination.id}><StatusPill tone={destination.configured ? "ok" : "danger"}>{destination.channel}</StatusPill><span>{destination.target_key} · {destination.required ? "required" : "optional"}</span></div>)}</td><td>{policy.decision_reason || (policy.reviewed_at ? new Date(policy.reviewed_at).toLocaleString() : "—")}</td><td>{policy.created_at ? new Date(policy.created_at).toLocaleString() : "—"}</td></tr>)}</DataTable>}
      </Surface>}
    </ProjectWorkspaceLayout>
  );
}
