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
    <PageHeader title="Городские проекты" description="Каждый поддомен создаётся как самостоятельный проект с отдельным primary city и черновиком коммерческих facts. Это не alias домена и не публикация." />
    {error && <p className="error" role="alert">{error}</p>}
    <Surface title="Создать городский проект">
      <form className="stack" onSubmit={create}>
        <label className="field">Подтверждённый город<select value={cityId} onChange={(event) => setCityId(event.target.value)} required><option value="">Выберите город</option>{cities.map((city) => <option key={city.id} value={city.id}>{city.name}</option>)}</select></label>
        <label className="field">Название проекта<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Ремонт телевизоров в Уфе" required /></label>
        <label className="field">Slug проекта<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="repair-ufa" pattern="[a-z0-9-]+" required /></label>
        <label className="field">Hostname поддомена<input value={hostname} onChange={(event) => setHostname(event.target.value)} placeholder="ufa.example.ru" required /></label>
        <p className="muted">Будет скопирована только публичная confirmed revision master-проекта как черновик. До генерации подтвердите или замените адрес, телефон, contacts и коммерческие сведения в Facts нового проекта.</p>
        <button className="btn" type="submit" disabled={busy || !cityId || !name || !slug || !hostname}>{busy ? "Создание…" : "Создать черновик городского проекта"}</button>
      </form>
    </Surface>
    <Surface title="Созданные городские проекты">
      <p className="muted">Readiness вычисляется сервером только из сохранённого состояния. Здесь нельзя синхронизировать facts, настраивать private recipient или публиковать child project.</p>
      {items.length === 0 ? <EmptyState title="Городских проектов пока нет" hint="Создайте отдельный проект для каждого поддомена, которому нужны свои коммерческие данные и независимый workflow публикации." /> : <DataTable headers={["Городской проект", "Facts и приватный маршрут", "Готовность", "Следующий шаг"]}>{items.map((item) => {
        const state = readiness[item.child_project.id];
        const diff = state?.public_fact_diff;
        return <tr key={item.id}><td><strong>{item.child_project.name}</strong><br /><span className="muted">{item.child_project.slug} · {item.hostname}</span></td><td>{state ? <><StatusPill tone={state.facts_state === "confirmed" ? "ok" : "warn"}>facts: {state.facts_state || "missing"}</StatusPill><br /><span className="muted">Изменено: {diff?.changed.length || 0}; отсутствует: {diff?.missing.length || 0}</span><br /><StatusPill tone={state.private_recipient_configured ? "ok" : "warn"}>private recipient: {state.private_recipient_configured ? "configured" : "not configured"}</StatusPill></> : <span className="muted">Загрузка readiness…</span>}</td><td>{state ? <><StatusPill tone={state.primary_geo_ready ? "ok" : "warn"}>primary city: {state.primary_geo_ready ? "ready" : "missing"}</StatusPill><br /><span className="muted">keywords: {state.keyword_count}; plans: draft {state.page_plans_by_state.draft || 0}, review {state.page_plans_by_state.review || 0}, approved {state.page_plans_by_state.approved || 0}; site: {state.site_exists ? "yes" : "no"}</span></> : "—"}</td><td><strong>{state?.next_action || "loading"}</strong><br /><Link className="btn btn-ghost" to={`/projects/${item.child_project.id}`}>Открыть child workspace</Link><Link className="btn btn-ghost" to={`/projects/${item.child_project.id}/facts`}>Facts</Link><Link className="btn btn-ghost" to={`/projects/${item.child_project.id}/pages`}>Pages</Link></td></tr>;
      })}</DataTable>}
    </Surface>
  </ProjectWorkspaceLayout>;
}
