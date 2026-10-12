import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { name: string };
type Plan = { plan_id: string; slug: string; state: string };
type CoverageRow = {
  collection_keyword_id: string;
  project_keyword_id: string;
  keyword_id: string;
  geo_binding_id: string | null;
  status: "covered" | "planned" | "uncovered" | "unbound";
  plans: Plan[];
};
type Signals = {
  totals: { members: number; bindings: number; covered: number; planned: number; uncovered: number; unbound: number };
  coverage: CoverageRow[];
  collisions: { target: { collection_keyword_id: string; geo_binding_id: string }; plans: Plan[]; reason: string }[];
  unmapped_plans: Plan[];
  policy: { mode: string; read_only: boolean; blocks_candidate: boolean; basis: string };
};

const coverageLabels: Record<CoverageRow["status"], string> = {
  covered: "Покрыто",
  planned: "Запланировано",
  uncovered: "Не покрыто",
  unbound: "Не привязано",
};

function statusTone(status: CoverageRow["status"]) {
  if (status === "covered") return "ok" as const;
  if (status === "uncovered" || status === "unbound") return "warn" as const;
  return "accent" as const;
}

export function SemanticCoveragePage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [signals, setSignals] = useState<Signals | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<Signals>(`/api/v1/projects/${projectId}/semantic-signals`, {}, token),
    ]).then(([nextProject, nextSignals]) => {
      setProject(nextProject);
      setSignals(nextSignals);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить покрытие семантики"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `${project.name}: покрытие семантики` : "Покрытие семантики"}
        description="Справочный обзор привязок одобренных семантических коллекций и сохранённых снимков планов страниц."
        actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть рабочую область</Link>}
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!signals && !error ? <p className="muted" aria-live="polite">Загрузка покрытия семантики…</p> : null}
      {signals && <>
        <Surface title="Справочная политика">
          <p className="muted">Этот обзор только информирует: он не создаёт черновики, не применяет изменения, не запускает сборку и не публикует сайт. Непокрытые, непривязанные и конфликтующие элементы не блокируют процесс пробной сборки.</p>
          <div className="detail-grid">
            <div><strong>{signals.totals.covered}</strong><span className="muted"> покрыто одобренным планом</span></div>
            <div><strong>{signals.totals.planned}</strong><span className="muted"> запланировано (черновик/проверка)</span></div>
            <div><strong>{signals.totals.uncovered}</strong><span className="muted"> не покрыто</span></div>
            <div><strong>{signals.totals.unbound}</strong><span className="muted"> не привязано</span></div>
            <div><strong>{signals.collisions.length}</strong><span className="muted"> предупреждений о конфликтах</span></div>
          </div>
        </Surface>
        <Surface title="Покрытие по одобренным семантическим привязкам">
          {signals.coverage.length === 0 ? <EmptyState title="Нет одобренной семантической коллекции" hint="Создайте и одобрите коллекцию в основной рабочей области; этот экран ничего не создаёт." /> : <DataTable headers={["Элемент коллекции", "Географическая привязка", "Сигнал", "Связанный план страницы"]}>{signals.coverage.map((item) => <tr key={`${item.collection_keyword_id}:${item.geo_binding_id || "unbound"}`}><td><code>{item.collection_keyword_id.slice(0, 8)}</code></td><td>{item.geo_binding_id ? <code>{item.geo_binding_id.slice(0, 8)}</code> : "Нет географической привязки"}</td><td><StatusPill tone={statusTone(item.status)}>{coverageLabels[item.status]}</StatusPill></td><td>{item.plans.length ? item.plans.map((plan) => `${plan.slug} (${plan.state})`).join(", ") : "—"}</td></tr>)}</DataTable>}
        </Surface>
        <Surface title="Справочные замечания">
          {signals.collisions.length === 0 && signals.unmapped_plans.length === 0 ? <EmptyState title="Нет дополнительных сигналов" hint="Это не означает готовность к публикации: обычные условия пробной сборки остаются отдельными." /> : <div className="stack">
            {signals.collisions.map((collision) => <p className="muted" key={`${collision.target.collection_keyword_id}:${collision.target.geo_binding_id}`}>Конфликт: {collision.reason}. Планы: {collision.plans.map((plan) => `${plan.slug} (${plan.state})`).join(", ")}.</p>)}
            {signals.unmapped_plans.length > 0 && <p className="muted">Непривязанные планы: {signals.unmapped_plans.map((plan) => `${plan.slug} (${plan.state})`).join(", ")}. Они не учитываются в покрытии семантики.</p>}
          </div>}
        </Surface>
      </>}
    </ProjectWorkspaceLayout>
  );
}
