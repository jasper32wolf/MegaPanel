import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Project = {
  id: string;
  name: string;
  slug: string;
  domain: string | null;
  niche: string | null;
  status: string;
  site_id: string | null;
  current_fact_revision_id: string | null;
};

export function ProjectsPage() {
  const { token } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [domain, setDomain] = useState("");
  const [niche, setNiche] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setProjects(await api<Project[]>("/api/v1/projects", {}, token));
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить проекты"));
  }, [token]);

  function suggestedSlug(value: string) {
    const candidate = value
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");
    return candidate || "";
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<Project>(
        "/api/v1/projects",
        { method: "POST", body: JSON.stringify({ name, slug, domain: domain || null, niche: niche || null }) },
        token,
      );
      setName("");
      setSlug("");
      setDomain("");
      setNiche("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать проект");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Проекты" description="Рабочая область оператора: факты → семантика и география → план страниц → черновик и проверка качества. Создание проекта не публикует сайт." />
      {error && <p className="error" role="alert">{error}</p>}
      <Surface title="Новый проект">
        <form className="stack" onSubmit={create}>
          <label className="field">Название проекта<input value={name} onChange={(event) => { setName(event.target.value); if (!slug) setSlug(suggestedSlug(event.target.value)); }} required /></label>
          <label className="field">Идентификатор проекта<input value={slug} onChange={(event) => setSlug(event.target.value.toLowerCase())} pattern="[a-z0-9-]+" required /></label>
          <label className="field">Домен<input value={domain} onChange={(event) => setDomain(event.target.value.toLowerCase())} placeholder="example.ru" /></label>
          <label className="field">Ниша<input value={niche} onChange={(event) => setNiche(event.target.value)} placeholder="Например: ремонт" /></label>
          <button className="btn" type="submit" disabled={busy || !name.trim() || !slug.trim()}>{busy ? "Создание…" : "Создать проект"}</button>
        </form>
      </Surface>
      <Surface title="Рабочие проекты">
        {projects.length === 0 ? <EmptyState title="Проектов пока нет" hint="Создайте проект и подтвердите его исходные данные перед созданием страниц." /> : <div className="stack">{projects.map((project) => <article className="detail-grid" key={project.id}><div><strong>{project.name}</strong><p className="muted">{project.domain || "Домен не указан"}</p></div><div><StatusPill tone={project.current_fact_revision_id ? "ok" : "warn"}>{project.current_fact_revision_id ? "факты подтверждены" : "нужны факты"}</StatusPill></div><div><Link className="btn btn-ghost" to={`/projects/${project.id}`}>Открыть проект</Link></div></article>)}</div>}
      </Surface>
    </div>
  );
}
