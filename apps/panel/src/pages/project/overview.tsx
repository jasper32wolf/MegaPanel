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

export function ProjectOverviewPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [summary, setSummary] = useState<WorkflowSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setProject(null);
    setSummary(null);
    setError(null);
    void Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<WorkflowSummary>(`/api/v1/projects/${projectId}/workflow-summary`, {}, token),
    ]).then(([nextProject, nextSummary]) => {
      if (active) {
        setProject(nextProject);
        setSummary(nextSummary);
      }
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : "Не удалось загрузить состояние проекта");
    });
    return () => { active = false; };
  }, [projectId, token]);

  const steps = summary ? [
    {
      title: "Подтвердить факты",
      observed: summary.facts_confirmed,
      detail: summary.facts_confirmed ? "Есть подтверждённая версия фактов проекта." : "Проверьте контакты, legal и допустимые утверждения.",
      href: `/projects/${projectId}/facts`,
    },
    {
      title: "Подготовить семантику и географию",
      observed: summary.keyword_count > 0 && summary.geo_count > 0 && summary.approved_collection_count > 0,
      detail: `Ключи: ${summary.keyword_count} · география: ${summary.geo_count} · одобренные коллекции: ${summary.approved_collection_count}`,
      href: `/projects/${projectId}`,
    },
    {
      title: "Одобрить единую структуру сайта",
      observed: summary.approved_structure_count > 0,
      detail: `Одобренных версий структуры: ${summary.approved_structure_count}. Для городского поддомена нужны собственные facts.`,
      href: `/projects/${projectId}/site-structure`,
    },
    {
      title: "Одобрить планы и применить прошедшие QA страницы",
      observed: summary.approved_plan_count > 0 && summary.applied_draft_count > 0,
      detail: `Одобренных PagePlans: ${summary.approved_plan_count} · применённых PageDrafts: ${summary.applied_draft_count}`,
      href: `/projects/${projectId}/pages`,
    },
    {
      title: "Проверить candidate и private preview",
      observed: summary.ready_candidate_count > 0 || summary.published,
      detail: `Готовых сборок: ${summary.ready_candidate_count} · в очереди/работе: ${summary.running_candidate_count}. Legal review и публикация — отдельные решения.`,
      href: `/projects/${projectId}/releases`,
    },
    {
      title: "Проверить публичный release и лиды",
      observed: summary.published,
      detail: summary.published ? "В БД отмечен опубликованный release. Внешние DNS/TLS и форму проверьте отдельно." : "Публикация требует явного подтверждения после всех серверных проверок.",
      href: `/projects/${projectId}/releases`,
    },
  ] : [];
  const nextStep = steps.find((step) => !step.observed);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project?.name || "Обзор проекта"}
        description="Наблюдаемые артефакты и следующий ручной шаг. Обзор не изменяет данные и не заменяет серверные QA, legal и release gates."
      />
      {error && <p className="error" role="alert">{error}</p>}
      {(!project || !summary) && !error ? <p className="muted" aria-live="polite">Загрузка проекта…</p> : null}
      {project && summary && <>
        <Surface title="Состояние проекта">
          <div className="detail-grid">
            <div><strong>Домен</strong><span className="muted">{project.domain || "не настроен"}</span></div>
            <div><strong>Ниша</strong><span className="muted">{project.niche || "не указана"}</span></div>
            <div><strong>DNS</strong><StatusPill tone={project.domain_check_meta?.dns_status === "ok" ? "ok" : "warn"}>{project.domain_check_meta?.dns_status || "не проверен"}</StatusPill></div>
            <div><strong>Публикация</strong><StatusPill tone={summary.published ? "ok" : "warn"}>{summary.published ? "записана в БД" : "не опубликован"}</StatusPill></div>
          </div>
          <p className="muted">Счётчики показывают наличие артефактов, а не пригодность конкретной версии к публикации. При изменении facts, структуры, прав на media или legal-политики проверяйте новый candidate.</p>
        </Surface>
        <Surface title="Этапы работы">
          {steps.map((step) => <div className="surface" key={step.title}>
            <div className="row"><StatusPill tone={step.observed ? "ok" : "warn"}>{step.observed ? "есть артефакт" : "нужен шаг"}</StatusPill><strong>{step.title}</strong></div>
            <p className="muted">{step.detail}</p>
            <Link className="btn btn-ghost" to={step.href}>Открыть этап</Link>
          </div>)}
        </Surface>
        <Surface title="Следующее действие">
          <p>{nextStep ? nextStep.title : "Основные артефакты записаны. Проверьте выпуск и входящие лиды."}</p>
          <div className="row"><Link className="btn" to={nextStep?.href || "/leads"}>{nextStep ? "Перейти к этапу" : "Открыть лиды"}</Link><Link className="btn btn-ghost" to={`/projects/${project.id}/activity`}>Открыть историю действий</Link></div>
        </Surface>
      </>}
    </ProjectWorkspaceLayout>
  );
}
