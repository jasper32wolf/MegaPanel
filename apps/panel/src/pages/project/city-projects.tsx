import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
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

export function ProjectCityProjectsPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [cities, setCities] = useState<City[]>([]);
  const [items, setItems] = useState<CityProject[]>([]);
  const [cityId, setCityId] = useState("");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [hostname, setHostname] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!projectId) return;
    const [nextCities, nextItems] = await Promise.all([
      api<City[]>("/api/v1/geo?kind=city&limit=200", {}, token),
      api<CityProject[]>(`/api/v1/projects/${projectId}/city-projects`, {}, token),
    ]);
    setCities(nextCities.filter((city) => city.is_validated));
    setItems(nextItems);
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
      {items.length === 0 ? <EmptyState title="Городских проектов пока нет" hint="Создайте отдельный проект для каждого поддомена, которому нужны свои коммерческие данные и независимый workflow публикации." /> : <DataTable headers={["Городской проект", "Hostname", "Facts", "Следующий шаг"]}>{items.map((item) => <tr key={item.id}><td><strong>{item.child_project.name}</strong><br /><span className="muted">{item.child_project.slug}</span></td><td>{item.hostname}</td><td>{item.draft_fact_revision_id ? <><StatusPill tone="warn">draft</StatusPill><br /><span className="muted">{item.draft_fact_revision_id.slice(0, 8)}…</span></> : <><StatusPill tone="ok">confirmed / no draft</StatusPill><br /><span className="muted">Городской проект остаётся в family.</span></>}</td><td>{item.draft_fact_revision_id ? "Откройте проект → Facts, заполните city-specific данные и подтвердите revision." : "Откройте проект → Pages или Structure; child готов к независимому следующему шагу."}</td></tr>)}</DataTable>}
    </Surface>
  </ProjectWorkspaceLayout>;
}
