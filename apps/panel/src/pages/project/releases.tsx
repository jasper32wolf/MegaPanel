import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { id: string; name: string };
type Build = {
  id: string;
  status: string;
  build_hash: string | null;
  previous_build_hash: string | null;
  pages_built: number;
  created_at: string | null;
  activated_at: string | null;
  release_gate: { status: string; blockers: string[]; warnings: string[] } | null;
  legal_review: { status: "pass" | "block"; blockers: string[]; review: { state: string; evidence_ref: string | null; reason: string | null; replacement_guidance: string | null; reviewed_at: string | null } };
  index_promotion_provenance: { slug: string; reason: string; decided_at: string }[];
};

function tone(state: string) {
  if (["ready", "published", "pass", "approved"].includes(state)) return "ok" as const;
  if (["block", "failed", "rejected"].includes(state)) return "danger" as const;
  return "warn" as const;
}

export function ProjectReleasesPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [builds, setBuilds] = useState<Build[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<Build[]>(`/api/v1/projects/${projectId}/builds`, {}, token),
    ]).then(([nextProject, nextBuilds]) => {
      setProject(nextProject);
      setBuilds(nextBuilds);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить candidate-сборки"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `Releases · ${project.name}` : "Project releases"}
        description="Read-only candidate and release-gate history. Candidate creation, legal review, private preview, publication and rollback remain separate explicit workspace actions."
        actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть workspace</Link>}
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!builds && !error ? <p className="muted" aria-live="polite">Загрузка candidate-сборок…</p> : null}
      {builds && <Surface title="Candidate и release history">
        {builds.length === 0 ? <EmptyState title="Сборок пока нет" hint="Создайте candidate только после подтверждённого PagePlan и QA в полном workspace." /> : <DataTable headers={["Статус", "Gate", "Legal review", "Индексация", "Страниц", "Создана", "Hash"]}>{builds.map((build) => <tr key={build.id}><td><StatusPill tone={tone(build.status)}>{build.status}</StatusPill></td><td><StatusPill tone={tone(build.release_gate?.status || "warn")}>{build.release_gate?.status || "не проверен"}</StatusPill>{build.release_gate?.blockers.map((blocker) => <p className="error" key={blocker}>{blocker}</p>)}{build.release_gate?.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}</td><td><StatusPill tone={build.legal_review.status === "pass" ? "ok" : "warn"}>{build.legal_review.review.state}</StatusPill>{build.legal_review.review.reason && <p className="error">{build.legal_review.review.reason}</p>}{build.legal_review.review.replacement_guidance && <p className="muted">{build.legal_review.review.replacement_guidance}</p>}</td><td>{build.index_promotion_provenance.length === 0 ? <span className="muted">Нет snapshot provenance / legacy build</span> : <details><summary>Подтверждений индексации: {build.index_promotion_provenance.length}</summary>{build.index_promotion_provenance.map((promotion) => <p className="muted" key={promotion.slug}><strong>{promotion.slug}</strong> · {promotion.reason}</p>)}</details>}</td><td>{build.pages_built}</td><td>{build.created_at ? new Date(build.created_at).toLocaleString() : "—"}</td><td className="muted">{build.build_hash?.slice(0, 16) || "—"}</td></tr>)}</DataTable>}
      </Surface>}
    </ProjectWorkspaceLayout>
  );
}
