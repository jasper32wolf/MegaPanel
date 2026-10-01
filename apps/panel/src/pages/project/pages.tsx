import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { id: string; name: string };
type PagePlan = {
  id: string;
  slug: string;
  objective: string;
  intent: string | null;
  kit_key: string;
  state: string;
  version: number;
  decision_reason: string | null;
  reviewed_at: string | null;
};
type PageDraft = {
  id: string;
  page_plan_id: string;
  revision: number;
  state: string;
  content_hash: string | null;
  last_qa_verdict: string | null;
  qa_runs: unknown[];
  failure_message: string | null;
  updated_at: string | null;
};

function tone(state: string | null) {
  if (["approved", "applied", "pass"].includes(state || "")) return "ok" as const;
  if (["rejected", "block", "failed"].includes(state || "")) return "danger" as const;
  if (["review", "warn"].includes(state || "")) return "warn" as const;
  return "accent" as const;
}

export function ProjectPagesPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [plans, setPlans] = useState<PagePlan[] | null>(null);
  const [drafts, setDrafts] = useState<PageDraft[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<PagePlan[]>(`/api/v1/projects/${projectId}/page-plans`, {}, token),
      api<PageDraft[]>(`/api/v1/projects/${projectId}/page-drafts`, {}, token),
    ]).then(([nextProject, nextPlans, nextDrafts]) => {
      setProject(nextProject);
      setPlans(nextPlans);
      setDrafts(nextDrafts);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить страницы проекта"));
  }, [projectId, token]);

  const latestDrafts = useMemo(() => {
    const byPlan = new Map<string, PageDraft>();
    for (const draft of drafts || []) {
      const current = byPlan.get(draft.page_plan_id);
      if (!current || draft.revision > current.revision) byPlan.set(draft.page_plan_id, draft);
    }
    return byPlan;
  }, [drafts]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `Pages · ${project.name}` : "Project pages"}
        description="Read-only PagePlan and draft lineage. Generation, QA, review and apply remain explicit actions in the candidate-first workspace."
        actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть workspace</Link>}
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!plans && !error ? <p className="muted" aria-live="polite">Загрузка PagePlan и drafts…</p> : null}
      {plans && <>
        <Surface title="PagePlan">
          {plans.length === 0 ? <EmptyState title="PagePlan пока нет" hint="Создайте и проверьте план в полном workspace." /> : <DataTable headers={["Путь", "Цель", "Версия", "Статус", "Решение"]}>{plans.map((plan) => <tr key={plan.id}><td>{plan.slug}</td><td>{plan.objective}</td><td>v{plan.version}</td><td><StatusPill tone={tone(plan.state)}>{plan.state}</StatusPill></td><td>{plan.decision_reason || (plan.reviewed_at ? new Date(plan.reviewed_at).toLocaleString() : "—")}</td></tr>)}</DataTable>}
        </Surface>
        <Surface title="Последние drafts и QA">
          {plans.length === 0 ? <EmptyState title="Черновиков пока нет" hint="После одобрения PagePlan создайте детерминированный или approved AI draft в полном workspace." /> : <DataTable headers={["Страница", "Revision", "Draft", "QA", "Hash"]}>{plans.map((plan) => {
            const draft = latestDrafts.get(plan.id);
            return <tr key={plan.id}><td>{plan.slug}</td>{draft ? <><td>v{draft.revision}</td><td><StatusPill tone={tone(draft.state)}>{draft.state}</StatusPill>{draft.failure_message && <p className="error">{draft.failure_message}</p>}</td><td><StatusPill tone={tone(draft.last_qa_verdict)}>{draft.last_qa_verdict || "не запускалась"}</StatusPill><span className="muted"> · {draft.qa_runs.length} run(s)</span></td><td className="muted">{draft.content_hash?.slice(0, 16) || "—"}</td></> : <><td>—</td><td><span className="muted">не создан</span></td><td>—</td><td>—</td></>}</tr>;
          })}</DataTable>}
        </Surface>
      </>}
    </ProjectWorkspaceLayout>
  );
}
