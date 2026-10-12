import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { id: string; name: string };
type AuditEntry = { id: number; action: string; actor_id: string | null; created_at: string | null; record_hash: string };
type Activity = { items: AuditEntry[]; offset: number; limit: number; total: number };

export function ProjectActivityPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(offset = 0) {
    const [nextProject, nextActivity] = await Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<Activity>(`/api/v1/projects/${projectId}/activity?limit=25&offset=${offset}`, {}, token),
    ]);
    setProject(nextProject);
    setActivity(nextActivity);
  }

  useEffect(() => {
    if (!projectId) return;
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить историю действий"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader title={project ? `История действий · ${project.name}` : "История действий проекта"} description="Здесь отображается только история метаданных проекта. Данные запросов, приватные факты, персональные данные, получатели, секреты и сведения о провайдерах не показываются." />
      {error && <p className="error" role="alert">{error}</p>}
      <Surface title="История действий">
        {!activity?.items.length ? <EmptyState title="Событий проекта пока нет" hint="После первых действий оператора здесь появится связанная хешами история метаданных." /> : <><DataTable headers={["Действие", "Время", "Инициатор", "Хеш"]}>{activity.items.map((entry) => <tr key={entry.id}><td><StatusPill tone="accent">{entry.action}</StatusPill></td><td>{entry.created_at ? new Date(entry.created_at).toLocaleString() : "—"}</td><td className="muted">{entry.actor_id?.slice(0, 8) || "система"}</td><td className="muted">{entry.record_hash.slice(0, 16)}</td></tr>)}</DataTable><div className="row"><button className="btn btn-ghost" type="button" disabled={!activity.offset} onClick={() => void load(Math.max(0, activity.offset - activity.limit))}>Назад</button><span className="muted">{activity.offset + 1}–{Math.min(activity.offset + activity.items.length, activity.total)} из {activity.total}</span><button className="btn btn-ghost" type="button" disabled={activity.offset + activity.limit >= activity.total} onClick={() => void load(activity.offset + activity.limit)}>Далее</button></div></>}
      </Surface>
    </ProjectWorkspaceLayout>
  );
}
