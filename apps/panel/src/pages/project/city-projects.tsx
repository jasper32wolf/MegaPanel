import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type City = { id: string; name: string; is_validated: boolean };
type Project = { id: string; name: string; slug: string; domain: string | null };
type CityProject = {
  id: string;
  geo_id: string;
  hostname: string;
  child_project: Project;
  draft_fact_revision_id: string | null;
};
type CityReadiness = {
  project_family_member_id: string;
  child_project_id: string;
  child_project_name: string;
  child_project_slug: string;
  hostname: string;
  facts_state: string | null;
  facts_version: number | null;
  public_fact_diff: { changed: string[]; missing: string[]; additional: string[] };
  private_recipient_configured: boolean;
  keyword_count: number;
  primary_geo_ready: boolean;
  page_plans_by_state: Record<string, number>;
  site_exists: boolean;
  next_action: string;
};

type CityReadinessMap = Record<string, CityReadiness>;

function readinessStateLabel(state: string | null) {
  return ({ draft: "черновик", review: "на проверке", confirmed: "подтверждены", missing: "нет" } as Record<string, string>)[state || "missing"] || state || "нет";
}

function booleanLabel(value: boolean, ready: string, missing: string) {
  return value ? ready : missing;
}

export function ProjectCityProjectsPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [cities, setCities] = useState<City[]>([]);
  const [items, setItems] = useState<CityProject[]>([]);
  const [readiness, setReadiness] = useState<CityReadinessMap>({});
  const [cityId, setCityId] = useState("");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [hostname, setHostname] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!projectId) return;
    const [nextCities, nextItems, nextReadiness] = await Promise.all([
      api<City[]>("/api/v1/geo?kind=city&limit=200", {}, token),
      api<CityProject[]>(`/api/v1/projects/${projectId}/city-projects`, {}, token),
      api<CityReadiness[]>(`/api/v1/projects/${projectId}/city-projects/readiness`, {}, token),
    ]);
    setCities(nextCities.filter((city) => city.is_validated));
    setItems(nextItems);
    setReadiness(Object.fromEntries(nextReadiness.map((item) => [item.child_project_id, item])));
  }

  useEffect(() => {
    void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить городские проекты"));
  }, [projectId, token]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!projectId || !cityId) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/projects/${projectId}/city-projects`, {
        method: "POST",
        body: JSON.stringify({ geo_id: cityId, name, slug, hostname }),
      }, token);
      setCityId("");
      setName("");
      setSlug("");
      setHostname("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Городской проект не создан");
    } finally {
      setBusy(false);
    }
  }

  return <ProjectWorkspaceLayout projectId={projectId}>
    <PageHeader title="Городские проекты" description="Каждый поддомен создаётся как самостоятельный проект с отдельным основным городом и черновиком коммерческих фактов. Это не псевдоним домена и не публикация." />
    {error && <p className="error" role="alert">{error}</p>}
    <Surface title="Создать городский проект">
      <form className="stack" onSubmit={create}>
        <label className="field">Подтверждённый город<select value={cityId} onChange={(event) => setCityId(event.target.value)} required><option value="">Выберите город</option>{cities.map((city) => <option key={city.id} value={city.id}>{city.name}</option>)}</select></label>
        <label className="field">Название проекта<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Ремонт телевизоров в Уфе" required /></label>
        <label className="field">Slug проекта<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="repair-ufa" pattern="[a-z0-9-]+" required /></label>
        <label className="field">Hostname поддомена<input value={hostname} onChange={(event) => setHostname(event.target.value)} placeholder="ufa.example.ru" required /></label>
        <p className="muted">В черновик будет скопирована только публичная подтверждённая версия основного проекта. До генерации подтвердите или замените адрес, телефон, контакты и коммерческие сведения в разделе «Факты» нового проекта.</p>
        <button className="btn" type="submit" disabled={busy || !cityId || !name || !slug || !hostname}>{busy ? "Создание…" : "Создать черновик городского проекта"}</button>
      </form>
    </Surface>
    <Surface title="Созданные городские проекты">
      <p className="muted">Готовность рассчитывается сервером только по сохранённым данным. Здесь нельзя синхронизировать факты, настроить приватного получателя или опубликовать дочерний проект.</p>
      {items.length === 0 ? <EmptyState title="Городских проектов пока нет" hint="Создайте отдельный проект для каждого поддомена, которому нужны свои коммерческие данные и независимый процесс публикации." /> : <DataTable headers={["Городской проект", "Факты и приватный маршрут", "Готовность", "Следующий шаг"]}>{items.map((item) => {
        const state = readiness[item.child_project.id];
        const diff = state?.public_fact_diff;
        return <tr key={item.id}><td><strong>{item.child_project.name}</strong><br /><span className="muted">{item.child_project.slug} · {item.hostname}</span></td><td>{state ? <><StatusPill tone={state.facts_state === "confirmed" ? "ok" : "warn"}>факты: {readinessStateLabel(state.facts_state)}</StatusPill><br /><span className="muted">Изменено: {diff?.changed.length || 0}; отсутствует: {diff?.missing.length || 0}</span><br /><StatusPill tone={state.private_recipient_configured ? "ok" : "warn"}>приватный получатель: {booleanLabel(state.private_recipient_configured, "настроен", "не настроен")}</StatusPill></> : <span className="muted">Загрузка данных о готовности…</span>}</td><td>{state ? <><StatusPill tone={state.primary_geo_ready ? "ok" : "warn"}>основной город: {booleanLabel(state.primary_geo_ready, "готов", "не указан")}</StatusPill><br /><span className="muted">ключевые фразы: {state.keyword_count}; планы: черновики — {state.page_plans_by_state.draft || 0}, на проверке — {state.page_plans_by_state.review || 0}, одобрены — {state.page_plans_by_state.approved || 0}; сайт: {booleanLabel(state.site_exists, "создан", "не создан")}</span></> : "—"}</td><td><strong>{state?.next_action || "Загрузка…"}</strong><br /><Link className="btn btn-ghost" to={`/projects/${item.child_project.id}`}>Открыть рабочее пространство проекта</Link><Link className="btn btn-ghost" to={`/projects/${item.child_project.id}/facts`}>Факты</Link><Link className="btn btn-ghost" to={`/projects/${item.child_project.id}/pages`}>Страницы</Link></td></tr>;
      })}</DataTable>}
    </Surface>
  </ProjectWorkspaceLayout>;
}
