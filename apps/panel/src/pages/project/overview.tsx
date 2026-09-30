import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = {
  id: string;
  name: string;
  domain: string | null;
  niche: string | null;
  site_id: string | null;
  current_fact_revision_id: string | null;
  domain_check_meta?: { dns_status?: string; ssl_status?: string };
};

export function ProjectOverviewPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    api<Project>(`/api/v1/projects/${projectId}`, {}, token)
      .then(setProject)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить проект"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project?.name || "Project overview"}
        description="Project-level navigation and release status. Detailed mutations stay in the legacy compatibility workflow."
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!project && !error ? <p className="muted" aria-live="polite">Загрузка проекта…</p> : null}
      {project && <>
        <Surface title="Состояние проекта">
          <div className="detail-grid">
            <div><strong>Домен</strong><span className="muted">{project.domain || "не настроен"}</span></div>
            <div><strong>Ниша</strong><span className="muted">{project.niche || "не указана"}</span></div>
            <div><strong>Facts</strong><StatusPill tone={project.current_fact_revision_id ? "ok" : "warn"}>{project.current_fact_revision_id ? "подтверждены" : "требуют проверки"}</StatusPill></div>
            <div><strong>DNS</strong><StatusPill tone={project.domain_check_meta?.dns_status === "ok" ? "ok" : "warn"}>{project.domain_check_meta?.dns_status || "не проверен"}</StatusPill></div>
          </div>
        </Surface>
        <Surface title="Безопасный переход">
          <p className="muted">Overview не загружает facts, private recipients, drafts или release payloads. Откройте нужный раздел, чтобы продолжить существующий candidate-first workflow.</p>
          <div className="row"><Link className="btn" to={`/projects/${project.id}`}>Открыть полный workspace</Link><Link className="btn btn-ghost" to={`/projects/${project.id}/activity`}>Открыть activity</Link></div>
        </Surface>
      </>}
    </ProjectWorkspaceLayout>
  );
}
