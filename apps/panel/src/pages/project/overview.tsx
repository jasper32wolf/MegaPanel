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
  domain_check_meta?: { dns_status?: string; ssl_status?: string };
};

type WorkflowSummary = {
  facts_confirmed: boolean;
  keyword_count: number;
  geo_count: number;
  approved_collection_count: number;
  approved_structure_count: number;
  approved_plan_count: number;
  applied_draft_count: number;
  ready_candidate_count: number;
  running_candidate_count: number;
  published: boolean;
};

type DeploymentStage = {
  key: string;
  title: string;
  state: "ready" | "blocked" | "prepare" | "approval_required" | "running" | "manual_decision" | "published";
  detail: string;
  route: string;
  manual: string;
};

type DeploymentPlan = { stages: DeploymentStage[]; next_stage: DeploymentStage | null; read_only: true };

const stageStatus: Record<DeploymentStage["state"], { label: string; tone: "ok" | "warn" | "accent" }> = {
  ready: { label: "подготовлено", tone: "ok" },
  blocked: { label: "нужны данные", tone: "warn" },
  prepare: { label: "нужно подготовить", tone: "warn" },
  approval_required: { label: "нужно решение", tone: "warn" },
  running: { label: "выполняется", tone: "accent" },
  manual_decision: { label: "требуется решение", tone: "warn" },
  published: { label: "выпуск записан", tone: "ok" },
};

export function ProjectOverviewPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [summary, setSummary] = useState<WorkflowSummary | null>(null);
  const [plan, setPlan] = useState<DeploymentPlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setProject(null);
    setSummary(null);
    setPlan(null);
    setError(null);
    void Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<WorkflowSummary>(`/api/v1/projects/${projectId}/workflow-summary`, {}, token),
      api<DeploymentPlan>(`/api/v1/projects/${projectId}/deployment-plan`, {}, token),
    ]).then(([nextProject, nextSummary, nextPlan]) => {
      if (active) {
        setProject(nextProject);
        setSummary(nextSummary);
        setPlan(nextPlan);
      }
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : "Не удалось загрузить состояние проекта");
    });
    return () => { active = false; };
  }, [projectId, token]);

  const nextStage = plan?.next_stage || null;
  const nextStageStatus = nextStage ? stageStatus[nextStage.state] : null;

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project?.name || "Обзор проекта"}
        description="Наблюдаемые артефакты и следующий ручной шаг. Обзор не изменяет данные и не заменяет серверные QA, legal и release gates."
      />
      {error && <p className="error" role="alert">{error}</p>}
      {(!project || !summary || !plan) && !error ? <p className="muted" aria-live="polite">Загрузка проекта…</p> : null}
      {project && summary && plan && <>
        <Surface title="Состояние проекта">
          <div className="detail-grid">
            <div><strong>Домен</strong><span className="muted">{project.domain || "не настроен"}</span></div>
            <div><strong>Ниша</strong><span className="muted">{project.niche || "не указана"}</span></div>
            <div><strong>DNS</strong><StatusPill tone={project.domain_check_meta?.dns_status === "ok" ? "ok" : "warn"}>{project.domain_check_meta?.dns_status || "не проверен"}</StatusPill></div>
            <div><strong>Публикация</strong><StatusPill tone={summary.published ? "ok" : "warn"}>{summary.published ? "записана в БД" : "не опубликован"}</StatusPill></div>
          </div>
          <p className="muted">Счётчики показывают наличие артефактов, а не пригодность конкретной версии к публикации. При изменении facts, структуры, прав на media или legal-политики проверяйте новый candidate.</p>
        </Surface>
        <Surface title="План развёртывания">
          <p className="muted">Это только подсказка по сохранённым артефактам. Она не создаёт черновики, не одобряет данные, не публикует сайт и не подтверждает внешнюю инфраструктуру.</p>
          {plan.stages.map((stage) => {
            const status = stageStatus[stage.state];
            return <div className="surface" key={stage.key}>
              <div className="row"><StatusPill tone={status.tone}>{status.label}</StatusPill><strong>{stage.title}</strong></div>
              <p>{stage.detail}</p>
              <p className="muted"><strong>Нужно вручную:</strong> {stage.manual}</p>
              <Link className="btn btn-ghost" to={stage.route}>Открыть этап</Link>
            </div>;
          })}
        </Surface>
        <Surface title="Следующее действие">
          {nextStage ? <>
            <div className="row"><StatusPill tone={nextStageStatus?.tone}>{nextStageStatus?.label}</StatusPill><strong>{nextStage.title}</strong></div>
            <p>{nextStage.detail}</p>
            <p className="muted">{nextStage.manual}</p>
            <div className="row"><Link className="btn" to={nextStage.route}>Перейти к этапу</Link><Link className="btn btn-ghost" to={`/help?topic=project-workflow&project=${project.id}`}>Открыть справку по процессу</Link><Link className="btn btn-ghost" to={`/projects/${project.id}/activity`}>Открыть историю действий</Link></div>
          </> : <>
            <p>Основные артефакты записаны. Отдельно проверьте выпуск, внешний сайт и приём заявок.</p>
            <div className="row"><Link className="btn" to={`/projects/${project.id}/releases`}>Открыть выпуск</Link><Link className="btn btn-ghost" to={`/help?topic=project-workflow&project=${project.id}`}>Открыть справку по процессу</Link><Link className="btn btn-ghost" to={`/projects/${project.id}/activity`}>Открыть историю действий</Link></div>
          </>}
        </Surface>
      </>}
    </ProjectWorkspaceLayout>
  );
}
